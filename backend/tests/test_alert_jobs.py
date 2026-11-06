"""v0.3.1 regression: alert evaluation is a scheduled background job, separate from read-only
dashboard requests; it deduplicates, keeps acknowledgements, resolves automatically, is safe under
concurrency and logs/retries failures."""
import logging
import threading

import pytest
from sqlalchemy import func, select

from app.db import SessionLocal, engine
from app.models import ActionCenterAlert, JobRun
from app.services import alerts as AL
from app.services import jobs


def _counts():
    with SessionLocal() as db:
        return (db.scalar(select(func.count(ActionCenterAlert.id))), db.scalar(select(func.count(JobRun.id))),
                db.scalar(select(func.max(ActionCenterAlert.last_seen_at))))


def _open(db=None):
    with SessionLocal() as db:
        return {(a.rule, a.subject_key): a for a in db.scalars(select(ActionCenterAlert).where(
            ActionCenterAlert.resolved_at.is_(None)))}


def test_dashboard_requests_never_write_alerts(client, as_role, demo):
    before = _counts()
    for role in ("admin", "analyst", "viewer"):
        c = client if role == "admin" else as_role(role)
        for params in ({}, {"severity": "critical"}, {"include_repeated": False}):
            assert c.get("/api/action-center/summary", params=params).status_code == 200
    assert _counts() == before  # no new alerts, no job runs, no last_seen updates


def test_summary_reports_evaluation_freshness(client, demo):
    s = client.get("/api/action-center/summary").json()
    ev = s["alert_evaluation"]
    assert ev["state"] == "ok" and ev["evaluated_at"] and ev["interval_seconds"] == 300  # demo load evaluated


def test_worker_cycle_dedupes_keeps_acknowledgement_and_resolves(client, demo, analyst_id):
    from app.worker import cycle

    assert cycle() is True
    first = _open()
    assert ("critical_unassigned" in {k[0] for k in first})
    unassigned_key = next(k for k in first if k[0] == "critical_unassigned")
    ack_key = next(k for k in first if k[0] == "action_overdue")
    client.post(f"/api/action-center/alerts/{first[ack_key].id}/acknowledge").raise_for_status()

    assert cycle() is True                                # second run: same rows, no duplicates
    second = _open()
    assert {k: a.id for k, a in first.items()} == {k: a.id for k, a in second.items()}
    assert second[ack_key].acknowledged_at is not None    # acknowledgement survives re-evaluation
    assert second[unassigned_key].first_detected_at == first[unassigned_key].first_detected_at

    inv_id = first[unassigned_key].investigation_id       # condition disappears → resolved automatically
    client.patch(f"/api/investigations/{inv_id}", json={"owner_id": analyst_id}).raise_for_status()
    assert cycle() is True
    assert unassigned_key not in _open()
    with SessionLocal() as db:
        assert db.get(ActionCenterAlert, first[unassigned_key].id).resolved_at is not None
        runs = db.scalars(select(JobRun).where(JobRun.job == AL.JOB).order_by(JobRun.id.desc()).limit(3)).all()
        assert all(r.status == "succeeded" and r.trigger == "scheduler" and r.duration_ms is not None for r in runs)


def test_failed_job_is_logged_retried_and_rolled_back(demo, caplog):
    calls, sleeps = [], []

    def body(db):
        calls.append(1)
        db.add(ActionCenterAlert(company_id=1, rule="test_rule", subject_key=f"x:{len(calls)}", severity="low"))
        db.flush()
        if len(calls) < 3:
            raise RuntimeError(f"transient failure {len(calls)}")
        return {"ok": True}

    with caplog.at_level(logging.ERROR, logger="factorypulse.jobs"):
        run = jobs.run_job("test.flaky", 1, body, max_attempts=3, retry_base_seconds=2, sleep=sleeps.append)
    assert run.status == "succeeded" and run.attempt == 3 and sleeps == [2, 4]   # exponential backoff
    assert sum("transient failure" in r.getMessage() for r in caplog.records) == 2
    with SessionLocal() as db:
        runs = db.scalars(select(JobRun).where(JobRun.job == "test.flaky").order_by(JobRun.id)).all()
        assert [r.status for r in runs] == ["failed", "failed", "succeeded"]
        assert "RuntimeError: transient failure 1" in runs[0].error
        # failed attempts were rolled back completely: only the successful attempt's row exists
        subjects = set(db.scalars(select(ActionCenterAlert.subject_key).where(ActionCenterAlert.rule == "test_rule")))
        assert subjects == {"x:3"}
        db.query(ActionCenterAlert).filter(ActionCenterAlert.rule == "test_rule").delete()
        db.commit()


def test_permanently_failing_job_is_recorded_and_worker_survives(demo, caplog, monkeypatch):
    from app import worker

    def boom(*a, **k):
        raise ConnectionError("rule source unavailable")
    monkeypatch.setattr(AL, "evaluate_company", boom)
    monkeypatch.setattr(jobs.time, "sleep", lambda s: None)
    with caplog.at_level(logging.ERROR):
        assert worker.cycle() is False           # cycle reports failure but does not raise
    assert any("failed after 3 attempts" in r.getMessage() for r in caplog.records)
    with SessionLocal() as db:
        last = jobs.latest(db, AL.JOB, 1)
        assert last.status == "failed" and "ConnectionError" in last.error and last.attempt == 3
        assert AL.status(db, 1)["state"] == "failed"
    monkeypatch.undo()
    assert worker.cycle() is True                # recovers on the next cycle
    with SessionLocal() as db:
        assert AL.status(db, 1)["state"] == "ok"


@pytest.mark.skipif(engine.dialect.name != "postgresql", reason="advisory locks are PostgreSQL-specific")
def test_concurrent_runs_are_serialised_by_lock(demo):
    started, release = threading.Event(), threading.Event()
    results = {}

    def slow(db):
        started.set()
        release.wait(10)
        return {}

    t = threading.Thread(target=lambda: results.setdefault("a", jobs.run_job("test.lock", 1, slow, max_attempts=1)))
    t.start()
    assert started.wait(10)
    results["b"] = jobs.run_job("test.lock", 1, lambda db: {}, max_attempts=1)   # while A holds the lock
    release.set()
    t.join(10)
    assert results["a"].status == "succeeded" and results["b"].status == "skipped"


def test_parallel_evaluations_never_duplicate_alerts(demo):
    """Several workers + a manual trigger at the same moment: still exactly one open alert per subject."""
    barrier = threading.Barrier(6)
    out = []

    def go():
        barrier.wait()
        out.extend(AL.run_alert_evaluation(1, trigger="parallel-test", max_attempts=3, retry_base_seconds=0.05))

    threads = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in threads]
    [t.join(60) for t in threads]
    assert any(r.status == "succeeded" for r in out)
    assert all(r.status in ("succeeded", "skipped") for r in out), [(r.status, r.error) for r in out]
    with SessionLocal() as db:
        dupes = db.execute(select(ActionCenterAlert.rule, ActionCenterAlert.subject_key, func.count())
                           .where(ActionCenterAlert.resolved_at.is_(None))
                           .group_by(ActionCenterAlert.rule, ActionCenterAlert.subject_key)
                           .having(func.count() > 1)).all()
    assert dupes == []


def test_evaluate_now_and_job_history_are_admin_only(client, as_role, demo):
    for role in ("analyst", "viewer"):
        c = as_role(role)
        assert c.post("/api/action-center/alerts/evaluate").status_code == 403
        assert c.get("/api/action-center/jobs").status_code == 403
    r = client.post("/api/action-center/alerts/evaluate")
    assert r.status_code == 200 and r.json()["run"]["trigger"].startswith("manual:")
    jobs_list = client.get("/api/action-center/jobs").json()
    assert jobs_list[0]["job"] == AL.JOB and jobs_list[0]["status"] == "succeeded"
    log = client.get("/api/audit-log", params={"event": "alerts.evaluated_manually"}).json()
    assert log and log[0]["user_email"]


def test_readiness_reports_worker_state(as_role, demo, frozen_clock):
    """Relative to the last successful evaluation (independent of other tests that ran with a moved clock)."""
    import datetime as dt

    from app.db import SessionLocal

    with SessionLocal() as db:
        last = dt.datetime.fromisoformat(AL.status(db, 1)["evaluated_at"])

    def check():
        c = as_role("admin")                          # session created at the frozen time
        return next(x for x in c.get("/api/admin/system").json()["checks"] if x["name"] == "alert_worker")
    frozen_clock.set(last + dt.timedelta(seconds=60))
    assert check()["status"] == "ok"
    frozen_clock.set(last + dt.timedelta(seconds=3 * 300 + 60))   # no run for > 3 intervals
    c = check()
    assert c["status"] == "warn" and "older than 3 intervals" in c["detail"]


def test_abandoned_runs_are_marked(demo, frozen_clock):
    import datetime as dt

    from app import clock
    from app.models import utcnow

    frozen_clock.set(clock._system_now())
    with SessionLocal() as db:
        db.add(JobRun(job="test.killed", company_id=1, status="running", trigger="t", attempt=1, max_attempts=1,
                      started_at=utcnow() - dt.timedelta(hours=2)))
        db.commit()
        assert jobs.mark_abandoned(db) >= 1
        db.commit()
        assert db.scalar(select(JobRun.status).where(JobRun.job == "test.killed")) == "abandoned"


def test_worker_once_exit_code(demo):
    from app import worker
    assert worker.main(["--once"]) == 0
