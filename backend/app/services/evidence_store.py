"""Crash-safe storage of evidence files (v0.3.3).

Problem: a file move is not part of a database transaction. If a file is moved and the commit then fails
(or the process dies), the filesystem and the database disagree.

Strategy
--------
1. **The database is the only authority for access.** Whether a file may be downloaded is decided from
   the persisted scan result / release (``evidence_scan.is_verified``), never from the directory a file
   happens to be in. A file in ``evidence/`` without a persisted clean result or release is NOT served to
   ordinary users. Every served file is checked against the SHA-256 recorded at upload (integrity).
2. **Atomic upload.** Uploads are written to ``evidence/.staging/<name>.part``, fsynced, then atomically
   renamed into ``evidence/quarantine/``. The database row is committed afterwards; if that commit fails
   the file is deleted again, and a crash in between leaves an *orphan* that reconciliation removes from
   the quarantine (moved to ``evidence/orphaned/``) after a grace period.
3. **Transactional outbox for moves.** A state change that implies a different location (clean scan,
   administrator release) inserts an ``evidence_file_ops`` row in the SAME transaction. The file is moved
   only AFTER the commit. A failed commit therefore never moves anything.
4. **Idempotent placement.** ``place()`` moves a file to the location implied by the *current* database
   state using ``os.replace`` (atomic on one filesystem), verifies the SHA-256 first, and can be repeated
   any number of times. Failed operations stay ``pending``/``failed`` and are retried.
5. **Reconciliation.** The worker periodically processes the outbox and compares every attachment's
   expected location with the filesystem (misplaced → moved, missing → reported), moves orphan files out
   of the quarantine and removes stale ``.part`` files. Readiness reports anything unresolved.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import EvidenceFileOp, InvestigationAttachment, utcnow

log = logging.getLogger("factorypulse.evidence.store")
JOB = "evidence.reconcile"
MAX_OP_ATTEMPTS = 20


class IntegrityError(Exception):
    """The file on disk does not match the SHA-256 recorded at upload."""


# ----------------------------------------------------------------------------- paths

def evidence_dir() -> Path:
    return get_settings().evidence_dir


def quarantine_dir() -> Path:
    return get_settings().quarantine_dir


def staging_dir() -> Path:
    return get_settings().evidence_dir / ".staging"


def orphan_dir() -> Path:
    return get_settings().evidence_dir / "orphaned"


def quarantine_path(a: InvestigationAttachment) -> Path:
    return quarantine_dir() / a.stored_filename


def released_path(a: InvestigationAttachment) -> Path:
    return evidence_dir() / a.stored_filename


def expected_path(a: InvestigationAttachment) -> Path:
    from .evidence_scan import is_verified

    return released_path(a) if is_verified(a) else quarantine_path(a)


def locate(a: InvestigationAttachment) -> Path | None:
    exp = expected_path(a)
    other = quarantine_path(a) if exp == released_path(a) else released_path(a)
    for p in (exp, other):
        if p.exists():
            return p
    return None


def _fsync_dir(d: Path) -> None:
    try:
        fd = os.open(d, os.O_RDONLY)
    except OSError:  # pragma: no cover - not supported on every platform
        return
    try:
        os.fsync(fd)
    except OSError:  # pragma: no cover
        pass
    finally:
        os.close(fd)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


# ----------------------------------------------------------------------------- upload

def write_upload(stored_filename: str, data: bytes) -> Path:
    """Atomically create ``quarantine/<stored_filename>`` (staging file + fsync + rename)."""
    staging_dir().mkdir(parents=True, exist_ok=True)
    quarantine_dir().mkdir(parents=True, exist_ok=True)
    part = staging_dir() / f"{stored_filename}.part"
    with part.open("wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    target = quarantine_dir() / stored_filename
    os.replace(part, target)
    _fsync_dir(quarantine_dir())
    return target


def discard_upload(stored_filename: str) -> None:
    """Undo ``write_upload`` after a failed database commit (best effort; reconciliation is the backstop)."""
    for p in (quarantine_dir() / stored_filename, staging_dir() / f"{stored_filename}.part"):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:  # pragma: no cover
            log.error("Could not remove uncommitted upload %s: %s", p, exc)


# ----------------------------------------------------------------------------- outbox

def enqueue(db: Session, a: InvestigationAttachment, reason: str) -> EvidenceFileOp:
    """Record (in the caller's transaction) that the file must be placed according to the new state."""
    op = EvidenceFileOp(attachment_id=a.id, op="place", reason=reason[:40], status="pending", attempts=0)
    db.add(op)
    return op


def place(a: InvestigationAttachment) -> str:
    """Idempotently move the file to the location implied by the CURRENT database state.
    Returns "ok" | "moved". Raises FileNotFoundError, IntegrityError or OSError."""
    dst = expected_path(a)
    src = quarantine_path(a) if dst == released_path(a) else released_path(a)
    if dst.exists():
        if src.exists():  # duplicate (e.g. crash during a copy-based move on another filesystem)
            if sha256_of(dst) == a.sha256:
                src.unlink()
            elif sha256_of(src) == a.sha256:
                os.replace(src, dst)
            else:
                raise IntegrityError("neither copy matches the recorded SHA-256")
        return "ok"
    if not src.exists():
        raise FileNotFoundError(f"evidence file {a.stored_filename} not found")
    if sha256_of(src) != a.sha256:
        raise IntegrityError("file content does not match the SHA-256 recorded at upload")
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)
    _fsync_dir(dst.parent)
    _fsync_dir(src.parent)
    return "moved"


def process_ops(db: Session, attachment_ids: list[int] | None = None, limit: int = 200) -> dict:
    """Execute pending/failed outbox rows. File operations are idempotent; the row updates belong to the
    caller's transaction (if that transaction is rolled back, the operations simply run again)."""
    q = select(EvidenceFileOp).where(EvidenceFileOp.status.in_(("pending", "failed")),
                                     EvidenceFileOp.attempts < MAX_OP_ATTEMPTS)
    if attachment_ids is not None:
        q = q.where(EvidenceFileOp.attachment_id.in_(attachment_ids))
    counts = {"done": 0, "failed": 0}
    for op in db.scalars(q.order_by(EvidenceFileOp.id).limit(limit)).all():
        a = db.get(InvestigationAttachment, op.attachment_id)
        op.attempts += 1
        try:
            place(a)
        except (OSError, IntegrityError) as exc:
            op.status, op.last_error = "failed", f"{exc.__class__.__name__}: {exc}"[:500]
            counts["failed"] += 1
            log.error("Evidence file operation %s (attachment %s) failed: %s", op.id, op.attachment_id, op.last_error)
            continue
        op.status, op.done_at, op.last_error = "done", utcnow(), None
        counts["done"] += 1
    return counts


def apply_now(attachment_ids: list[int]) -> None:
    """After a committed state change in a request: place the files immediately (best effort). Anything
    that fails here is retried by the reconciliation job."""
    from ..db import SessionLocal

    try:
        with SessionLocal() as db:
            process_ops(db, attachment_ids)
            db.commit()
    except Exception:  # noqa: BLE001 - never fail a committed request because of a file move
        log.exception("Immediate evidence file placement failed; the reconciliation job will retry")


# ----------------------------------------------------------------------------- reconciliation

def _file_age_seconds(p: Path) -> float:
    return time.time() - p.stat().st_mtime


def reconcile(db: Session, company_id: int | None = None, *, orphan_grace_seconds: int | None = None) -> dict:
    """Bring the filesystem in line with the database. Idempotent; safe to run at any time and repeatedly.
    Runs as a job body (no commit here)."""
    if orphan_grace_seconds is None:
        orphan_grace_seconds = get_settings().evidence_orphan_grace_seconds
    counts = {"ops_done": 0, "ops_failed": 0, "misplaced_fixed": 0, "missing": 0, "integrity_errors": 0, "move_errors": 0,
              "orphans_moved": 0, "stale_parts_removed": 0}
    r = process_ops(db)
    counts["ops_done"], counts["ops_failed"] = r["done"], r["failed"]

    A = InvestigationAttachment
    q = select(A)
    if company_id is not None:
        q = q.where(A.company_id == company_id)
    known: set[str] = set()
    for a in db.scalars(q).all():
        known.add(a.stored_filename)
        if a.deleted_at is not None:
            continue  # soft-deleted evidence keeps its file where it is (audit)
        exp = expected_path(a)
        if exp.exists():
            other = quarantine_path(a) if exp == released_path(a) else released_path(a)
            if not other.exists():
                continue
        try:
            if place(a) == "moved":
                counts["misplaced_fixed"] += 1
                log.warning("Reconciliation moved evidence %s (attachment %s) to %s", a.stored_filename, a.id, exp.parent.name)
        except FileNotFoundError:
            counts["missing"] += 1
        except IntegrityError:
            counts["integrity_errors"] += 1
            log.error("Evidence %s (attachment %s) does not match its SHA-256", a.stored_filename, a.id)
        except OSError as exc:   # e.g. disk full / permissions: reported, retried on the next run
            counts["move_errors"] += 1
            log.error("Evidence %s (attachment %s) could not be placed: %s", a.stored_filename, a.id, exc)

    # orphans are files without ANY attachment row (e.g. crash between file write and commit); the grace
    # period protects uploads whose transaction is still in flight
    all_known = known if company_id is None else set(db.scalars(select(A.stored_filename)))
    for d in (quarantine_dir(), evidence_dir()):
        if not d.exists():
            continue
        for f in d.iterdir():
            if f.is_file() and f.name not in all_known and _file_age_seconds(f) > orphan_grace_seconds:
                orphan_dir().mkdir(parents=True, exist_ok=True)
                os.replace(f, orphan_dir() / f.name)
                counts["orphans_moved"] += 1
                log.warning("Unreferenced evidence file %s moved to evidence/orphaned", f.name)
    if staging_dir().exists():
        for f in staging_dir().iterdir():
            if f.is_file() and _file_age_seconds(f) > orphan_grace_seconds:
                f.unlink()
                counts["stale_parts_removed"] += 1
    return counts


def storage_status(db: Session) -> dict:
    """For readiness/admin: unresolved file operations and inconsistencies (read-only, no file moves)."""
    from sqlalchemy import func

    pending = db.scalar(select(func.count(EvidenceFileOp.id)).where(EvidenceFileOp.status == "pending")) or 0
    failed = db.scalar(select(func.count(EvidenceFileOp.id)).where(EvidenceFileOp.status == "failed")) or 0
    misplaced = missing = 0
    for a in db.scalars(select(InvestigationAttachment).where(InvestigationAttachment.deleted_at.is_(None))).all():
        if expected_path(a).exists():
            continue
        if locate(a) is None:
            missing += 1
        else:
            misplaced += 1
    orphaned = sum(1 for f in orphan_dir().iterdir() if f.is_file()) if orphan_dir().exists() else 0
    return {"ops_pending": pending, "ops_failed": failed, "misplaced": misplaced, "missing": missing, "orphaned": orphaned}


def run_reconcile_job(company_id: int | None = None, *, trigger: str = "scheduler", **kw) -> list:
    from ..db import SessionLocal
    from . import jobs

    sf = kw.pop("session_factory", None) or SessionLocal
    return [jobs.run_job(JOB, company_id, lambda db: reconcile(db, company_id), trigger=trigger, session_factory=sf, **kw)]
