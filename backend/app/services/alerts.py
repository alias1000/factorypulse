"""Scheduled Action Center alert evaluation.

Dashboard requests are READ-ONLY: they show the alert table as last written by this job. The job runs
in the separate worker process (``python -m app.worker``) on a fixed schedule, can be triggered by an
administrator ("Evaluate now") and runs once after a demo reload. Deduplication (one open alert per
rule/subject), acknowledgement and automatic resolution are implemented in
``action_center.evaluate_rules`` and are unchanged.
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import ActionCenterAlert, Company, utcnow
from . import action_center as AC
from . import jobs

log = logging.getLogger("factorypulse.alerts")
JOB = "alerts.evaluate"


def evaluate_company(db: Session, company_id: int, include_repeated: bool = True) -> dict:
    from .settings_store import get_setting

    rep = AC.repeated_anomaly_conditions(db, company_id, get_setting(db, company_id, "anomaly_config")) \
        if include_repeated else None
    open_alerts = AC.evaluate_rules(db, company_id, rep)
    by_rule: dict[str, int] = {}
    for a in open_alerts:
        by_rule[a.rule] = by_rule.get(a.rule, 0) + 1
    return {"open_alerts": len(open_alerts), "by_rule": by_rule, "repeated_evaluated": include_repeated}


def run_alert_evaluation(company_id: int | None = None, *, trigger: str = "scheduler", include_repeated: bool = True,
                         **kw) -> list:
    """Evaluate the rules for one company (or all). Returns the JobRun of each company."""
    from ..db import SessionLocal

    sf = kw.pop("session_factory", None) or SessionLocal
    with sf() as db:
        ids = [company_id] if company_id else list(db.scalars(select(Company.id).order_by(Company.id)))
    return [jobs.run_job(JOB, cid, lambda db, cid=cid: evaluate_company(db, cid, include_repeated), trigger=trigger,
                         session_factory=sf, **kw) for cid in ids]


def status(db: Session, company_id: int) -> dict:
    """Freshness of the alert table for the dashboard."""
    interval = get_settings().alert_interval_seconds
    ok = jobs.last_success(db, JOB, company_id)
    last = jobs.latest(db, JOB, company_id)
    evaluated_at = ok.finished_at if ok else None
    if evaluated_at is not None and evaluated_at.tzinfo is None:
        evaluated_at = evaluated_at.replace(tzinfo=dt.timezone.utc)
    if evaluated_at is None:
        state = "never"
    elif (utcnow() - evaluated_at).total_seconds() > 3 * interval:
        state = "stale"
    else:
        state = "ok"
    if last is not None and last.status == "failed" and (ok is None or last.id > ok.id):
        state = "failed"
    return {"evaluated_at": evaluated_at.isoformat() if evaluated_at else None, "state": state,
            "interval_seconds": interval, "last_error": last.error if state == "failed" else None}


def open_alerts(db: Session, company_id: int) -> list[ActionCenterAlert]:
    return list(db.scalars(select(ActionCenterAlert).where(ActionCenterAlert.company_id == company_id,
                                                           ActionCenterAlert.resolved_at.is_(None))))


def open_alerts_for_tests() -> list[ActionCenterAlert]:
    from ..db import SessionLocal

    with SessionLocal() as db:
        rows = list(db.scalars(select(ActionCenterAlert).where(ActionCenterAlert.resolved_at.is_(None))))
        for r in rows:
            db.expunge(r)
        return rows
