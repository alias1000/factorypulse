"""Action Center domain logic: workflow, authorization rules, timeline, deadlines, alert rules,
before/after KPI comparison and dashboard summary.

All workflow rules are enforced here (server-side); the UI only mirrors them.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    ActionCenterAlert,
    CorrectiveAction,
    Investigation,
    InvestigationAnomalyLink,
    InvestigationEvent,
    Machine,
    ProductionLine,
    User,
    utcnow,
)
from .kpi import compute_kpis
from .settings_store import audit, get_setting

# ----------------------------------------------------------------------------- constants

OPEN_STATUSES = ("open", "in_progress", "pending_verification", "reopened")
DONE_STATUSES = ("resolved", "closed")
ACTIVE_ACTION_STATUSES = ("open", "in_progress")
FINISHED_ACTION_STATUSES = ("done", "verified", "cancelled")

# Investigation workflow. Value = (allowed targets, admin-only targets).
TRANSITIONS: dict[str, set[str]] = {
    "open": {"in_progress", "closed"},
    "in_progress": {"pending_verification", "open", "closed"},
    "pending_verification": {"resolved", "in_progress"},
    "resolved": {"closed", "reopened"},
    "closed": {"reopened"},
    "reopened": {"in_progress", "closed"},
}
ADMIN_ONLY_TARGETS = {"closed", "reopened"}

# Corrective-action workflow
ACTION_TRANSITIONS: dict[str, set[str]] = {
    "open": {"in_progress", "done", "cancelled"},
    "in_progress": {"open", "done", "cancelled"},
    "done": {"verified", "in_progress"},
    "verified": {"in_progress"},  # admin only: re-open after a failed later check
    "cancelled": {"open"},  # admin only
}
ADMIN_ONLY_ACTION_FROM = {"verified", "cancelled"}

DEFAULT_RULES: dict[str, Any] = {
    "critical_unassigned_hours": 4,  # critical investigation without owner for longer than X h
    "critical_open_hours": 48,  # critical investigation not resolved after X h
    "pending_verification_days": 7,  # stuck in pending verification
    "action_overdue_grace_days": 0,  # action counts as overdue X days after its due date
    "repeated_anomaly_count": 3,  # >= N anomaly days on the same machine ...
    "repeated_anomaly_window_days": 30,  # ... within the last N days of data
    "default_due_days": {"critical": 2, "high": 5, "medium": 10, "low": 20},
}


class WorkflowError(ValueError):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code = code
        self.status = status


def tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def today() -> dt.date:
    """Business date in the company timezone, from the injectable clock (never the host date)."""
    from ..clock import local_today

    return local_today()


def rules(db: Session, company_id: int) -> dict[str, Any]:
    stored = get_setting(db, company_id, "action_center_rules") or {}
    merged = {**DEFAULT_RULES, **stored}
    merged["default_due_days"] = {**DEFAULT_RULES["default_due_days"], **(stored.get("default_due_days") or {})}
    return merged


# ----------------------------------------------------------------------------- permissions

def can_edit_investigation(user: User, inv: Investigation) -> bool:
    """Admins: everything. Analysts: investigations they created or own. Viewers: never."""
    if user.role == "admin":
        return True
    if user.role != "analyst":
        return False
    return inv.owner_id == user.id or inv.created_by_id == user.id


def can_contribute(user: User, inv: Investigation) -> bool:
    """Comments / evidence / proposing actions: any analyst or admin while the case is not closed."""
    return user.role in ("analyst", "admin") and inv.status != "closed"


def can_update_action(user: User, action: CorrectiveAction) -> bool:
    if user.role == "admin":
        return True
    if user.role != "analyst":
        return False
    return action.owner_id == user.id or can_edit_investigation(user, action.investigation)


def assignable_user(db: Session, company_id: int, user_id: int | None) -> User | None:
    if user_id is None:
        return None
    u = db.get(User, user_id)
    if u is None or u.company_id != company_id or not u.is_active or u.role not in ("analyst", "admin"):
        raise WorkflowError("invalid_assignee", "Only active analysts or administrators can be assigned.")
    return u


# ----------------------------------------------------------------------------- numbering & timeline

def next_number(db: Session, company_id: int, year: int) -> str:
    prefix = f"INV-{year}-"
    last = db.scalar(select(func.max(Investigation.number)).where(
        Investigation.company_id == company_id, Investigation.number.like(f"{prefix}%")))
    n = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{n:04d}"


def actor_label(user: User | None) -> str:
    return f"{user.display_name} <{user.email}>" if user else "System"


def add_event(db: Session, inv: Investigation, event_type: str, user: User | None, data: dict | None = None,
              at: dt.datetime | None = None) -> InvestigationEvent:
    ev = InvestigationEvent(company_id=inv.company_id, investigation_id=inv.id, event_type=event_type,
                            actor_id=user.id if user else None, actor_label=actor_label(user), data=data,
                            created_at=at or utcnow())
    db.add(ev)
    inv.updated_at = at or utcnow()
    return ev


def default_due_date(db: Session, company_id: int, severity: str, start: dt.date | None = None) -> dt.date:
    days = int(rules(db, company_id)["default_due_days"].get(severity, 10))
    return (start or today()) + dt.timedelta(days=days)


# ----------------------------------------------------------------------------- workflow

def transition(db: Session, inv: Investigation, user: User, to_status: str, note: str | None,
               resolution_summary: str | None) -> None:
    frm = inv.status
    if to_status not in TRANSITIONS.get(frm, set()):
        raise WorkflowError("invalid_transition", f"Transition {frm} → {to_status} is not allowed.")
    if to_status in ADMIN_ONLY_TARGETS and user.role != "admin":
        raise WorkflowError("forbidden", "Only administrators can close or reopen investigations.", 403)
    if to_status not in ADMIN_ONLY_TARGETS and not can_edit_investigation(user, inv):
        raise WorkflowError("forbidden", "Only the owner, the creator or an administrator can change the status.", 403)
    if to_status == "pending_verification":
        if not inv.actions:
            raise WorkflowError("actions_required", "Add at least one corrective action before requesting verification.")
        active = [a.id for a in inv.actions if a.status in ACTIVE_ACTION_STATUSES]
        if active:
            raise WorkflowError("actions_open", f"{len(active)} corrective action(s) are still open or in progress.")
    if to_status in ("resolved", "closed"):
        summary = (resolution_summary or inv.resolution_summary or "").strip()
        if len(summary) < 10:
            raise WorkflowError("resolution_required", "A documented outcome (resolution summary, ≥ 10 characters) is required.")
        inv.resolution_summary = summary
    if to_status == "reopened" and not (note and note.strip()):
        raise WorkflowError("reason_required", "A reason is required to reopen an investigation.")
    now = utcnow()
    inv.status = to_status
    inv.status_changed_at = now
    if to_status == "resolved":
        inv.resolved_at = now
    if to_status == "closed":
        inv.closed_at = now
        inv.resolved_at = inv.resolved_at or now
    if to_status == "reopened":
        inv.reopen_count += 1
        inv.resolved_at = None
        inv.closed_at = None
    add_event(db, inv, "status_changed", user, {"from": frm, "to": to_status, "note": (note or "").strip() or None,
                                                "resolution_summary": inv.resolution_summary if to_status in ("resolved", "closed") else None})
    audit(db, "investigation.status_changed", inv.company_id, "investigation", inv.id,
          {"number": inv.number, "from": frm, "to": to_status})


def action_transition(db: Session, action: CorrectiveAction, user: User, to_status: str, notes: str | None,
                      verification_result: str | None, verification_notes: str | None) -> None:
    frm = action.status
    if to_status not in ACTION_TRANSITIONS.get(frm, set()):
        raise WorkflowError("invalid_transition", f"Action transition {frm} → {to_status} is not allowed.")
    if frm in ADMIN_ONLY_ACTION_FROM and user.role != "admin":
        raise WorkflowError("forbidden", "Only administrators can reopen verified or cancelled actions.", 403)
    inv = action.investigation
    if to_status == "verified":
        # effectiveness is judged by the investigation owner or an administrator, not by the implementer alone
        if not (user.role == "admin" or inv.owner_id == user.id):
            raise WorkflowError("forbidden", "Only the investigation owner or an administrator can verify actions.", 403)
        if verification_result not in ("effective", "partially_effective", "not_effective"):
            raise WorkflowError("verification_required", "A verification result is required.")
        action.verification_result = verification_result
        action.verification_notes = (verification_notes or "").strip() or None
        action.verified_at = utcnow()
        action.verified_by_id = user.id
    if to_status == "done":
        if not (notes and notes.strip()):
            raise WorkflowError("notes_required", "Completion notes are required.")
        action.completion_notes = notes.strip()
        action.completed_at = utcnow()
    if to_status in ("open", "in_progress") and frm in ("done", "verified"):
        action.completed_at = None
        action.verification_result = None
        action.verified_at = None
        action.verified_by_id = None
    action.status = to_status
    action.updated_at = utcnow()
    ev = {"action_id": action.id, "title": action.title, "from": frm, "to": to_status}
    if to_status == "done":
        ev["notes"] = action.completion_notes
    if to_status == "verified":
        ev["verification_result"] = verification_result
        ev["verification_notes"] = action.verification_notes
    add_event(db, inv, "action_verified" if to_status == "verified" else "action_status_changed", user, ev)
    audit(db, "action.status_changed", inv.company_id, "corrective_action", action.id,
          {"investigation": inv.number, "from": frm, "to": to_status})


# ----------------------------------------------------------------------------- overdue helpers

def is_overdue_inv(inv: Investigation, ref: dt.date | None = None) -> bool:
    return inv.status in OPEN_STATUSES and inv.due_date is not None and inv.due_date < (ref or today())


def is_overdue_action(a: CorrectiveAction, grace_days: int = 0, ref: dt.date | None = None) -> bool:
    return (a.status in ACTIVE_ACTION_STATUSES and a.due_date is not None
            and a.due_date + dt.timedelta(days=grace_days) < (ref or today()))


def days_overdue(due: dt.date | None, ref: dt.date | None = None) -> int | None:
    if due is None:
        return None
    return max(((ref or today()) - due).days, 0)


# ----------------------------------------------------------------------------- alert rules

def _aware(d: dt.datetime) -> dt.datetime:
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def evaluate_rules(db: Session, company_id: int, repeated_anomalies: list[dict] | None = None) -> list[ActionCenterAlert]:
    """Re-evaluate all rules and synchronise the persistent alert table:
    new conditions → new alert (first_detected_at), still-active → last_seen_at, vanished → resolved_at.
    Returns the unresolved alerts."""
    r = rules(db, company_id)
    now = utcnow()
    ref = today()
    active: dict[tuple[str, str], dict] = {}

    invs = db.scalars(select(Investigation).where(Investigation.company_id == company_id,
                                                  Investigation.status.in_(OPEN_STATUSES))).all()
    for inv in invs:
        age_h = (now - _aware(inv.created_at)).total_seconds() / 3600
        if inv.severity == "critical" and inv.owner_id is None and age_h >= r["critical_unassigned_hours"]:
            active[("critical_unassigned", f"inv:{inv.id}")] = {
                "severity": "critical", "investigation_id": inv.id,
                "details": {"number": inv.number, "title": inv.title, "age_hours": round(age_h, 1),
                            "threshold_hours": r["critical_unassigned_hours"]}}
        if inv.severity == "critical" and age_h >= r["critical_open_hours"]:
            active[("critical_open_too_long", f"inv:{inv.id}")] = {
                "severity": "high", "investigation_id": inv.id,
                "details": {"number": inv.number, "title": inv.title, "age_hours": round(age_h, 1),
                            "threshold_hours": r["critical_open_hours"]}}
        if inv.status == "pending_verification":
            waited = (now - _aware(inv.status_changed_at)).total_seconds() / 86400
            if waited >= r["pending_verification_days"]:
                active[("pending_verification_too_long", f"inv:{inv.id}")] = {
                    "severity": "medium", "investigation_id": inv.id,
                    "details": {"number": inv.number, "title": inv.title, "days": round(waited, 1),
                                "threshold_days": r["pending_verification_days"]}}
        if is_overdue_inv(inv, ref):
            active[("investigation_overdue", f"inv:{inv.id}")] = {
                "severity": "high" if inv.severity in ("critical", "high") else "medium", "investigation_id": inv.id,
                "details": {"number": inv.number, "title": inv.title, "due_date": inv.due_date.isoformat(),
                            "days_overdue": days_overdue(inv.due_date, ref)}}

    acts = db.scalars(select(CorrectiveAction).join(Investigation).where(
        CorrectiveAction.company_id == company_id, CorrectiveAction.status.in_(ACTIVE_ACTION_STATUSES))).all()
    for a in acts:
        if is_overdue_action(a, int(r["action_overdue_grace_days"]), ref):
            active[("action_overdue", f"act:{a.id}")] = {
                "severity": "high" if a.priority in ("critical", "high") else "medium",
                "investigation_id": a.investigation_id, "action_id": a.id,
                "details": {"number": a.investigation.number, "title": a.title, "due_date": a.due_date.isoformat(),
                            "days_overdue": days_overdue(a.due_date, ref),
                            "owner": a.owner.display_name if a.owner else None}}

    for rep in repeated_anomalies or []:
        active[("repeated_anomalies", f"machine:{rep['machine_id']}")] = {
            "severity": "high", "machine_id": rep["machine_id"],
            "details": {k: v for k, v in rep.items() if k != "machine_id"}}

    existing = {(a.rule, a.subject_key): a for a in db.scalars(select(ActionCenterAlert).where(
        ActionCenterAlert.company_id == company_id, ActionCenterAlert.resolved_at.is_(None)))}
    for key, spec in active.items():
        row = existing.get(key)
        if row is None:
            row = ActionCenterAlert(company_id=company_id, rule=key[0], subject_key=key[1], first_detected_at=now,
                                    last_seen_at=now, severity=spec["severity"], details=spec["details"],
                                    investigation_id=spec.get("investigation_id"), action_id=spec.get("action_id"),
                                    machine_id=spec.get("machine_id"))
            db.add(row)
            existing[key] = row
        else:
            row.last_seen_at, row.details, row.severity = now, spec["details"], spec["severity"]
    for key, row in existing.items():
        if key not in active:
            if repeated_anomalies is None and key[0] == "repeated_anomalies":
                continue  # repeated-anomaly rule not evaluated in this run: keep its state
            row.resolved_at = now
    db.flush()
    return [row for key, row in existing.items() if row.resolved_at is None]


def repeated_anomaly_conditions(db: Session, company_id: int, cfg: dict) -> list[dict]:
    """Machines with >= N anomaly days within the last N days of available production data."""
    from ..services.anomaly import detect
    from .data_access import Filters, data_date_range, load_records

    r = rules(db, company_id)
    _, end, _ = data_date_range(db, company_id)
    if end is None:
        return []
    window = int(r["repeated_anomaly_window_days"])
    start = end - dt.timedelta(days=window - 1)
    records = load_records(db, company_id, Filters(start - dt.timedelta(days=60), end))
    res = detect(records, {**cfg, "levels": ["machine"]})
    if res["status"] != "ok":
        return []
    days: dict[int, set[str]] = defaultdict(set)
    codes: dict[int, str] = {}
    for a in res["anomalies"]:
        if a["date"] >= start.isoformat():
            days[a["entity_id"]].add(a["date"])
            codes[a["entity_id"]] = a["entity_code"]
    n = int(r["repeated_anomaly_count"])
    return [{"machine_id": mid, "machine_code": codes[mid], "anomaly_days": len(d), "threshold": n,
             "window_days": window, "window_start": start.isoformat(), "window_end": end.isoformat(),
             "last_anomaly": max(d)} for mid, d in sorted(days.items()) if len(d) >= n]


# ----------------------------------------------------------------------------- before / after KPIs

COMPARISON_METRICS = [
    # key, kpi source key, kind
    ("oee", "oee", "ratio"),
    ("availability", "availability", "ratio"),
    ("performance", "performance", "ratio"),
    ("quality", "quality", "ratio"),
    ("defect_rate", "defect_rate", "ratio"),
    ("unplanned_downtime_minutes", "unplanned_downtime_minutes", "minutes"),
    ("total_production", "total_production", "units"),
]
LOWER_IS_BETTER = {"defect_rate", "unplanned_downtime_minutes"}
MIN_PRODUCTION_DAYS = 3


def kpi_comparison(records_before: pd.DataFrame, records_after: pd.DataFrame) -> dict[str, Any]:
    """Observed before/after values. Counts (downtime, output) are also normalised per production day,
    because the two periods may contain different numbers of production days."""
    kb, ka = compute_kpis(records_before), compute_kpis(records_after)
    days_b = int(records_before["date"].nunique()) if not records_before.empty else 0
    days_a = int(records_after["date"].nunique()) if not records_after.empty else 0
    sufficient = days_b >= MIN_PRODUCTION_DAYS and days_a >= MIN_PRODUCTION_DAYS
    rows = []
    for key, src, kind in COMPARISON_METRICS:
        b, a = kb[src]["value"], ka[src]["value"]
        if not sufficient:
            b = a = None
        row: dict[str, Any] = {"metric": key, "kind": kind, "before": b, "after": a,
                               "coverage_before": kb[src]["coverage"], "coverage_after": ka[src]["coverage"],
                               "records_before": kb[src]["records_used"], "records_after": ka[src]["records_used"],
                               "lower_is_better": key in LOWER_IS_BETTER}
        if kind != "ratio":
            row["before_per_day"] = b / days_b if b is not None and days_b else None
            row["after_per_day"] = a / days_a if a is not None and days_a else None
            cb, ca = row["before_per_day"], row["after_per_day"]
        else:
            cb, ca = b, a
        row["abs_diff"] = None if cb is None or ca is None else ca - cb
        # relative change only where meaningful (non-zero, non-tiny baseline)
        row["pct_change"] = None if row["abs_diff"] is None or not cb or abs(cb) < 1e-9 else row["abs_diff"] / abs(cb)
        if row["abs_diff"] is None or abs(row["abs_diff"]) < 1e-12:
            row["direction"] = None if row["abs_diff"] is None else "unchanged"
        else:
            better = (row["abs_diff"] < 0) if key in LOWER_IS_BETTER else (row["abs_diff"] > 0)
            row["direction"] = "better" if better else "worse"
        rows.append(row)
    return {
        "metrics": rows,
        "production_days_before": days_b,
        "production_days_after": days_a,
        "sufficient_data": sufficient,
        "min_production_days": MIN_PRODUCTION_DAYS,
        "disclaimer": ("Observed difference between two periods. It is not proof that a corrective action caused the "
                       "change; other factors (product mix, staffing, demand, data quality) may contribute."),
    }


def daily_series(records: pd.DataFrame) -> list[dict]:
    from .kpi import aggregate, to_jsonable

    if records.empty:
        return []
    g = aggregate(records, ["date"])[["date", "oee", "availability", "performance", "quality", "defect_rate",
                                      "unplanned_downtime_minutes", "actual_qty"]]
    return to_jsonable(g)


def default_comparison_periods(db: Session, inv: Investigation) -> dict[str, dt.date]:
    """Baseline: 14 days up to the anomaly date (or creation date). Comparison: 14 days after the last
    completed action (or resolution / status change), capped at the available data."""
    from .data_access import data_date_range

    _, data_end, _ = data_date_range(db, inv.company_id)
    link = inv.anomaly_links[0] if inv.anomaly_links else None
    anchor = link.anomaly_date if link else _aware(inv.created_at).astimezone(tz()).date()
    completed = [a.completed_at for a in inv.actions if a.completed_at]
    after_anchor = max([_aware(c) for c in completed]).astimezone(tz()).date() if completed else (
        _aware(inv.resolved_at).astimezone(tz()).date() if inv.resolved_at else anchor + dt.timedelta(days=1))
    after_anchor = max(after_anchor, anchor + dt.timedelta(days=1))
    out = {"baseline_start": anchor - dt.timedelta(days=13), "baseline_end": anchor,
           "comparison_start": after_anchor, "comparison_end": after_anchor + dt.timedelta(days=13)}
    if data_end and out["comparison_end"] > data_end:
        out["comparison_end"] = max(data_end, out["comparison_start"])
    return out


# ----------------------------------------------------------------------------- summary

def summary(db: Session, company_id: int, conds: list, alerts: list[ActionCenterAlert]) -> dict[str, Any]:
    """Dashboard figures computed with SQL aggregates over the filtered investigations (``conds`` are SQL
    WHERE conditions incl. the company). Read-only: alerts come from the scheduled evaluation job."""
    from sqlalchemy import and_, case
    from sqlalchemy.orm import selectinload

    from ..clock import local_day_bounds

    I, A = Investigation, CorrectiveAction
    ref = today()
    zone = tz()
    open_c = [*conds, I.status.in_(OPEN_STATUSES)]

    def count(*extra) -> int:
        return db.scalar(select(func.count(I.id)).where(*conds, *extra)) or 0

    def grouped(col, where) -> dict:
        return dict(db.execute(select(col, func.count(I.id)).where(*where).group_by(col)).all())

    # calendar month in the company timezone: [first day 00:00, first day of next month 00:00)
    month_start = local_day_bounds(ref.replace(day=1))[0]
    nxt = (ref.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    month_end = local_day_bounds(nxt)[0]

    resolved_q = select(I.created_at, I.resolved_at).where(*conds, I.resolved_at.is_not(None))
    if db.get_bind().dialect.name == "postgresql":
        avg_s, n_resolved = db.execute(select(func.avg(func.extract("epoch", I.resolved_at - I.created_at)),
                                              func.count(I.id)).where(*conds, I.resolved_at.is_not(None))).one()
        avg_h = float(avg_s) / 3600 if avg_s is not None else None
    else:  # SQLite (tests/dev): compute in Python
        pairs = db.execute(resolved_q).all()
        n_resolved = len(pairs)
        avg_h = (sum((_aware(r) - _aware(c)).total_seconds() for c, r in pairs) / 3600 / n_resolved) if pairs else None

    grace = int(rules(db, company_id)["action_overdue_grace_days"])
    overdue_a = [*conds, A.investigation_id == I.id, A.status.in_(ACTIVE_ACTION_STATUSES), A.due_date.is_not(None),
                 A.due_date < ref - dt.timedelta(days=grace)]
    overdue_actions = db.scalar(select(func.count(A.id)).where(*overdue_a)) or 0
    by_owner = db.execute(select(func.coalesce(User.display_name, "—"), func.count(A.id)).select_from(A).join(I, A.investigation_id == I.id)
                          .outerjoin(User, A.owner_id == User.id).where(*overdue_a)
                          .group_by(User.display_name).order_by(func.count(A.id).desc())).all()
    overdue_list = db.scalars(select(A).join(I, A.investigation_id == I.id).where(*overdue_a).order_by(A.due_date, A.id).limit(10)
                              .options(selectinload(A.investigation), selectinload(A.owner))).all()

    sev = grouped(I.severity, open_c)
    lines = dict(db.execute(select(ProductionLine.id, ProductionLine.code).where(ProductionLine.company_id == company_id)).all())
    by_line = grouped(I.line_id, open_c)
    by_cat = grouped(I.category, open_c)
    by_status = grouped(I.status, conds)

    # weekly opened vs resolved (last 12 ISO weeks, company timezone)
    monday = ref - dt.timedelta(days=ref.weekday())
    first = monday - dt.timedelta(weeks=11)
    window_start = local_day_bounds(first)[0]
    opened_ts = db.scalars(select(I.created_at).where(*conds, I.created_at >= window_start)).all()
    resolved_ts = db.scalars(select(I.resolved_at).where(*conds, I.resolved_at >= window_start)).all()

    def week_of(d) -> dt.date:
        local = _aware(d).astimezone(zone).date()
        return local - dt.timedelta(days=local.weekday())
    opened_w, resolved_w = Counter(map(week_of, opened_ts)), Counter(map(week_of, resolved_ts))
    weeks = []
    for k in range(11, -1, -1):
        ws = monday - dt.timedelta(weeks=k)
        end_excl = local_day_bounds(ws + dt.timedelta(days=6))[1]
        open_at_end = count(I.created_at < end_excl, (I.resolved_at.is_(None)) | (I.resolved_at >= end_excl))
        iso = ws.isocalendar()
        weeks.append({"week": f"{iso[0]}-W{iso[1]:02d}", "week_start": ws.isoformat(),
                      "opened": opened_w.get(ws, 0), "resolved": resolved_w.get(ws, 0), "open_at_end": open_at_end})

    overdue_first = case((and_(I.due_date.is_not(None), I.due_date < ref), 0), else_=1)
    sev_rank = case({"critical": 0, "high": 1, "medium": 2, "low": 3}, value=I.severity, else_=9)
    priority = db.scalars(select(I).where(*open_c).order_by(sev_rank, overdue_first, I.due_date.asc().nulls_last(), I.id)
                          .limit(8).options(selectinload(I.actions), selectinload(I.anomaly_links), selectinload(I.line),
                                            selectinload(I.machine), selectinload(I.owner), selectinload(I.created_by))).all()
    return {
        "generated_at": utcnow().astimezone(zone).isoformat(),
        "timezone": get_settings().timezone,
        "today": ref.isoformat(),
        "kpis": {
            "open_total": sum(sev.values()),
            "critical_open": sev.get("critical", 0),
            "overdue_actions": overdue_actions,
            "overdue_investigations": count(I.status.in_(OPEN_STATUSES), I.due_date < ref),
            "in_progress": by_status.get("in_progress", 0),
            "pending_verification": by_status.get("pending_verification", 0),
            "resolved_this_month": count(I.resolved_at >= month_start, I.resolved_at < month_end),
            "avg_resolution_hours": avg_h,
            "resolved_sample": n_resolved,
        },
        "by_severity": [{"severity": x, "count": sev.get(x, 0)} for x in ("critical", "high", "medium", "low")],
        "by_line": sorted(({"line_code": lines.get(lid, "?"), "count": n} for lid, n in by_line.items()),
                          key=lambda x: x["line_code"]),
        "by_category": sorted(({"category": c, "count": n} for c, n in by_cat.items()), key=lambda x: (-x["count"], x["category"])),
        "by_status": [{"status": x, "count": by_status.get(x, 0)}
                      for x in ("open", "in_progress", "pending_verification", "reopened", "resolved", "closed")],
        "trend": weeks,
        "overdue_by_owner": [{"owner": o, "count": n} for o, n in by_owner],
        "priority_issues": priority,
        "overdue_actions_list": overdue_list,
        "alerts": alerts,
    }


def link_for_anomaly(db: Session, company_id: int, keys: list[str]) -> dict[str, InvestigationAnomalyLink]:
    if not keys:
        return {}
    return {lk.anomaly_key: lk for lk in db.scalars(select(InvestigationAnomalyLink).where(
        InvestigationAnomalyLink.company_id == company_id, InvestigationAnomalyLink.anomaly_key.in_(keys)))}


def machine_belongs_to_line(db: Session, machine_id: int | None, line_id: int) -> None:
    if machine_id is None:
        return
    m = db.get(Machine, machine_id)
    if m is None or m.line_id != line_id:
        raise WorkflowError("machine_line_mismatch", "The machine does not belong to the selected production line.")
