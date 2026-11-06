"""FactoryPulse background worker – a separate process from the web API.

    python -m app.worker            # run forever (docker compose service "worker")
    python -m app.worker --once     # one cycle, exit code 1 if a job finally failed (cron / tests)

Each cycle:
  1. evaluates the Action Center alert rules for every company   (job "alerts.evaluate")
  2. explicit malware-scanner health check (persisted)            (job "evidence.health")
  3. scans quarantined evidence files – skipped while the scanner   (job "evidence.scan")
     is unavailable
  4. executes queued evidence file moves and reconciles the        (job "evidence.reconcile")
     evidence directories with the database (also without scanner)
  5. housekeeping: marks runs of killed processes as abandoned, prunes old job history

Failures are logged, recorded in ``job_runs`` and retried with exponential backoff; a failing cycle
never stops the worker. Several workers may run at the same time: PostgreSQL advisory locks make sure a
job runs at most once concurrently.
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time

from .config import get_settings

log = logging.getLogger("factorypulse.worker")
_stop = threading.Event()


def wait_for_schema(timeout: float = 300.0, poll: float = 3.0) -> bool:
    """The API container applies migrations; the worker waits until the schema is at head."""
    from alembic.script import ScriptDirectory
    from alembic.config import Config
    from pathlib import Path
    from sqlalchemy import text

    from .db import engine

    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    deadline = time.monotonic() + timeout
    while not _stop.is_set():
        try:
            with engine.connect() as conn:
                if conn.scalar(text("SELECT version_num FROM alembic_version")) == head:
                    return True
        except Exception as exc:  # noqa: BLE001 - database not up yet
            log.info("Waiting for database schema %s: %s", head, exc.__class__.__name__)
        if time.monotonic() > deadline:
            return False
        _stop.wait(poll)
    return False


def cycle() -> bool:
    """Run all jobs once. Returns False if any job finally failed."""
    from .db import SessionLocal
    from .services import alerts, evidence_scan, evidence_store, jobs
    from .services.auth import request_ctx

    request_ctx.set({"request_id": "worker", "user_email": "system:worker"})
    ok = True
    with SessionLocal() as db:
        n = jobs.mark_abandoned(db)
        db.commit()
        if n:
            log.warning("Marked %d interrupted job run(s) as abandoned", n)
    for run in alerts.run_alert_evaluation(trigger="scheduler"):
        ok &= run.status in ("succeeded", "skipped")
    # v0.3.3: explicit scanner health check first (the only thing that can clear an outage), then the scan
    # backlog (skipped while the scanner is unavailable), then file placement/reconciliation (outbox)
    for run in evidence_scan.run_health_job(trigger="scheduler"):
        ok &= run.status in ("succeeded", "skipped")
    for run in evidence_scan.run_scan_job(trigger="scheduler"):
        ok &= run.status in ("succeeded", "skipped")
    for run in evidence_store.run_reconcile_job(trigger="scheduler"):
        ok &= run.status in ("succeeded", "skipped")
    with SessionLocal() as db:
        jobs.prune(db)
        db.commit()
    return ok


def main(argv: list[str] | None = None) -> int:
    s = get_settings()
    p = argparse.ArgumentParser(prog="python -m app.worker")
    p.add_argument("--once", action="store_true", help="run one cycle and exit")
    p.add_argument("--interval", type=int, default=s.alert_interval_seconds, help="seconds between cycles")
    args = p.parse_args(argv)
    logging.basicConfig(level=s.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    def _handle(signum, _frame):
        log.info("Signal %s received, finishing current cycle", signum)
        _stop.set()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _handle)

    if not wait_for_schema():
        log.error("Database schema is not at the expected revision; worker exits")
        return 2
    log.info("Worker started (interval %ss, scanner=%s)", args.interval, s.evidence_scanner)
    while True:
        started = time.monotonic()
        try:
            ok = cycle()
        except Exception:  # noqa: BLE001 - e.g. database temporarily unreachable
            log.exception("Worker cycle crashed; retrying next cycle")
            ok = False
        if args.once:
            return 0 if ok else 1
        _stop.wait(max(args.interval - (time.monotonic() - started), 1))
        if _stop.is_set():
            log.info("Worker stopped")
            return 0


if __name__ == "__main__":
    sys.exit(main())
