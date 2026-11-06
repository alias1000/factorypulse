"""v0.3.3 regression: evidence scanner health (Issue 1) and filesystem/database consistency (Issue 2).

Invariant under test: no uploaded evidence becomes accessible to ordinary users without a persisted clean
scan result or an explicitly authorized (persisted) release – whatever fails in between."""
import contextlib
import os
import time

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from tests.test_evidence_scanning import EICAR, PDF, FakeClamd, _install_scanner, _inv, _upload


def _att(att_id):
    from app.db import SessionLocal
    from app.models import InvestigationAttachment

    with SessionLocal() as db:
        a = db.get(InvestigationAttachment, att_id)
        db.expunge(a)
        return a


def _where(att_id):
    """(in quarantine, in evidence/) on disk."""
    from app.services import evidence_store as EStore

    a = _att(att_id)
    return EStore.quarantine_path(a).exists(), EStore.released_path(a).exists()


def _checks(c):
    return {x["name"]: x for x in c.get("/api/admin/system").json()["checks"]}


def _ops(att_id):
    from app.db import SessionLocal
    from app.models import EvidenceFileOp

    with SessionLocal() as db:
        return [(o.status, o.reason) for o in db.scalars(select(EvidenceFileOp).where(
            EvidenceFileOp.attachment_id == att_id).order_by(EvidenceFileOp.id))]


@contextlib.contextmanager
def failing_commit(predicate):
    """Make every database COMMIT for which ``predicate(session)`` is true fail (simulated DB failure)."""
    def listener(session):
        if predicate(session):
            raise RuntimeError("simulated database commit failure")
    event.listen(Session, "before_commit", listener)
    try:
        yield
    finally:
        event.remove(Session, "before_commit", listener)


def _objects(session):
    return list(session.new) + list(session.identity_map.values())


class Crash(BaseException):
    """Simulated process death (not an Exception: nothing in the application may catch it)."""


@pytest.fixture()
def broken_scanner(fake_scanner, monkeypatch, tmp_path):
    """FP_EVIDENCE_SCANNER=command with a command that does not exist (yet). ``install()`` repairs it
    under the SAME path – i.e. the same scanner, recovered."""
    path = tmp_path / "bin" / "clamdscan"
    monkeypatch.setattr(fake_scanner, "evidence_scan_command", str(path))

    class B:
        command = path

        @staticmethod
        def install():
            _install_scanner(path)
    return B


@pytest.fixture()
def no_backoff(monkeypatch):
    from app.services import jobs
    monkeypatch.setattr(jobs.time, "sleep", lambda s: None)


# ============================================================================ Issue 1: scanner health

def test_issue1_reproduction_warning_survives_zero_work_runs(client, demo, broken_scanner, no_backoff):
    """Exact reproduction from the review: a scanner command that does not exist, uploaded evidence, the
    scan job runs again and again (more often than FP_EVIDENCE_SCAN_MAX_ATTEMPTS) – the outage warning must
    stay until the scanner has verifiably recovered."""
    from app import worker
    from app.config import get_settings
    from app.db import SessionLocal
    from app.services import evidence_scan as ES

    inv = _inv(client)
    att = _upload(client, inv)
    assert att["scan_status"] == "pending" and att["quarantined"]
    for i in range(get_settings().evidence_scan_max_attempts + 3):
        assert worker.cycle() is True                          # an outage is an outcome, not a crash
        run = ES.run_scan_job(1)[0]
        assert run.status == "succeeded" and run.details["skipped_scanner_unavailable"] is True
        assert run.details["clean"] == run.details["failed"] == 0   # zero work …
        chk = _checks(client)["evidence_scanner"]
        assert chk["status"] == "warn" and "UNAVAILABLE" in chk["detail"], (i, chk)   # … warning stays
    with SessionLocal() as db:
        h = ES.health(db)
        assert h.status == "unavailable" and h.consecutive_failures >= get_settings().evidence_scan_max_attempts + 3
        assert h.unavailable_since is not None and "FileNotFoundError" in h.last_error
    a = _att(att["id"])
    assert a.scan_status == "pending" and a.scan_attempts == 0      # no attempts burnt by the outage
    assert _where(att["id"]) == (True, False)
    viewer_dl = client.get(f"/api/attachments/{att['id']}/download")
    assert viewer_dl.status_code == 409                              # admin needs acknowledgement; still unverified
    listed = {x["id"]: x for x in client.get(f"/api/investigations/{inv}/attachments").json()}
    assert listed[att["id"]]["retries_exhausted"] is False

    # verified recovery clears the warning and the backlog is processed in the same worker cycle
    broken_scanner.install()
    assert worker.cycle() is True
    chk = _checks(client)["evidence_scanner"]
    assert chk["status"] == "ok" and "healthy" in chk["detail"]
    assert _att(att["id"]).scan_status == "clean" and _where(att["id"]) == (False, True)
    log = client.get("/api/audit-log", params={"event": "evidence.scanner_recovered"}).json()
    assert log and log[0]["details"]["scanner"] == "command:clamdscan"
    assert client.get("/api/audit-log", params={"event": "evidence.scanner_unavailable"}).json()


def test_zero_work_scan_run_never_clears_an_outage(client, demo, broken_scanner):
    """Nothing pending at all (the scan run has literally zero files) – the outage persists."""
    from app.db import SessionLocal
    from app.services import evidence_scan as ES

    assert ES.run_health_job()[0].details["healthy"] is False
    with SessionLocal() as db:                 # demo reload: the company has no evidence at all
        n_pending = db.scalar(select(func.count()).select_from(ES.pending_query(1).subquery()))
    assert n_pending == 0
    for _ in range(3):
        run = ES.run_scan_job(1)[0]
        assert run.status == "succeeded" and run.details["clean"] == run.details["failed"] == 0
    assert _checks(client)["evidence_scanner"]["status"] == "warn"
    with SessionLocal() as db:
        assert ES.health(db).status == "unavailable"


def test_upload_during_outage_does_not_touch_the_scanner(client, demo, broken_scanner):
    from app.services import evidence_scan as ES

    ES.run_health_job()
    att = _upload(client, _inv(client))
    assert att["scan_status"] == "pending" and att["scan_detail"] is None and att["scan_attempts"] == 0
    r = client.post(f"/api/attachments/{att['id']}/rescan")
    assert r.status_code == 503 and r.json()["code"] == "scanner_unavailable"


def test_exhausted_retries_are_counted_and_reset_by_manual_rescan(client, as_role, demo, fake_scanner, monkeypatch):
    """Per-file scanner errors (the scanner works, but cannot scan THIS file) use up attempts; after
    FP_EVIDENCE_SCAN_MAX_ATTEMPTS the file stays quarantined and readiness shows how many are exhausted."""
    from app.services import evidence_scan as ES

    monkeypatch.setattr(fake_scanner, "evidence_scan_max_attempts", 2)
    inv = _inv(client)
    bad = _upload(client, inv, PDF + b"SCANNER-CRASH", "x.pdf")
    assert bad["scan_status"] == "failed" and bad["scan_attempts"] == 1 and not bad["retries_exhausted"]
    assert ES.run_health_job()[0].details["healthy"] is True
    ES.run_scan_job(1)
    ES.run_scan_job(1)
    listed = {x["id"]: x for x in client.get(f"/api/investigations/{inv}/attachments").json()}
    assert listed[bad["id"]]["retries_exhausted"] is True and listed[bad["id"]]["scan_attempts"] == 2
    q = _checks(client)["evidence_quarantine"]
    assert q["status"] == "warn" and "retries exhausted" in q["detail"]
    import re
    assert int(re.search(r"retries exhausted (\d+)", q["detail"]).group(1)) >= 1
    status = client.get("/api/admin/evidence/status").json()
    assert status["quarantine"]["retries_exhausted"] >= 1 and status["quarantine"]["max_attempts"] == 2
    assert as_role("analyst").get(f"/api/attachments/{bad['id']}/download").json()["code"] == "quarantined"
    # an administrator rescan starts a fresh set of attempts
    r = client.post(f"/api/attachments/{bad['id']}/rescan").json()
    assert r["scan_status"] == "failed" and r["scan_attempts"] == 1 and r["retries_exhausted"] is False
    assert client.get("/api/audit-log", params={"event": "evidence.rescanned"}).json()


def test_health_check_command_requires_detection(client, demo, fake_scanner, tmp_path, monkeypatch):
    import sys

    from app.services import evidence_scan as ES

    r = ES.check_health()
    assert r.ok and "EICAR detected" in r.detail and r.scanner == f"command:{os.path.basename(sys.executable)}"
    blind = tmp_path / "blind.py"                         # a "scanner" that reports everything clean
    blind.write_text("import sys; print('OK'); sys.exit(0)\n")
    monkeypatch.setattr(fake_scanner, "evidence_scan_command", f"{sys.executable} {blind}")
    r = ES.check_health()
    assert not r.ok and "EICAR test file not detected" in r.detail
    monkeypatch.setattr(fake_scanner, "evidence_scanner_selftest_eicar", False)
    assert ES.check_health().ok
    assert not list((tmp_path).glob("selftest-*"))
    from app.services import evidence_store as EStore
    assert not list(EStore.staging_dir().glob("selftest-*"))  # self-test files are removed


def test_health_check_clamd_ping_version_and_eicar(client, demo, monkeypatch):
    from app.config import get_settings
    from app.db import SessionLocal
    from app.services import evidence_scan as ES

    srv = FakeClamd()
    s = get_settings()
    monkeypatch.setattr(s, "evidence_scanner", "clamd")
    monkeypatch.setattr(s, "clamd_host", "127.0.0.1")
    monkeypatch.setattr(s, "clamd_port", srv.port)
    try:
        r = ES.check_health()
        assert r.ok and "FakeClamd" in r.detail
        assert srv.commands[:2] == [b"zPING\0", b"zVERSION\0"]
        assert any(b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE" in d for d in srv.received)
        ES.run_health_job()
        with SessionLocal() as db:
            assert ES.health(db).status == "healthy" and ES.health(db).scanner == f"clamd@127.0.0.1:{srv.port}"
    finally:
        srv.close()
    # daemon gone → unavailable, upload is not scanned and stays quarantined
    assert ES.check_health().ok is False
    ES.run_health_job()
    chk = _checks(client)["evidence_scanner"]
    assert chk["status"] == "warn" and "scanner unreachable" in chk["detail"]
    att = _upload(client, _inv(client))
    assert att["scan_status"] == "pending" and att["quarantined"]


def test_health_staleness_is_reported(as_role, demo, fake_scanner, frozen_clock):
    import datetime as dt

    from app.services import evidence_scan as ES

    ES.run_health_job()
    assert _checks(as_role("admin"))["evidence_scanner"]["status"] == "ok"
    frozen_clock.advance(seconds=3 * 300 + 60)
    chk = _checks(as_role("admin"))["evidence_scanner"]
    assert chk["status"] == "warn" and "older than 3 intervals" in chk["detail"]


def test_admin_scanner_check_and_reconcile_endpoints(client, as_role, demo, broken_scanner):
    for role in ("analyst", "viewer"):
        c = as_role(role)
        assert c.get("/api/admin/evidence/status").status_code == 403
        assert c.post("/api/admin/evidence/scanner/check").status_code == 403
        assert c.post("/api/admin/evidence/reconcile").status_code == 403
    att = _upload(client, _inv(client))
    r = client.post("/api/admin/evidence/scanner/check").json()
    assert r["health"]["status"] == "unavailable" and r["scan_run"] is None
    broken_scanner.install()
    r = client.post("/api/admin/evidence/scanner/check").json()
    assert r["health"]["status"] == "healthy" and r["scan_run"]["details"]["clean"] >= 1
    assert _att(att["id"]).scan_status == "clean" and _where(att["id"]) == (False, True)
    r = client.post("/api/admin/evidence/reconcile")
    assert r.status_code == 200 and r.json()["run"]["status"] == "succeeded"
    assert client.get("/api/audit-log", params={"event": "evidence.scanner_checked"}).json()
    assert client.get("/api/audit-log", params={"event": "evidence.reconciled"}).json()


# ============================================================================ Issue 2: consistency

def test_upload_commit_failure_leaves_no_file_and_no_row(client, demo):
    from app.models import AuditLog
    from app.services import evidence_store as EStore

    inv = _inv(client)
    before = set(os.listdir(EStore.quarantine_dir()))
    marker = PDF + b"commit-failure-" + os.urandom(8).hex().encode()
    pred = lambda s: any(isinstance(o, AuditLog) and o.event == "investigation.attachment_added" for o in s.new)  # noqa: E731
    with failing_commit(pred):
        try:
            r = client.post(f"/api/investigations/{inv}/attachments", files={"file": ("x.pdf", marker)})
            assert r.status_code == 500
        except RuntimeError as exc:
            assert "simulated database commit failure" in str(exc)
    assert set(os.listdir(EStore.quarantine_dir())) == before        # file removed again
    assert not [p for p in EStore.staging_dir().glob("*.part")]
    assert client.get(f"/api/investigations/{inv}/attachments").json() == []


def test_release_commit_failure_moves_nothing_and_grants_nothing(client, as_role, demo):
    from app.models import AuditLog

    att = _upload(as_role("analyst"), _inv(client))                   # no scanner: not_scanned
    pred = lambda s: any(isinstance(o, AuditLog) and o.event == "evidence.released" for o in s.new)  # noqa: E731
    with failing_commit(pred):
        try:
            r = client.post(f"/api/attachments/{att['id']}/release", json={"note": "checked on isolated PC"})
            assert r.status_code == 500
        except RuntimeError:
            pass
    a = _att(att["id"])
    assert a.released_at is None and _ops(att["id"]) == []
    assert _where(att["id"]) == (True, False)                         # the file did not move
    assert as_role("viewer").get(f"/api/attachments/{att['id']}/download").json()["code"] == "quarantined"


def test_scan_job_commit_failure_rolls_back_result_and_file_stays(client, as_role, demo, fake_scanner, monkeypatch):
    from app.config import get_settings
    from app.models import EvidenceFileOp
    from app.services import evidence_scan as ES

    monkeypatch.setattr(get_settings(), "evidence_scanner", "none")
    att = _upload(client, _inv(client))                               # backlog
    monkeypatch.setattr(get_settings(), "evidence_scanner", "command")
    ES.run_health_job()
    with failing_commit(lambda s: any(isinstance(o, EvidenceFileOp) for o in _objects(s))):
        run = ES.run_scan_job(1, max_attempts=1)[0]
    assert run.status == "failed" and "simulated database commit failure" in run.error
    a = _att(att["id"])
    assert a.scan_status == "not_scanned" and _ops(att["id"]) == [] and _where(att["id"]) == (True, False)
    assert as_role("viewer").get(f"/api/attachments/{att['id']}/download").json()["code"] == "quarantined"
    # next run (database back): clean, file placed by the reconciliation job
    assert ES.run_scan_job(1)[0].status == "succeeded"
    from app.services import evidence_store as EStore
    EStore.run_reconcile_job()
    assert _att(att["id"]).scan_status == "clean" and _where(att["id"]) == (False, True)


def test_worker_crash_during_scan_changes_nothing(client, as_role, demo, fake_scanner, monkeypatch):
    from app.config import get_settings
    from app.db import SessionLocal
    from app.models import JobRun
    from app.services import evidence_scan as ES

    monkeypatch.setattr(get_settings(), "evidence_scanner", "none")
    inv = _inv(client)
    first, second = _upload(client, inv), _upload(client, inv, PDF + b"2", "b.pdf")
    monkeypatch.setattr(get_settings(), "evidence_scanner", "command")
    ES.run_health_job()
    real, calls = ES.scan_file, []

    def dying_scan(path):
        calls.append(path)
        if len(calls) == 2:
            raise Crash("worker killed while scanning")
        return real(path)
    monkeypatch.setattr(ES, "scan_file", dying_scan)
    with pytest.raises(Crash):
        ES.run_scan_job(1)
    monkeypatch.setattr(ES, "scan_file", real)
    for x in (first, second):                                         # the first result was never committed
        assert _att(x["id"]).scan_status == "not_scanned" and _where(x["id"]) == (True, False) and _ops(x["id"]) == []
    with SessionLocal() as db:
        assert db.scalar(select(JobRun.status).where(JobRun.job == ES.JOB).order_by(JobRun.id.desc())) == "running"
    assert as_role("viewer").get(f"/api/attachments/{first['id']}/download").json()["code"] == "quarantined"
    assert ES.run_scan_job(1)[0].status == "succeeded"                # recovery after the interruption
    assert _att(first["id"]).scan_status == _att(second["id"]).scan_status == "clean"


def test_crash_after_commit_before_move_is_served_from_db_state_and_reconciled(client, as_role, demo, fake_scanner, monkeypatch):
    """Commit succeeded, process died before the file move: access follows the persisted result, the file
    is placed by the reconciliation job."""
    from app.config import get_settings
    from app.services import evidence_scan as ES
    from app.services import evidence_store as EStore

    monkeypatch.setattr(get_settings(), "evidence_scanner", "none")
    att = _upload(client, _inv(client))
    released = _upload(client, _inv(client), PDF + b"rel", "r.pdf")
    monkeypatch.setattr(get_settings(), "evidence_scanner", "command")
    ES.run_health_job()
    monkeypatch.setattr(EStore, "apply_now", lambda ids: None)        # "crash" right after the commit
    assert client.post(f"/api/attachments/{released['id']}/release", json={"note": "checked offline, ok"}).status_code == 200
    ES.run_scan_job(1)                                                # commits clean + queued move, no move yet
    for x in (att, released):
        assert _where(x["id"]) == (True, False) and _ops(x["id"])[-1][0] == "pending"
    assert _att(att["id"]).scan_status == "clean"
    viewer = as_role("viewer")
    assert viewer.get(f"/api/attachments/{att['id']}/download").content == PDF   # persisted clean result
    assert viewer.get(f"/api/attachments/{released['id']}/download").content == PDF + b"rel"
    st = EStore.run_reconcile_job()[0]
    assert st.status == "succeeded" and st.details["ops_done"] >= 2
    for x in (att, released):
        assert _where(x["id"]) == (False, True) and _ops(x["id"])[-1][0] == "done"
    assert EStore.run_reconcile_job()[0].details["ops_done"] == 0     # idempotent


def test_file_move_failure_is_retried_and_reported(client, as_role, demo, monkeypatch):
    from app.services import evidence_store as EStore

    att = _upload(client, _inv(client))
    real_replace = os.replace

    def broken_replace(src, dst, *a, **k):
        if str(EStore.evidence_dir()) == os.path.dirname(str(dst)):
            raise OSError(28, "No space left on device (simulated)")
        return real_replace(src, dst, *a, **k)
    monkeypatch.setattr(EStore.os, "replace", broken_replace)
    r = client.post(f"/api/attachments/{att['id']}/release", json={"note": "checked with two scanners"})
    assert r.status_code == 200 and r.json()["quarantined"] is False   # the release is committed …
    assert _ops(att["id"]) == [("failed", "released")] and _where(att["id"]) == (True, False)   # … the move failed
    chk = _checks(client)["evidence_storage"]
    assert chk["status"] == "warn" and "ops failed 1" in chk["detail"]
    assert as_role("viewer").get(f"/api/attachments/{att['id']}/download").content == PDF
    assert EStore.run_reconcile_job()[0].details["ops_failed"] == 1   # still broken: stays failed, retried
    monkeypatch.setattr(EStore.os, "replace", real_replace)
    assert EStore.run_reconcile_job()[0].details["ops_done"] == 1
    assert _ops(att["id"])[-1][0] == "done" and _where(att["id"]) == (False, True)
    assert _checks(client)["evidence_storage"]["status"] == "ok"


def test_unverified_file_in_released_directory_is_not_served(client, as_role, demo):
    """Directory placement never grants access (e.g. a file copied into evidence/ by hand, or a move
    that happened before a failed commit in an older version)."""
    import shutil

    from app.services import evidence_store as EStore

    att = _upload(client, _inv(client))
    a = _att(att["id"])
    shutil.move(EStore.quarantine_path(a), EStore.released_path(a))
    for role in ("viewer", "analyst"):
        assert as_role(role).get(f"/api/attachments/{att['id']}/download").json()["code"] == "quarantined"
    assert EStore.run_reconcile_job()[0].details["misplaced_fixed"] >= 1
    assert _where(att["id"]) == (True, False)


def test_tampered_file_is_never_served(client, as_role, demo, fake_scanner):
    from app.services import evidence_store as EStore

    att = _upload(client, _inv(client))
    assert att["scan_status"] == "clean"
    p = EStore.released_path(_att(att["id"]))
    original = p.read_bytes()
    p.write_bytes(original + b"appended later")
    try:
        for c in (as_role("viewer"), client):
            r = c.get(f"/api/attachments/{att['id']}/download")
            assert r.status_code == 409 and r.json()["code"] == "integrity"
    finally:
        p.write_bytes(original)
    assert as_role("viewer").get(f"/api/attachments/{att['id']}/download").content == PDF


def test_orphans_and_stale_staging_files_are_cleaned_up(client, demo):
    from app.services import evidence_store as EStore

    EStore.write_upload("orphan-old.pdf", PDF)                        # crash between file write and commit
    EStore.write_upload("orphan-new.pdf", PDF)                        # upload whose commit is still in flight
    part = EStore.staging_dir() / "dead.pdf.part"
    part.write_bytes(b"partial")
    old = time.time() - 7200
    os.utime(EStore.quarantine_dir() / "orphan-old.pdf", (old, old))
    os.utime(part, (old, old))
    try:
        d = EStore.run_reconcile_job()[0].details
        assert d["orphans_moved"] == 1 and d["stale_parts_removed"] == 1
        assert (EStore.orphan_dir() / "orphan-old.pdf").exists() and not part.exists()
        assert (EStore.quarantine_dir() / "orphan-new.pdf").exists()    # inside the grace period: untouched
        st = client.get("/api/admin/evidence/status").json()["storage"]
        assert st["orphaned"] >= 1
    finally:
        for f in (EStore.orphan_dir() / "orphan-old.pdf", EStore.quarantine_dir() / "orphan-new.pdf"):
            f.unlink(missing_ok=True)


def test_migration_0005_resets_outage_failures_only(client, demo):
    """Data fix for installations upgraded from v0.3.1/0.3.2: files that were marked failed only because the
    scanner was unreachable get their attempts back (they stay quarantined); real scan errors are kept."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from app.db import engine
    from tests.conftest import BACKEND

    inv = _inv(client)
    outage = _upload(client, inv)
    real_err = _upload(client, inv, PDF + b"e", "e.pdf")
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    engine.dispose()
    command.downgrade(cfg, "0004")
    try:
        with engine.begin() as c:
            c.execute(text("UPDATE investigation_attachments SET scan_status='failed', scan_attempts=5, "
                           "scan_detail='FileNotFoundError: [Errno 2] No such file or directory' WHERE id=:i"),
                      {"i": outage["id"]})
            c.execute(text("UPDATE investigation_attachments SET scan_status='failed', scan_attempts=5, "
                           "scan_detail='exit 2: scanner internal error' WHERE id=:i"), {"i": real_err["id"]})
    finally:
        command.upgrade(cfg, "head")
        engine.dispose()
    a, b = _att(outage["id"]), _att(real_err["id"])
    assert a.scan_status == "pending" and a.scan_attempts == 0 and a.scan_detail.startswith("[reset by migration 0005")
    assert b.scan_status == "failed" and b.scan_attempts == 5
    assert a.released_at is None and _where(outage["id"]) == (True, False)


def test_invariant_no_unverified_download_for_ordinary_users(as_role, demo, fake_scanner):
    """Across everything the test database holds (all scenarios above, in any order): an ordinary user
    can download a file ONLY if the database holds a clean result or a release."""
    from app.db import SessionLocal
    from app.models import InvestigationAttachment
    from app.services import evidence_scan as ES

    inv = _inv(as_role("admin"))
    _upload(as_role("analyst"), inv, EICAR, "e.pdf")
    _upload(as_role("analyst"), inv, PDF + b"SCANNER-CRASH", "c.pdf")
    _upload(as_role("analyst"), inv)                                     # clean
    viewer = as_role("viewer")
    with SessionLocal() as db:
        rows = db.scalars(select(InvestigationAttachment).where(InvestigationAttachment.company_id == 1,
                                                                 InvestigationAttachment.deleted_at.is_(None))).all()
        verified = {a.id: ES.is_verified(a) for a in rows}
    assert len(verified) >= 3 and any(verified.values()) and not all(verified.values())
    for att_id, ok in verified.items():
        r = viewer.get(f"/api/attachments/{att_id}/download")
        if not ok:
            assert r.status_code == 403, (att_id, r.status_code)
        else:
            assert r.status_code == 200, (att_id, r.status_code)


def test_restore_reconciles_evidence_files(app, client, as_role, demo, monkeypatch):
    """A backup taken while a file move was still queued (released, process died before the move): after
    the restore, reconciliation places the file according to the restored database state."""
    from app.db import engine
    if engine.dialect.name != "postgresql":
        pytest.skip("backups require PostgreSQL")
    from app.services import evidence_store as EStore
    from tests.conftest import TEST_PASSWORD, email_for

    att = _upload(client, _inv(client))
    monkeypatch.setattr(EStore, "apply_now", lambda ids: None)
    client.post(f"/api/attachments/{att['id']}/release", json={"note": "checked before the backup"}).raise_for_status()
    assert _where(att["id"]) == (True, False) and _ops(att["id"])[-1][0] == "pending"
    name = client.post("/api/admin/backups", json={"note": "pending move"}).json()["name"]
    r = client.post(f"/api/admin/backups/{name}/restore", json={"password": TEST_PASSWORD, "confirm": "RESTORE"})
    assert r.status_code == 200, r.text
    rec = r.json()["evidence_reconciliation"]
    assert rec["ops_done"] >= 1 and rec["missing"] == 0 and rec["integrity_errors"] == 0
    me = client.post("/api/auth/login", json={"email": email_for("admin"), "password": TEST_PASSWORD}).json()
    client.headers["X-CSRF-Token"] = me["csrf_token"]
    assert _where(att["id"]) == (False, True) and _ops(att["id"])[-1][0] == "done"
    assert as_role("viewer").get(f"/api/attachments/{att['id']}/download").content == PDF
