"""Background job runner: persistent run log, retries with exponential backoff, concurrency guard.

* Every attempt is recorded in ``job_runs`` (status, attempt, duration, error) and logged.
* The job body runs in ONE database transaction: a failure rolls back completely, so a retry starts
  from a consistent state ("retried safely"). Job bodies must therefore be idempotent.
* Concurrency: on PostgreSQL a transaction-scoped advisory lock per (job, company) guarantees that two
  workers (or a worker and a manual "evaluate now") never run the same job at the same time; the
  second one is recorded as ``skipped``. Database constraints (e.g. the partial unique index on open
  alerts) remain the last line of defence; an IntegrityError is treated like any other failure and retried.
"""
from __future__ import annotations

import logging
import os
import socket
import time
import traceback
import zlib
from collections.abc import Callable
from typing import Any

from sqlalchemy import event, select, text, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import JobRun, utcnow

log = logging.getLogger("factorypulse.jobs")
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


class JobBodyCommitError(RuntimeError):
    """A job body tried to commit. Bodies run in ONE transaction owned by run_job (retry = clean rollback)."""


@event.listens_for(Session, "before_commit")
def _forbid_commit_inside_job_body(session: Session) -> None:
    # releasing a SAVEPOINT (begin_nested) is allowed: it does not end the job's transaction
    if session.info.get("fp_job_body") and not session.in_nested_transaction():
        raise JobBodyCommitError("job bodies must not commit – run_job commits once after the body succeeded")


def _session_factory():
    from ..db import SessionLocal

    return SessionLocal


def lock_key(job: str, company_id: int | None) -> int:
    return zlib.crc32(f"{job}:{company_id or 0}".encode())


def try_lock(db: Session, job: str, company_id: int | None) -> bool:
    """Transaction-scoped lock (released on commit/rollback). SQLite serialises writers itself."""
    if db.get_bind().dialect.name == "postgresql":
        return bool(db.scalar(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": lock_key(job, company_id)}))
    return True


def _finish(session_factory, run_id: int, status: str, **values: Any) -> JobRun:
    with session_factory() as db:
        run = db.get(JobRun, run_id)
        now = utcnow()
        run.status, run.finished_at = status, now
        started = run.started_at if run.started_at.tzinfo else run.started_at.replace(tzinfo=now.tzinfo)
        run.duration_ms = max(int((now - started).total_seconds() * 1000), 0)
        for k, v in values.items():
            setattr(run, k, v)
        db.commit()
        db.refresh(run)
        db.expunge(run)
        return run


def run_job(job: str, company_id: int | None, body: Callable[[Session], dict | None], *, trigger: str = "scheduler",
            max_attempts: int | None = None, retry_base_seconds: float | None = None,
            sleep: Callable[[float], None] | None = None, session_factory=None) -> JobRun:
    """Run ``body(db)`` with retries. Returns the final JobRun (succeeded, skipped or failed)."""
    s = get_settings()
    session_factory = session_factory or _session_factory()
    sleep = sleep or time.sleep
    max_attempts = max_attempts or s.job_max_attempts
    base = s.job_retry_base_seconds if retry_base_seconds is None else retry_base_seconds
    last: JobRun | None = None
    for attempt in range(1, max_attempts + 1):
        with session_factory() as db:
            run = JobRun(job=job, company_id=company_id, status="running", trigger=trigger[:120], attempt=attempt,
                         max_attempts=max_attempts, worker=WORKER_ID[:120])
            db.add(run)
            db.commit()
            run_id = run.id
        with session_factory() as db:
            try:
                if not try_lock(db, job, company_id):
                    db.rollback()
                    log.info("Job %s (company %s) skipped: another run holds the lock", job, company_id)
                    return _finish(session_factory, run_id, "skipped", details={"reason": "already running"})
                db.info["fp_job_body"] = True
                try:
                    details = body(db) or {}
                finally:
                    db.info.pop("fp_job_body", None)
                db.commit()
            except Exception as exc:  # noqa: BLE001 - every failure is recorded and retried
                db.rollback()
                err = "".join(traceback.format_exception_only(type(exc), exc)).strip()[:4000]
                log.error("Job %s (company %s) attempt %d/%d failed: %s", job, company_id, attempt, max_attempts, err,
                          exc_info=True)
                last = _finish(session_factory, run_id, "failed", error=err)
                if attempt < max_attempts:
                    sleep(base * (2 ** (attempt - 1)))
                continue
        log.info("Job %s (company %s) succeeded on attempt %d: %s", job, company_id, attempt, details)
        return _finish(session_factory, run_id, "succeeded", details=details)
    log.critical("Job %s (company %s) failed after %d attempts", job, company_id, max_attempts)
    return last  # type: ignore[return-value]


def last_success(db: Session, job: str, company_id: int | None) -> JobRun | None:
    return db.scalar(select(JobRun).where(JobRun.job == job, JobRun.company_id == company_id,
                                          JobRun.status == "succeeded").order_by(JobRun.finished_at.desc()).limit(1))


def latest(db: Session, job: str, company_id: int | None) -> JobRun | None:
    return db.scalar(select(JobRun).where(JobRun.job == job, JobRun.company_id == company_id,
                                          JobRun.status != "running").order_by(JobRun.id.desc()).limit(1))


def mark_abandoned(db: Session, older_than_seconds: int = 3600) -> int:
    """Runs left in 'running' by a killed process are marked 'abandoned' (they never committed anything)."""
    import datetime as dt

    cutoff = utcnow() - dt.timedelta(seconds=older_than_seconds)
    q = update(JobRun).where(JobRun.status == "running", JobRun.started_at < cutoff)
    return db.execute(q.values(status="abandoned", finished_at=utcnow())).rowcount or 0


def prune(db: Session) -> int:
    import datetime as dt
    from sqlalchemy import delete

    cutoff = utcnow() - dt.timedelta(days=get_settings().job_history_days)
    return db.execute(delete(JobRun).where(JobRun.started_at < cutoff)).rowcount or 0


def run_dict(r: JobRun | None) -> dict | None:
    if r is None:
        return None
    return {"id": r.id, "job": r.job, "company_id": r.company_id, "status": r.status, "trigger": r.trigger,
            "attempt": r.attempt, "max_attempts": r.max_attempts, "worker": r.worker,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "duration_ms": r.duration_ms, "error": r.error, "details": r.details}
