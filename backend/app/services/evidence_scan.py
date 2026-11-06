"""Malware scanning and quarantine for Action Center evidence files.

Lifecycle of an uploaded file
-----------------------------
1. The upload is validated (type, signature, size) and written atomically to ``<data>/evidence/quarantine``
   (``evidence_store.write_upload``).
2. If a scanner is configured (``FP_EVIDENCE_SCANNER=clamd|command``) it is scanned immediately – unless
   the persisted scanner health says the scanner is unavailable:
   * clean       → status ``clean``; the file is moved to ``<data>/evidence`` AFTER the commit (outbox)
   * infected    → stays in quarantine, NEVER downloadable (not even for administrators via the API)
   * error       → the scanner answered but could not scan this file: status ``failed``, attempt +1;
                   the worker retries up to ``FP_EVIDENCE_SCAN_MAX_ATTEMPTS`` ("retries exhausted" after)
   * unavailable → the scanner could not be reached / executed. This is NOT a property of the file: the
                   file stays ``pending`` (attempt counter unchanged) and the scanner health is recorded as
                   ``unavailable``.
3. Without a scanner the status is ``not_scanned``: the file stays in quarantine and only an
   administrator can download it (explicit risk acknowledgement) or *release* it after checking it
   by other means. Releases are audited and shown in the timeline.

Scanner health (v0.3.3)
-----------------------
``check_health()`` is an explicit, active check (clamd: PING/VERSION + scan of a clean and an EICAR test
sample; command: scan of a clean and an EICAR test file). The result is persisted in ``scanner_health``.
The outage state is set by a failed health check or by an ``unavailable`` scan result, and it is cleared
ONLY by a successful health check – a scan run that had nothing to do never changes it.

Ordinary users (viewer/analyst) can only download ``clean`` or administrator-released files; the decision is
made from the database, never from the directory the file is in (see ``evidence_store``).
"""
from __future__ import annotations

import datetime as dt
import logging
import shlex
import socket
import struct
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError as DBIntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Company, Investigation, InvestigationAttachment, ScannerHealth, User, utcnow
from . import evidence_store as store
from . import jobs

log = logging.getLogger("factorypulse.evidence")
JOB = "evidence.scan"
HEALTH_JOB = "evidence.health"
VERIFIED_FOR_ALL = ("clean",)
BATCH = 50
UNAVAILABLE_PREFIX = "[scanner unavailable] "

# The EICAR anti-virus test string (harmless by definition). Assembled at runtime so that this source file
# itself is not flagged by scanners.
EICAR = ("X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + "EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*").encode()
CLEAN_SAMPLE = b"FactoryPulse AI scanner self-test - clean sample\n"


@dataclass
class ScanResult:
    status: str  # clean | infected | error | unavailable
    detail: str
    scanner: str


def scanner_name() -> str:
    return get_settings().evidence_scanner


def scanner_label() -> str:
    s = get_settings()
    if s.evidence_scanner == "clamd":
        return f"clamd@{s.clamd_host}:{s.clamd_port}"
    if s.evidence_scanner == "command":
        cmd = shlex.split(s.evidence_scan_command or "") or ["<not set>"]
        return f"command:{Path(cmd[0]).name}"[:120]
    return "none"


# ----------------------------------------------------------------------------- scanners

class _Unavailable(Exception):
    pass


def _clamd_connect():
    s = get_settings()
    try:
        return socket.create_connection((s.clamd_host, s.clamd_port), timeout=s.clamd_timeout_seconds)
    except OSError as exc:  # refused, DNS failure, connect timeout, …
        raise _Unavailable(f"scanner unreachable: {exc.__class__.__name__}: {exc}") from exc


def _recv_reply(sock) -> bytes:
    reply = b""
    while not reply.endswith(b"\0"):
        part = sock.recv(4096)
        if not part:
            break
        reply += part
    return reply.rstrip(b"\0")


def _clamd_instream(chunks) -> str:
    """ClamAV daemon INSTREAM protocol (TCP). See clamd(8): zINSTREAM\\0, <len:uint32 BE><data>…, 0-length end.
    Raises _Unavailable when the daemon cannot be reached or drops the connection without an answer."""
    with _clamd_connect() as sock:
        try:
            sock.sendall(b"zINSTREAM\0")
            for chunk in chunks:
                sock.sendall(struct.pack("!L", len(chunk)) + chunk)
            sock.sendall(struct.pack("!L", 0))
            text = _recv_reply(sock).decode("utf-8", "replace").strip()
        except (ConnectionError, socket.gaierror) as exc:
            raise _Unavailable(f"scanner connection lost: {exc.__class__.__name__}: {exc}") from exc
    if not text:
        raise _Unavailable("scanner closed the connection without a result")
    return text


def _file_chunks(path: Path):
    with path.open("rb") as fh:
        while chunk := fh.read(64 * 1024):
            yield chunk


def _classify_clamd(text: str, label: str) -> ScanResult:
    if text.endswith("FOUND"):
        return ScanResult("infected", text[:500], label)
    if text.endswith("OK"):
        return ScanResult("clean", text[:500], label)
    return ScanResult("error", text[:500], label)  # e.g. "INSTREAM size limit exceeded. ERROR"


def _scan_clamd(path: Path) -> ScanResult:
    label = scanner_label()
    try:
        text = _clamd_instream(_file_chunks(path))
    except _Unavailable as exc:
        return ScanResult("unavailable", str(exc)[:500], label)
    except OSError as exc:  # e.g. read timeout while scanning a large file: a problem with THIS scan
        return ScanResult("error", f"{exc.__class__.__name__}: {exc}"[:500], label)
    return _classify_clamd(text, label)


def _command_argv(path: Path) -> list[str]:
    s = get_settings()
    if not s.evidence_scan_command:
        raise _Unavailable("FP_EVIDENCE_SCAN_COMMAND is not set")
    return [*shlex.split(s.evidence_scan_command), str(path)]


def _scan_command(path: Path) -> ScanResult:
    s = get_settings()
    label = scanner_label()
    try:
        argv = _command_argv(path)
        p = subprocess.run(argv, capture_output=True, text=True, timeout=s.clamd_timeout_seconds, check=False)
    except _Unavailable as exc:
        return ScanResult("unavailable", str(exc), label)
    except subprocess.TimeoutExpired as exc:
        return ScanResult("error", f"TimeoutExpired: {exc}"[:500], label)
    except OSError as exc:  # command missing, not executable, …
        return ScanResult("unavailable", f"{exc.__class__.__name__}: {exc}"[:500], label)
    out = (p.stdout + p.stderr).strip().replace(str(path), "<file>")[:500]
    if p.returncode == 0:
        return ScanResult("clean", out or "exit 0", label)
    if p.returncode == 1:
        return ScanResult("infected", out or "exit 1", label)
    return ScanResult("error", f"exit {p.returncode}: {out}"[:500], label)


def scan_file(path: Path) -> ScanResult | None:
    """None when no scanner is configured."""
    kind = scanner_name()
    if kind == "clamd":
        return _scan_clamd(path)
    if kind == "command":
        return _scan_command(path)
    return None


# ----------------------------------------------------------------------------- health check

@dataclass
class HealthResult:
    ok: bool
    detail: str
    scanner: str


def _health_clamd() -> HealthResult:
    s = get_settings()
    label = scanner_label()
    try:
        with _clamd_connect() as sock:
            sock.sendall(b"zPING\0")
            pong = _recv_reply(sock).decode("utf-8", "replace").strip()
        if pong != "PONG":
            return HealthResult(False, f"unexpected PING reply: {pong or 'no reply'}"[:500], label)
        with _clamd_connect() as sock:
            sock.sendall(b"zVERSION\0")
            version = _recv_reply(sock).decode("utf-8", "replace").strip()
        clean = _classify_clamd(_clamd_instream([CLEAN_SAMPLE]), label)
        if clean.status != "clean":
            return HealthResult(False, f"clean test sample not reported clean: {clean.detail}"[:500], label)
        if s.evidence_scanner_selftest_eicar:
            eicar = _classify_clamd(_clamd_instream([EICAR]), label)
            if eicar.status != "infected":
                return HealthResult(False, f"EICAR test sample not detected: {eicar.detail}"[:500], label)
    except _Unavailable as exc:
        return HealthResult(False, str(exc)[:500], label)
    except OSError as exc:
        return HealthResult(False, f"{exc.__class__.__name__}: {exc}"[:500], label)
    return HealthResult(True, (version or "PONG")[:500], label)


def _health_command() -> HealthResult:
    s = get_settings()
    label = scanner_label()
    d = store.staging_dir()
    d.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    clean_p, eicar_p = d / f"selftest-{token}-clean.txt", d / f"selftest-{token}-eicar.txt"
    try:
        clean_p.write_bytes(CLEAN_SAMPLE)
        r = _scan_command(clean_p)
        if r.status != "clean":
            return HealthResult(False, f"clean test file: {r.status}: {r.detail}"[:500], label)
        if s.evidence_scanner_selftest_eicar:
            eicar_p.write_bytes(EICAR)
            r = _scan_command(eicar_p)
            if r.status != "infected":
                return HealthResult(False, f"EICAR test file not detected: {r.status}: {r.detail}"[:500], label)
    finally:
        for p in (clean_p, eicar_p):
            p.unlink(missing_ok=True)
    return HealthResult(True, "self-test passed (clean sample clean"
                              + (", EICAR detected)" if s.evidence_scanner_selftest_eicar else ")"), label)


def check_health() -> HealthResult | None:
    """Explicit, active scanner check. None when no scanner is configured. Never raises for scanner problems."""
    kind = scanner_name()
    if kind == "clamd":
        return _health_clamd()
    if kind == "command":
        return _health_command()
    return None


def _health_row(db: Session, label: str) -> ScannerHealth:
    row = db.scalar(select(ScannerHealth).where(ScannerHealth.scanner == label))
    if row is None:
        try:
            with db.begin_nested():
                row = ScannerHealth(scanner=label, status="unknown", consecutive_failures=0)
                db.add(row)
                db.flush()
        except DBIntegrityError:  # created concurrently
            row = db.scalar(select(ScannerHealth).where(ScannerHealth.scanner == label))
    return row


def health(db: Session) -> ScannerHealth | None:
    """Current persisted health of the configured scanner (read-only). None without scanner or before any check."""
    if scanner_name() == "none":
        return None
    return db.scalar(select(ScannerHealth).where(ScannerHealth.scanner == scanner_label()))


def scanner_unavailable(db: Session) -> bool:
    h = health(db)
    return h is not None and h.status == "unavailable"


def _audit_all(db: Session, event: str, entity_id: int, details: dict) -> None:
    """Scanner state changes concern the whole installation: visible in every company's audit log."""
    from .settings_store import audit

    for cid in db.scalars(select(Company.id).order_by(Company.id)).all():
        audit(db, event, cid, "scanner", entity_id, details)


def record_failure(db: Session, label: str, error: str, *, source: str) -> ScannerHealth:
    """Persist an outage (from a failed health check or an ``unavailable`` scan result)."""
    row = _health_row(db, label)
    now = utcnow()
    was = row.status
    row.status, row.last_failure_at, row.last_error = "unavailable", now, error[:500]
    row.consecutive_failures = (row.consecutive_failures or 0) + 1
    if source == "health_check":
        row.checked_at = now
    if was != "unavailable":
        row.unavailable_since = now
        log.error("Evidence scanner %s is UNAVAILABLE (%s): %s", label, source, error)
        _audit_all(db, "evidence.scanner_unavailable", row.id, {"scanner": label, "source": source, "error": error[:200]})
    return row


def record_success(db: Session, label: str, detail: str) -> ScannerHealth:
    """Persist a successful health check – the ONLY way an outage is cleared."""
    row = _health_row(db, label)
    now = utcnow()
    was, since = row.status, row.unavailable_since
    row.status, row.checked_at, row.last_ok_at, row.detail = "healthy", now, now, detail[:500]
    row.consecutive_failures, row.unavailable_since = 0, None
    if was == "unavailable":
        log.warning("Evidence scanner %s recovered (unavailable since %s)", label, since)
        _audit_all(db, "evidence.scanner_recovered", row.id,
                   {"scanner": label, "unavailable_since": since.isoformat() if since else None})
    return row


def run_health_check(db: Session) -> dict:
    """Check the scanner and persist the result in the caller's transaction (job body: no commit)."""
    r = check_health()
    if r is None:
        return {"scanner": "none"}
    row = record_success(db, r.scanner, r.detail) if r.ok else record_failure(db, r.scanner, r.detail, source="health_check")
    return {"scanner": r.scanner, "healthy": r.ok, "detail": r.detail[:200],
            "consecutive_failures": row.consecutive_failures}


def health_dict(row: ScannerHealth | None) -> dict | None:
    if row is None:
        return None
    iso = lambda v: v.isoformat() if v else None  # noqa: E731
    return {"scanner": row.scanner, "status": row.status, "checked_at": iso(row.checked_at),
            "last_ok_at": iso(row.last_ok_at), "last_failure_at": iso(row.last_failure_at),
            "unavailable_since": iso(row.unavailable_since), "consecutive_failures": row.consecutive_failures,
            "last_error": row.last_error, "detail": row.detail}


# ----------------------------------------------------------------------------- storage (delegated)

quarantine_path = store.quarantine_path
released_path = store.released_path


def is_verified(a: InvestigationAttachment) -> bool:
    """Downloadable for ordinary users: scanner said clean, or an administrator released it."""
    return a.scan_status == "clean" or (a.released_at is not None and a.scan_status != "infected")


def locate(a: InvestigationAttachment) -> Path | None:
    return store.locate(a)


def retries_exhausted(a: InvestigationAttachment) -> bool:
    return (a.scan_status == "failed" and a.released_at is None
            and (a.scan_attempts or 0) >= get_settings().evidence_scan_max_attempts)


# ----------------------------------------------------------------------------- state changes

def apply_result(db: Session, a: InvestigationAttachment, r: ScanResult) -> None:
    """Persist a scan result. File moves are NOT done here: they are queued in the outbox and executed
    after the commit (``evidence_store``)."""
    from .action_center import add_event
    from .settings_store import audit

    if r.status == "unavailable":
        # not a property of the file: keep it waiting, do not use up an attempt
        if a.scan_status in ("not_scanned",):
            a.scan_status = "pending"
        a.scan_detail, a.scanner = (UNAVAILABLE_PREFIX + r.detail)[:500], r.scanner
        record_failure(db, r.scanner, r.detail, source=f"scan of attachment {a.id}")
        return
    previous = a.scan_status
    status = "failed" if r.status == "error" else r.status
    a.scan_status, a.scan_detail, a.scanner, a.scanned_at = status, r.detail, r.scanner, utcnow()
    a.scan_attempts = (a.scan_attempts or 0) + 1
    if status in ("clean", "infected"):
        store.enqueue(db, a, f"scan_{status}")
    if status != previous and status in ("clean", "infected"):
        inv = db.get(Investigation, a.investigation_id)
        add_event(db, inv, "attachment_scanned", None, {"attachment_id": a.id, "filename": a.original_filename,
                                                        "result": status, "scanner": r.scanner})
        audit(db, f"evidence.scan_{status}", a.company_id, "attachment", a.id,
              {"investigation_id": a.investigation_id, "scanner": r.scanner, "detail": r.detail[:200]})
    if status == "infected":
        log.warning("Evidence %s (attachment %s) flagged as INFECTED by %s: %s", a.original_filename, a.id, r.scanner, r.detail)
    elif status == "failed" and retries_exhausted(a):
        log.error("Evidence %s (attachment %s): scan retries exhausted (%s); stays quarantined", a.original_filename,
                  a.id, r.detail)


def scan_attachment(db: Session, a: InvestigationAttachment) -> ScanResult | None:
    """Scan one attachment and record the result (no commit). The file is scanned where it is; its
    integrity is checked first."""
    path = locate(a)
    if path is None:
        r = ScanResult("error", "file missing", scanner_label())
        apply_result(db, a, r)
        return r
    if store.sha256_of(path) != a.sha256:
        r = ScanResult("error", "file content does not match the SHA-256 recorded at upload", scanner_label())
        apply_result(db, a, r)
        return r
    r = scan_file(path)
    if r is None:
        a.scan_status = "not_scanned"
        return None
    apply_result(db, a, r)
    return r


def release(db: Session, a: InvestigationAttachment, admin: User, note: str) -> None:
    from .action_center import add_event
    from .settings_store import audit

    if a.scan_status == "infected":
        raise PermissionError("Files flagged as infected can never be released.")
    a.released_at, a.released_by_id, a.release_note = utcnow(), admin.id, note.strip()[:500]
    store.enqueue(db, a, "released")
    inv = db.get(Investigation, a.investigation_id)
    add_event(db, inv, "attachment_released", admin, {"attachment_id": a.id, "filename": a.original_filename,
                                                      "scan_status": a.scan_status, "note": a.release_note})
    audit(db, "evidence.released", a.company_id, "attachment", a.id,
          {"scan_status": a.scan_status, "note": a.release_note, "sha256": a.sha256})


# ----------------------------------------------------------------------------- quarantine statistics

def quarantine_counts(db: Session, company_id: int | None = None) -> dict:
    """Unverified evidence by state (soft-deleted and released files excluded)."""
    A = InvestigationAttachment
    maxa = get_settings().evidence_scan_max_attempts
    base = [A.deleted_at.is_(None), A.released_at.is_(None)]
    if company_id is not None:
        base.append(A.company_id == company_id)

    def n(*cond):
        return db.scalar(select(func.count(A.id)).where(*base, *cond)) or 0
    return {"pending": n(A.scan_status == "pending"),
            "retrying": n(A.scan_status == "failed", A.scan_attempts < maxa),
            "retries_exhausted": n(A.scan_status == "failed", A.scan_attempts >= maxa),
            "not_scanned": n(A.scan_status == "not_scanned"),
            "infected": n(A.scan_status == "infected"),
            "max_attempts": maxa}


# ----------------------------------------------------------------------------- background jobs

def pending_query(company_id: int):
    s = get_settings()
    A = InvestigationAttachment
    return (select(A).where(A.company_id == company_id, A.deleted_at.is_(None), A.released_at.is_(None),
                            or_(A.scan_status.in_(("pending", "not_scanned")),
                                (A.scan_status == "failed") & (A.scan_attempts < s.evidence_scan_max_attempts)))
            .order_by(A.id).limit(BATCH))


def scan_pending(db: Session, company_id: int) -> dict:
    """Job body (runs inside ONE transaction owned by jobs.run_job – it never commits itself).

    * If the persisted scanner health is ``unavailable`` nothing is scanned (the files stay pending and keep
      their attempts); only the health job can clear that state.
    * A per-file scanner error is a recorded *outcome* (``failed``, attempt +1).
    * An ``unavailable`` result stops the run and records the outage.
    * A run with nothing to do changes NOTHING about the scanner health.
    Unexpected errors (database, bugs) raise and roll the whole run back."""
    counts = {"clean": 0, "infected": 0, "failed": 0, "unavailable": 0, "skipped_scanner_unavailable": False}
    if scanner_unavailable(db):
        counts["skipped_scanner_unavailable"] = True
        return counts
    for a in db.scalars(pending_query(company_id)).all():
        r = scan_attachment(db, a)
        if r is None:
            continue
        key = {"error": "failed"}.get(r.status, r.status)
        counts[key] += 1
        if r.status == "unavailable":
            log.error("Evidence scanner became unavailable during the scan run: %s", r.detail)
            break
    return counts


def run_health_job(*, trigger: str = "scheduler", **kw) -> list:
    if scanner_name() == "none":
        return []
    from ..db import SessionLocal

    sf = kw.pop("session_factory", None) or SessionLocal
    return [jobs.run_job(HEALTH_JOB, None, run_health_check, trigger=trigger, session_factory=sf, **kw)]


def run_scan_job(company_id: int | None = None, *, trigger: str = "scheduler", **kw) -> list:
    if scanner_name() == "none":
        return []
    from ..db import SessionLocal

    sf = kw.pop("session_factory", None) or SessionLocal
    with sf() as db:
        ids = [company_id] if company_id else list(db.scalars(select(Company.id).order_by(Company.id)))
    return [jobs.run_job(JOB, cid, lambda db, cid=cid: scan_pending(db, cid), trigger=trigger, session_factory=sf, **kw)
            for cid in ids]


def health_is_stale(row: ScannerHealth | None) -> bool:
    from .. import clock

    if row is None or row.checked_at is None:
        return True
    checked = row.checked_at if row.checked_at.tzinfo else row.checked_at.replace(tzinfo=dt.timezone.utc)
    return (clock.now_utc() - checked).total_seconds() > 3 * get_settings().alert_interval_seconds
