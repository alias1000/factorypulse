"""v0.3.1 regression: malware-scanner integration, quarantine and download restrictions for evidence."""
import socket
import struct
import threading

import pytest


PDF = b"%PDF-1.4\n%evidence\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
EICAR = PDF + b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


def _inv(client):
    meta = client.get("/api/meta").json()
    r = client.post("/api/investigations", json={"title": "Evidence scan test", "category": "other", "severity": "low",
                                                  "line_id": meta["lines"][0]["id"]})
    assert r.status_code == 201
    return r.json()["id"]


def _upload(c, inv_id, data=PDF, name="report.pdf"):
    r = c.post(f"/api/investigations/{inv_id}/attachments", files={"file": (name, data)})
    assert r.status_code == 201, r.text
    return r.json()


def _paths(att_id):
    from app.db import SessionLocal
    from app.models import InvestigationAttachment
    from app.services import evidence_scan as ES

    with SessionLocal() as db:
        a = db.get(InvestigationAttachment, att_id)
        return ES.quarantine_path(a).exists(), ES.released_path(a).exists()


def test_without_scanner_uploads_stay_quarantined_until_admin_release(client, as_role, demo):
    inv = _inv(client)
    analyst, viewer = as_role("analyst"), as_role("viewer")
    att = _upload(analyst, inv)
    assert att["scan_status"] == "not_scanned" and att["quarantined"] and not att["downloadable"]
    assert _paths(att["id"]) == (True, False)             # physically in the quarantine directory
    for c in (analyst, viewer):
        r = c.get(f"/api/attachments/{att['id']}/download")
        assert r.status_code == 403 and r.json()["code"] == "quarantined"
    # administrators: explicit risk acknowledgement, audited
    assert client.get(f"/api/attachments/{att['id']}/download").json()["code"] == "acknowledge_required"
    r = client.get(f"/api/attachments/{att['id']}/download", params={"acknowledge_risk": True})
    assert r.status_code == 200 and r.content == PDF
    assert client.get("/api/audit-log", params={"event": "evidence.unverified_download"}).json()
    listed = {a["id"]: a for a in client.get(f"/api/investigations/{inv}/attachments").json()}
    assert listed[att["id"]]["requires_acknowledgement"] and listed[att["id"]]["can_release"]
    # release: admin only, with a reason; afterwards available to everybody who may read the investigation
    assert analyst.post(f"/api/attachments/{att['id']}/release", json={"note": "checked with offline AV"}).status_code == 403
    assert client.post(f"/api/attachments/{att['id']}/release", json={"note": "short"}).status_code == 422
    rel = client.post(f"/api/attachments/{att['id']}/release", json={"note": "Checked on isolated PC with Defender"})
    assert rel.status_code == 200 and rel.json()["quarantined"] is False
    assert _paths(att["id"]) == (False, True)
    assert viewer.get(f"/api/attachments/{att['id']}/download").content == PDF
    types = [e["type"] for e in client.get(f"/api/investigations/{inv}/timeline").json()]
    assert "attachment_released" in types
    assert client.get("/api/audit-log", params={"event": "evidence.released"}).json()


def test_scanner_clean_infected_and_failed(client, as_role, demo, fake_scanner):
    inv = _inv(client)
    analyst, viewer = as_role("analyst"), as_role("viewer")
    clean = _upload(analyst, inv)
    assert clean["scan_status"] == "clean" and clean["scanner"].startswith("command:") and not clean["quarantined"]
    assert _paths(clean["id"]) == (False, True)
    assert viewer.get(f"/api/attachments/{clean['id']}/download").content == PDF

    bad = _upload(analyst, inv, EICAR, "invoice.pdf")
    assert bad["scan_status"] == "infected" and bad["quarantined"] and not bad["downloadable"]
    assert _paths(bad["id"]) == (True, False)
    for c in (analyst, viewer, client):                   # never downloadable – not even for administrators
        r = c.get(f"/api/attachments/{bad['id']}/download", params={"acknowledge_risk": True})
        assert r.status_code == 403 and r.json()["code"] == "infected"
    assert client.post(f"/api/attachments/{bad['id']}/release", json={"note": "I am sure it is fine"}).status_code == 403
    assert client.get("/api/audit-log", params={"event": "evidence.scan_infected"}).json()
    events = [e for e in client.get(f"/api/investigations/{inv}/timeline").json() if e["type"] == "attachment_scanned"]
    assert any(e["data"]["result"] == "infected" for e in events)
    from app.services import evidence_scan as ES
    assert ES.run_health_job()[0].details["healthy"] is True   # v0.3.3: health comes from the explicit check
    checks = {c["name"]: c for c in client.get("/api/admin/system").json()["checks"]}
    assert checks["evidence_infected"]["status"] == "warn" and checks["evidence_scanner"]["status"] == "ok"

    err = _upload(analyst, inv, PDF + b"SCANNER-CRASH", "x.pdf")
    assert err["scan_status"] == "failed" and err["quarantined"]
    assert analyst.get(f"/api/attachments/{err['id']}/download").json()["code"] == "quarantined"


def test_worker_scans_backlog_and_limits_retries(client, demo, fake_scanner, monkeypatch):
    from app.config import get_settings
    from app.services import evidence_scan as ES
    from app.services import jobs

    monkeypatch.setattr(jobs.time, "sleep", lambda s: None)
    inv = _inv(client)
    monkeypatch.setattr(get_settings(), "evidence_scanner", "none")
    backlog = _upload(client, inv)                        # uploaded while no scanner was configured
    crash = _upload(client, inv, PDF + b"SCANNER-CRASH", "c.pdf")
    assert backlog["scan_status"] == crash["scan_status"] == "not_scanned"
    assert ES.run_scan_job() == []                        # no scanner → no job
    monkeypatch.setattr(get_settings(), "evidence_scanner", "command")
    runs = ES.run_scan_job()
    assert runs[0].status == "succeeded" and runs[0].details == {"clean": 1, "infected": 0, "failed": 1, "unavailable": 0,
                                                                 "skipped_scanner_unavailable": False}
    st = {a["id"]: a["scan_status"] for a in client.get(f"/api/investigations/{inv}/attachments").json()}
    assert st[backlog["id"]] == "clean" and st[crash["id"]] == "failed"
    # the failing file is retried until FP_EVIDENCE_SCAN_MAX_ATTEMPTS, then left for an administrator
    for _ in range(10):
        ES.run_scan_job()
    from app.db import SessionLocal
    from app.models import InvestigationAttachment
    with SessionLocal() as db:
        assert db.get(InvestigationAttachment, crash["id"]).scan_attempts == get_settings().evidence_scan_max_attempts


def _install_scanner(path):
    """Create an executable scanner command (the fake scanner) at ``path`` – simulates installing/repairing it."""
    import sys

    from tests.conftest import FAKE_SCANNER

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\n{FAKE_SCANNER}")
    path.chmod(0o755)


def test_scanner_outage_is_recorded_transactionally_and_reported(client, demo, fake_scanner, monkeypatch, tmp_path):
    """v0.3.1 review: the scan job must not commit inside its body. v0.3.3: an unreachable scanner is NOT a
    property of the file – the file stays pending (no attempt used up), the outage is persisted in the
    scanner health and only a successful health check clears it."""
    from app.config import get_settings
    from app.db import SessionLocal
    from app.models import InvestigationAttachment
    from app.services import evidence_scan as ES

    inv = _inv(client)
    missing = tmp_path / "bin" / "clamdscan"                       # configured, but not installed (yet)
    monkeypatch.setattr(get_settings(), "evidence_scan_command", str(missing))
    att = _upload(client, inv)
    assert att["scan_status"] == "pending" and att["quarantined"] and att["scan_attempts"] == 0
    run = ES.run_scan_job()[0]
    assert run.status == "succeeded" and run.attempt == 1
    assert run.details["skipped_scanner_unavailable"] is True     # nothing scanned while unavailable
    with SessionLocal() as db:
        a = db.get(InvestigationAttachment, att["id"])
        assert a.scan_status == "pending" and a.scan_attempts == 0 and "FileNotFoundError" in a.scan_detail
        assert ES.health(db).status == "unavailable"
    checks = {c["name"]: c for c in client.get("/api/admin/system").json()["checks"]}
    assert checks["evidence_scanner"]["status"] == "warn" and "UNAVAILABLE" in checks["evidence_scanner"]["detail"]
    # scanner back: a scan run alone does NOT clear the outage; the explicit health check does
    _install_scanner(missing)
    assert ES.run_scan_job()[0].details["skipped_scanner_unavailable"] is True
    assert "UNAVAILABLE" in {c["name"]: c for c in client.get("/api/admin/system").json()["checks"]}["evidence_scanner"]["detail"]
    assert ES.run_health_job()[0].details["healthy"] is True
    assert ES.run_scan_job()[0].details["clean"] == 1
    checks = {c["name"]: c for c in client.get("/api/admin/system").json()["checks"]}
    assert checks["evidence_scanner"]["status"] == "ok"


def test_job_bodies_cannot_commit(demo):
    """Enforced invariant: a body that commits is rejected and its writes are rolled back."""
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import ActionCenterAlert
    from app.services import jobs

    def bad(db):
        db.add(ActionCenterAlert(company_id=1, rule="test_commit", subject_key="x", severity="low"))
        db.commit()

    run = jobs.run_job("test.commit", 1, bad, max_attempts=1)
    assert run.status == "failed" and "JobBodyCommitError" in run.error
    with SessionLocal() as db:
        assert db.scalar(select(ActionCenterAlert.id).where(ActionCenterAlert.rule == "test_commit")) is None


# ----------------------------------------------------------------------------- clamd protocol

class FakeClamd:
    """Minimal clamd INSTREAM server (enough to test the client side of the protocol)."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]
        self.received = []
        self.commands = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                cmd = b""
                while not cmd.endswith(b"\0"):
                    part = conn.recv(1)
                    if not part:
                        break
                    cmd += part
                self.commands.append(cmd)
                if cmd == b"zPING\0":
                    conn.sendall(b"PONG\0")
                    continue
                if cmd == b"zVERSION\0":
                    conn.sendall(b"ClamAV 1.4.1/27000/FakeClamd\0")
                    continue
                assert cmd == b"zINSTREAM\0"
                data = b""
                while True:
                    (n,) = struct.unpack("!L", self._read(conn, 4))
                    if n == 0:
                        break
                    data += self._read(conn, n)
                self.received.append(data)
                found = b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE" in data
                conn.sendall(b"stream: Win.Test.EICAR_HDB-1 FOUND\0" if found else b"stream: OK\0")

    @staticmethod
    def _read(conn, n):
        buf = b""
        while len(buf) < n:
            buf += conn.recv(n - len(buf))
        return buf

    def close(self):
        self.sock.close()


@pytest.fixture()
def clamd(monkeypatch):
    from app.config import get_settings

    srv = FakeClamd()
    s = get_settings()
    monkeypatch.setattr(s, "evidence_scanner", "clamd")
    monkeypatch.setattr(s, "clamd_host", "127.0.0.1")
    monkeypatch.setattr(s, "clamd_port", srv.port)
    yield srv
    srv.close()


def test_clamd_integration(client, demo, clamd):
    inv = _inv(client)
    big = PDF + b"0" * 200_000                            # several INSTREAM chunks
    ok = _upload(client, inv, big, "big.pdf")
    assert ok["scan_status"] == "clean" and ok["scanner"] == f"clamd@127.0.0.1:{clamd.port}"
    assert clamd.received[-1] == big
    bad = _upload(client, inv, EICAR, "e.pdf")
    assert bad["scan_status"] == "infected"


def test_clamd_unreachable_is_a_failure_not_clean(client, demo, monkeypatch):
    from app.config import get_settings

    s = get_settings()
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()                                          # nothing listens on this port
    monkeypatch.setattr(s, "evidence_scanner", "clamd")
    monkeypatch.setattr(s, "clamd_host", "127.0.0.1")
    monkeypatch.setattr(s, "clamd_port", port)
    att = _upload(client, _inv(client))
    # v0.3.3: still quarantined, but "pending" (the scanner outage is not the file's fault: no attempt used)
    assert att["scan_status"] == "pending" and att["quarantined"] and att["scan_attempts"] == 0
    assert client.get(f"/api/attachments/{att['id']}/download", params={"acknowledge_risk": False}).status_code == 409


# ----------------------------------------------------------------------------- real ClamAV (optional)

@pytest.mark.skipif(not __import__("os").environ.get("FP_TEST_CLAMD"),
                    reason="set FP_TEST_CLAMD=host:port to test against a real clamd")
def test_real_clamd(client, demo, monkeypatch):
    import os

    from app.config import get_settings

    host, port = os.environ["FP_TEST_CLAMD"].rsplit(":", 1)
    s = get_settings()
    monkeypatch.setattr(s, "evidence_scanner", "clamd")
    monkeypatch.setattr(s, "clamd_host", host)
    monkeypatch.setattr(s, "clamd_port", int(port))
    from app.services import evidence_scan as ES
    h = ES.check_health()                                  # v0.3.3: PING, VERSION, clean + EICAR sample
    assert h.ok, h.detail
    inv = _inv(client)
    assert _upload(client, inv)["scan_status"] == "clean"
    bad = _upload(client, inv, EICAR, "eicar.pdf")
    assert bad["scan_status"] == "infected"
    detail = client.get(f"/api/investigations/{inv}/attachments").json()[-1]["scan_detail"]
    assert "FOUND" in detail


@pytest.mark.skipif(not __import__("os").environ.get("FP_TEST_CLAMSCAN"),
                    reason="set FP_TEST_CLAMSCAN='clamscan -d <db> --no-summary' to test the command integration")
def test_real_clamscan_command(client, demo, monkeypatch):
    import os

    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "evidence_scanner", "command")
    monkeypatch.setattr(s, "evidence_scan_command", os.environ["FP_TEST_CLAMSCAN"])
    from app.services import evidence_scan as ES
    h = ES.check_health()
    assert h.ok, h.detail
    inv = _inv(client)
    assert _upload(client, inv)["scan_status"] == "clean"
    assert _upload(client, inv, EICAR, "eicar.pdf")["scan_status"] == "infected"
