"""Action Center API: investigations, corrective actions, comments, evidence, timeline,
before/after KPI comparison, rule-based alerts and dashboard summary.

Permissions (see services/auth.py):
  action.read   viewer+   read everything in the company, download evidence
  action.write  analyst+  create investigations, comment, upload evidence, propose actions;
                          edit investigations they own or created; update actions they own
  action.manage admin     everything, incl. assigning any user, close/reopen, rule thresholds
Fine-grained checks (owner/creator) live in services/action_center.py.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from ..config import get_settings
from ..db import get_db
from ..models import (
    ActionCenterAlert,
    CorrectiveAction,
    Investigation,
    InvestigationAnomalyLink,
    InvestigationAttachment,
    InvestigationComment,
    InvestigationEvent,
    Machine,
    ProductionLine,
    User,
    utcnow,
)
from ..services import action_center as AC
from ..services import alerts as AL
from ..services import evidence_scan as ES
from ..services import evidence_store as EStore
from ..services import jobs as JOBS
from ..services.data_access import Filters, get_company, load_records
from ..services.settings_store import audit, set_setting
from .admin import AnomalyConfig

log = logging.getLogger("factorypulse.action_center")
from .deps import Principal, require

router = APIRouter(prefix="/api", tags=["action-center"])

Category = Literal["equipment_failure", "production_loss", "quality_issue", "excessive_downtime",
                   "process_deviation", "safety_observation", "other"]
Severity = Literal["low", "medium", "high", "critical"]
InvStatus = Literal["open", "in_progress", "pending_verification", "resolved", "closed", "reopened"]
ActionStatus = Literal["open", "in_progress", "done", "verified", "cancelled"]
SEV_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
# severity as a sortable number in SQL (descending = most severe first)
SEV_SORT = case({"critical": 4, "high": 3, "medium": 2, "low": 1}, value=Investigation.severity, else_=0)


def inv_conditions(company_id: int, *, search: str | None = None, status=None, severity=None, category=None,
                   line_id: int | None = None, machine_id: int | None = None, owner_id: int | None = None,
                   unassigned: bool = False, overdue: bool = False, open_only: bool = False,
                   start_date: dt.date | None = None, end_date: dt.date | None = None) -> list:
    """SQL WHERE conditions shared by the investigation list and the dashboard summary.
    Date filters refer to the creation date in the company timezone (DST-correct day boundaries)."""
    from ..clock import local_day_bounds
    from ..services.search import ESCAPE, like_pattern

    I = Investigation
    as_list = (lambda v: [v] if isinstance(v, str) else list(v or []))
    c = [I.company_id == company_id]
    if search and search.strip():
        c.append(I.search_text.like(like_pattern(search), escape=ESCAPE))
    if as_list(status):
        c.append(I.status.in_(as_list(status)))
    if open_only:
        c.append(I.status.in_(AC.OPEN_STATUSES))
    if as_list(severity):
        c.append(I.severity.in_(as_list(severity)))
    if as_list(category):
        c.append(I.category.in_(as_list(category)))
    if line_id:
        c.append(I.line_id == line_id)
    if machine_id:
        c.append(I.machine_id == machine_id)
    if owner_id:
        c.append(I.owner_id == owner_id)
    if unassigned:
        c.append(I.owner_id.is_(None))
    if overdue:
        c += [I.status.in_(AC.OPEN_STATUSES), I.due_date < AC.today()]
    if start_date:
        c.append(I.created_at >= local_day_bounds(start_date)[0])
    if end_date:
        c.append(I.created_at < local_day_bounds(end_date)[1])
    return c

ALLOWED_EVIDENCE = {
    ".pdf": ("application/pdf", [b"%PDF-"]),
    ".png": ("image/png", [b"\x89PNG\r\n\x1a\n"]),
    ".jpg": ("image/jpeg", [b"\xff\xd8\xff"]),
    ".jpeg": ("image/jpeg", [b"\xff\xd8\xff"]),
    ".xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", [b"PK\x03\x04"]),
    ".csv": ("text/csv", []),
}


def _err(e: AC.WorkflowError) -> HTTPException:
    return HTTPException(e.status, {"code": e.code, "message": str(e)})


# ----------------------------------------------------------------------------- serialisation

def user_ref(u: User | None) -> dict | None:
    return {"id": u.id, "display_name": u.display_name, "email": u.email, "role": u.role, "is_active": u.is_active} if u else None


def _iso(d) -> str | None:
    return d.isoformat() if d else None


def inv_dict(inv: Investigation, me: User, detail: bool = False) -> dict:
    ref = AC.today()
    acts = inv.actions
    d = {
        "id": inv.id, "number": inv.number, "title": inv.title, "category": inv.category, "severity": inv.severity,
        "status": inv.status, "source": inv.source,
        "line": {"id": inv.line.id, "code": inv.line.code, "name": inv.line.name},
        "machine": {"id": inv.machine.id, "code": inv.machine.code, "name": inv.machine.name} if inv.machine else None,
        "owner": user_ref(inv.owner), "created_by": user_ref(inv.created_by),
        "created_by_label": inv.created_by.display_name if inv.created_by else ("Synthetic demo data" if inv.is_synthetic else "System"),
        "due_date": _iso(inv.due_date), "overdue": AC.is_overdue_inv(inv, ref),
        "days_overdue": AC.days_overdue(inv.due_date, ref) if AC.is_overdue_inv(inv, ref) else None,
        "created_at": _iso(inv.created_at), "updated_at": _iso(inv.updated_at),
        "status_changed_at": _iso(inv.status_changed_at), "resolved_at": _iso(inv.resolved_at),
        "closed_at": _iso(inv.closed_at), "reopen_count": inv.reopen_count, "is_synthetic": inv.is_synthetic,
        "actions_summary": {
            "total": len(acts), "active": sum(1 for a in acts if a.status in AC.ACTIVE_ACTION_STATUSES),
            "overdue": sum(1 for a in acts if AC.is_overdue_action(a, 0, ref)),
            "done": sum(1 for a in acts if a.status in ("done", "verified")),
        },
        "anomaly": _link_dict(inv.anomaly_links[0]) if inv.anomaly_links else None,
    }
    if detail:
        d.update({
            "description": inv.description, "resolution_summary": inv.resolution_summary,
            "allowed_transitions": allowed_transitions(inv, me),
            "permissions": {"can_edit": AC.can_edit_investigation(me, inv), "can_contribute": AC.can_contribute(me, inv),
                            "can_assign_any": me.role == "admin", "can_manage": me.role == "admin"},
            "actions": [action_dict(a, me) for a in acts],
            "anomaly_links": [_link_dict(lk, full=True) for lk in inv.anomaly_links],
        })
    return d


def allowed_transitions(inv: Investigation, me: User) -> list[str]:
    out = []
    for t in sorted(AC.TRANSITIONS.get(inv.status, set())):
        if t in AC.ADMIN_ONLY_TARGETS:
            if me.role == "admin":
                out.append(t)
        elif AC.can_edit_investigation(me, inv):
            out.append(t)
    return out


def _link_dict(lk: InvestigationAnomalyLink, full: bool = False) -> dict:
    d = {"anomaly_key": lk.anomaly_key, "date": lk.anomaly_date.isoformat(), "level": lk.level,
         "entity_code": lk.entity_code, "metric": lk.metric, "severity": lk.snapshot.get("severity"),
         "observed": lk.snapshot.get("observed"), "baseline": lk.snapshot.get("baseline")}
    if full:
        d["snapshot"] = lk.snapshot
        d["linked_at"] = _iso(lk.created_at)
    return d


def action_dict(a: CorrectiveAction, me: User) -> dict:
    inv = a.investigation
    ref = AC.today()
    allowed = []
    if AC.can_update_action(me, a):
        for t in sorted(AC.ACTION_TRANSITIONS.get(a.status, set())):
            if a.status in AC.ADMIN_ONLY_ACTION_FROM and me.role != "admin":
                continue
            if t == "verified" and not (me.role == "admin" or inv.owner_id == me.id):
                continue
            allowed.append(t)
    return {
        "id": a.id, "code": f"ACT-{a.id:05d}", "title": a.title, "description": a.description,
        "owner": user_ref(a.owner), "due_date": _iso(a.due_date), "priority": a.priority, "status": a.status,
        "overdue": AC.is_overdue_action(a, 0, ref), "days_overdue": AC.days_overdue(a.due_date, ref) if AC.is_overdue_action(a, 0, ref) else None,
        "completed_at": _iso(a.completed_at), "completion_notes": a.completion_notes,
        "verification_result": a.verification_result, "verification_notes": a.verification_notes,
        "verified_at": _iso(a.verified_at), "verified_by": user_ref(a.verified_by),
        "created_at": _iso(a.created_at), "updated_at": _iso(a.updated_at),
        "investigation": {"id": inv.id, "number": inv.number, "title": inv.title, "status": inv.status,
                          "severity": inv.severity, "line_code": inv.line.code,
                          "machine_code": inv.machine.code if inv.machine else None},
        "allowed_transitions": allowed,
        "can_edit": AC.can_edit_investigation(me, inv) and inv.status != "closed",
    }


def alert_dict(al: ActionCenterAlert) -> dict:
    return {"id": al.id, "rule": al.rule, "severity": al.severity, "investigation_id": al.investigation_id,
            "action_id": al.action_id, "machine_id": al.machine_id, "details": al.details,
            "first_detected_at": _iso(al.first_detected_at), "last_seen_at": _iso(al.last_seen_at),
            "acknowledged_at": _iso(al.acknowledged_at)}


def _load(db: Session, company_id: int, inv_id: int) -> Investigation:
    inv = db.scalar(select(Investigation).options(
        selectinload(Investigation.actions), selectinload(Investigation.anomaly_links)).where(
        Investigation.id == inv_id, Investigation.company_id == company_id))
    if inv is None:
        raise HTTPException(404, {"code": "not_found", "message": "Investigation not found"})
    return inv


def _not_closed(inv: Investigation) -> None:
    if inv.status == "closed":
        raise HTTPException(409, {"code": "closed", "message": "Closed investigations are read-only. Reopen it first."})


# ----------------------------------------------------------------------------- anomaly lookup

ANOMALY_KEY = re.compile(r"^(machine|line)-(.+)-(\d{8})-(defect_rate|downtime_ratio|target_achievement|performance)(-if)?$")
METRIC_CATEGORY = {"defect_rate": "quality_issue", "downtime_ratio": "excessive_downtime",
                   "target_achievement": "production_loss", "performance": "production_loss"}
METRIC_TITLE = {
    "en": {"defect_rate": "Defect rate deviation", "downtime_ratio": "Unusual unplanned downtime",
           "target_achievement": "Production drop", "performance": "Performance loss"},
    "de": {"defect_rate": "Abweichung Ausschussquote", "downtime_ratio": "Auffälliger ungeplanter Stillstand",
           "target_achievement": "Produktionseinbruch", "performance": "Leistungsverlust"},
}


def find_anomaly(db: Session, company_id: int, key: str, cfg: dict) -> dict:
    """Re-detect the anomaly server-side so the stored evidence is authentic (never trust client values)."""
    from ..services.anomaly import detect
    from ..services.data_access import load_defects, load_downtime

    m = ANOMALY_KEY.match(key)
    if not m:
        raise HTTPException(422, {"code": "invalid_anomaly", "message": "Unknown anomaly identifier."})
    level, code, day, _metric, is_if = m.groups()
    date = dt.datetime.strptime(day, "%Y%m%d").date()
    f = Filters(date - dt.timedelta(days=60), date)
    if not is_if:
        if level == "machine":
            mach = db.scalar(select(Machine).where(Machine.company_id == company_id, Machine.code == code))
            if mach is None:
                raise HTTPException(404, {"code": "not_found", "message": "Machine not found"})
            f.machine_ids = [mach.id]
        else:
            line = db.scalar(select(ProductionLine).where(ProductionLine.company_id == company_id, ProductionLine.code == code))
            if line is None:
                raise HTTPException(404, {"code": "not_found", "message": "Line not found"})
            f.line_ids = [line.id]
    cfg = {**cfg, "levels": [level], "use_isolation_forest": bool(is_if)}
    res = detect(load_records(db, company_id, f), cfg, load_downtime(db, company_id, f), load_defects(db, company_id, f))
    for a in res.get("anomalies", []):
        if a["id"] == key:
            return a
    raise HTTPException(422, {"code": "anomaly_not_reproducible",
                              "message": "This anomaly is not detected with the given settings and current data. "
                                         "Create the investigation manually instead."})


def _cfg(db: Session, company_id: int, override: AnomalyConfig | None) -> dict:
    from ..services.settings_store import get_setting
    return override.model_dump() if override else get_setting(db, company_id, "anomaly_config")


def anomaly_prefill(db: Session, company_id: int, a: dict, lang: str) -> dict:
    if a["level"] == "machine":
        mach = db.get(Machine, a["entity_id"])
        line_id, machine_id = mach.line_id, mach.id
    else:
        line_id, machine_id = a["entity_id"], None
    d = dt.date.fromisoformat(a["date"])
    date_txt = d.strftime("%d.%m.%Y") if lang == "de" else d.isoformat()
    title = f"{METRIC_TITLE[lang][a['metric']]} – {a['entity_code']} ({date_txt})"
    expl = a.get("explanation_de") if lang == "de" and a.get("explanation_de") else a["explanation"]
    note = ("Hinweis: Statistisches Signal aus der Anomalieerkennung. Die Ursache ist nicht festgestellt und muss "
            "untersucht werden." if lang == "de" else
            "Note: statistical signal from anomaly detection. The cause has not been established and must be investigated.")
    return {"title": title[:200], "description": f"{expl}\n\n{note}", "category": METRIC_CATEGORY[a["metric"]],
            "severity": a["severity"] if a["severity"] in SEV_RANK else "medium", "line_id": line_id,
            "machine_id": machine_id, "due_date": AC.default_due_date(db, company_id, a["severity"]).isoformat()}


class AnomalyRef(BaseModel):
    anomaly_id: str = Field(..., max_length=120)
    config: AnomalyConfig | None = None
    lang: Literal["de", "en"] = "en"


@router.post("/investigations/anomaly-prefill", summary="Re-detect an anomaly and return a proposed investigation")
def prefill_from_anomaly(body: AnomalyRef, p: Principal = Depends(require("action.write")), db: Session = Depends(get_db)):
    existing = db.scalar(select(InvestigationAnomalyLink).where(
        InvestigationAnomalyLink.company_id == p.company_id, InvestigationAnomalyLink.anomaly_key == body.anomaly_id))
    if existing:
        inv = db.get(Investigation, existing.investigation_id)
        return {"duplicate": {"id": inv.id, "number": inv.number, "title": inv.title, "status": inv.status}}
    a = find_anomaly(db, p.company_id, body.anomaly_id, _cfg(db, p.company_id, body.config))
    return {"duplicate": None, "anomaly": a, "proposal": anomaly_prefill(db, p.company_id, a, body.lang)}


# ----------------------------------------------------------------------------- investigations

class InvestigationCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    description: str = Field("", max_length=10000)
    category: Category
    severity: Severity
    line_id: int
    machine_id: int | None = None
    owner_id: int | None = None
    due_date: dt.date | None = None
    anomaly_id: str | None = Field(None, max_length=120)
    anomaly_config: AnomalyConfig | None = None


class InvestigationUpdate(BaseModel):
    title: str | None = Field(None, min_length=3, max_length=200)
    description: str | None = Field(None, max_length=10000)
    category: Category | None = None
    severity: Severity | None = None
    machine_id: int | None = None
    clear_machine: bool = False
    owner_id: int | None = None
    clear_owner: bool = False
    due_date: dt.date | None = None
    resolution_summary: str | None = Field(None, max_length=10000)


class TransitionRequest(BaseModel):
    to_status: InvStatus
    note: str | None = Field(None, max_length=5000)
    resolution_summary: str | None = Field(None, max_length=10000)


def _check_owner_assignment(db: Session, me: User, owner_id: int | None, company_id: int) -> User | None:
    try:
        owner = AC.assignable_user(db, company_id, owner_id)
    except AC.WorkflowError as e:
        raise _err(e) from e
    if owner is not None and me.role != "admin" and owner.id != me.id:
        raise HTTPException(403, {"code": "forbidden", "message": "Only administrators can assign other users."})
    return owner


@router.get("/investigations", summary="Search, filter, sort and paginate investigations")
def list_investigations(
    search: str | None = Query(None, max_length=100),
    status: list[InvStatus] = Query(default_factory=list),
    severity: list[Severity] = Query(default_factory=list),
    category: list[Category] = Query(default_factory=list),
    line_id: int | None = None, machine_id: int | None = None, owner_id: int | None = None,
    unassigned: bool = False, overdue: bool = False, open_only: bool = False,
    start_date: dt.date | None = None, end_date: dt.date | None = None,
    sort: Literal["number", "created_at", "due_date", "severity", "status", "updated_at", "title"] = "created_at",
    order: Literal["asc", "desc"] = "desc",
    page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200),
    p: Principal = Depends(require("action.read")), db: Session = Depends(get_db),
):
    conds = inv_conditions(p.company_id, search=search, status=status, severity=severity, category=category,
                           line_id=line_id, machine_id=machine_id, owner_id=owner_id, unassigned=unassigned,
                           overdue=overdue, open_only=open_only, start_date=start_date, end_date=end_date)
    # everything below runs in the database: filtering, counting, ordering and pagination (v0.3.1)
    total = db.scalar(select(func.count(Investigation.id)).where(*conds)) or 0
    I = Investigation
    key = {
        "number": I.number, "created_at": I.created_at, "updated_at": I.updated_at, "due_date": I.due_date,
        "severity": SEV_SORT, "status": I.status, "title": I.title,
    }[sort]
    primary = key.desc() if order == "desc" else key.asc()
    if sort == "due_date":
        primary = primary.nulls_last()  # investigations without a due date always at the end
    tiebreak = I.id.desc() if order == "desc" else I.id.asc()  # deterministic pages
    page_rows = db.scalars(
        select(I).where(*conds).order_by(primary, tiebreak).offset((page - 1) * page_size).limit(page_size)
        .options(selectinload(I.actions), selectinload(I.anomaly_links), selectinload(I.line), selectinload(I.machine),
                 selectinload(I.owner), selectinload(I.created_by))
    ).all()
    return {"total": total, "page": page, "page_size": page_size, "rows": [inv_dict(i, p.user) for i in page_rows]}


@router.post("/investigations", status_code=201, summary="Create an investigation (manually or from an anomaly)")
def create_investigation(body: InvestigationCreate, p: Principal = Depends(require("action.write")),
                         db: Session = Depends(get_db)):
    company = get_company(db)
    line = db.get(ProductionLine, body.line_id)
    if line is None or line.company_id != company.id:
        raise HTTPException(422, {"code": "invalid_line", "message": "Unknown production line."})
    snapshot = None
    if body.anomaly_id:
        if db.scalar(select(InvestigationAnomalyLink.id).where(InvestigationAnomalyLink.company_id == company.id,
                                                              InvestigationAnomalyLink.anomaly_key == body.anomaly_id)):
            existing = db.scalar(select(InvestigationAnomalyLink).where(
                InvestigationAnomalyLink.company_id == company.id, InvestigationAnomalyLink.anomaly_key == body.anomaly_id))
            inv0 = db.get(Investigation, existing.investigation_id)
            raise HTTPException(409, {"code": "duplicate_anomaly", "message": f"Investigation {inv0.number} already exists for this anomaly.",
                                      "investigation_id": inv0.id, "number": inv0.number})
        cfg = _cfg(db, company.id, body.anomaly_config)
        snapshot = find_anomaly(db, company.id, body.anomaly_id, cfg)
        snapshot = {**snapshot, "detection_config": cfg, "captured_at": utcnow().isoformat(),
                    "note": "Evidence snapshot frozen at investigation creation; later algorithm changes do not alter it."}
        # anomaly scope is authoritative
        pre = anomaly_prefill(db, company.id, snapshot, "en")
        body.line_id, body.machine_id = pre["line_id"], pre["machine_id"]
    try:
        AC.machine_belongs_to_line(db, body.machine_id, body.line_id)
    except AC.WorkflowError as e:
        raise _err(e) from e
    owner = _check_owner_assignment(db, p.user, body.owner_id, company.id)
    if body.due_date and body.due_date < AC.today() - dt.timedelta(days=365):
        raise HTTPException(422, {"code": "invalid_due_date", "message": "Due date is too far in the past."})
    due = body.due_date or AC.default_due_date(db, company.id, body.severity)
    for attempt in range(3):
        try:
            with db.begin_nested():
                inv = Investigation(
                    company_id=company.id, number=AC.next_number(db, company.id, AC.today().year), title=body.title.strip(),
                    description=body.description.strip(), category=body.category, severity=body.severity,
                    status="open", source="anomaly" if snapshot else "manual", line_id=body.line_id,
                    machine_id=body.machine_id, owner_id=owner.id if owner else None, created_by_id=p.id, due_date=due)
                db.add(inv)
                db.flush()
                if snapshot:
                    db.add(InvestigationAnomalyLink(
                        company_id=company.id, investigation_id=inv.id, anomaly_key=snapshot["id"],
                        anomaly_date=dt.date.fromisoformat(snapshot["date"]), level=snapshot["level"],
                        entity_code=snapshot["entity_code"], metric=snapshot["metric"], snapshot=snapshot))
                    db.flush()
            break
        except IntegrityError as exc:
            if "anomaly" in str(exc.orig).lower():
                raise HTTPException(409, {"code": "duplicate_anomaly", "message": "An investigation for this anomaly already exists."}) from exc
            if attempt == 2:
                raise
    AC.add_event(db, inv, "created", p.user, {"source": inv.source, "severity": inv.severity, "category": inv.category,
                                              "owner": owner.display_name if owner else None, "due_date": due.isoformat(),
                                              "anomaly_key": snapshot["id"] if snapshot else None})
    if owner:
        AC.add_event(db, inv, "assigned", p.user, {"from": None, "to": owner.display_name})
    audit(db, "investigation.created", company.id, "investigation", inv.id,
          {"number": inv.number, "source": inv.source, "anomaly_key": snapshot["id"] if snapshot else None})
    db.commit()
    return inv_dict(_load(db, company.id, inv.id), p.user, detail=True)


@router.get("/investigations/{inv_id}", summary="Investigation details incl. actions and anomaly evidence")
def get_investigation(inv_id: int, p: Principal = Depends(require("action.read")), db: Session = Depends(get_db)):
    return inv_dict(_load(db, p.company_id, inv_id), p.user, detail=True)


@router.patch("/investigations/{inv_id}", summary="Update investigation fields (owner/creator or admin)")
def update_investigation(inv_id: int, body: InvestigationUpdate, p: Principal = Depends(require("action.write")),
                         db: Session = Depends(get_db)):
    inv = _load(db, p.company_id, inv_id)
    _not_closed(inv)
    me = p.user
    takes_ownership = (body.owner_id == me.id and inv.owner_id is None and me.role == "analyst"
                       and set(body.model_dump(exclude_unset=True)) <= {"owner_id"})
    if not (AC.can_edit_investigation(me, inv) or takes_ownership):
        raise HTTPException(403, {"code": "forbidden", "message": "Only the owner, the creator or an administrator can edit this investigation."})
    changes: dict = {}
    for field in ("title", "description", "category", "severity", "resolution_summary"):
        val = getattr(body, field)
        if val is not None and val != getattr(inv, field):
            changes[field] = {"from": getattr(inv, field) if field not in ("description", "resolution_summary") else None,
                              "to": val if field not in ("description", "resolution_summary") else None}
            setattr(inv, field, val.strip() if isinstance(val, str) else val)
    if body.due_date is not None and body.due_date != inv.due_date:
        AC.add_event(db, inv, "due_date_changed", me, {"from": _iso(inv.due_date), "to": body.due_date.isoformat()})
        inv.due_date = body.due_date
    if body.clear_machine or (body.machine_id is not None and body.machine_id != inv.machine_id):
        new_m = None if body.clear_machine else body.machine_id
        try:
            AC.machine_belongs_to_line(db, new_m, inv.line_id)
        except AC.WorkflowError as e:
            raise _err(e) from e
        changes["machine_id"] = {"from": inv.machine_id, "to": new_m}
        inv.machine_id = new_m
    if body.clear_owner or (body.owner_id is not None and body.owner_id != inv.owner_id):
        if body.clear_owner and me.role != "admin":
            raise HTTPException(403, {"code": "forbidden", "message": "Only administrators can remove the owner."})
        new_owner = None if body.clear_owner else _check_owner_assignment(db, me, body.owner_id, p.company_id)
        AC.add_event(db, inv, "assigned", me, {"from": inv.owner.display_name if inv.owner else None,
                                               "to": new_owner.display_name if new_owner else None})
        audit(db, "investigation.assigned", p.company_id, "investigation", inv.id,
              {"number": inv.number, "owner_id": new_owner.id if new_owner else None})
        inv.owner_id = new_owner.id if new_owner else None
    if changes:
        AC.add_event(db, inv, "updated", me, changes)
        audit(db, "investigation.updated", p.company_id, "investigation", inv.id, {"number": inv.number, "fields": sorted(changes)})
    inv.updated_at = utcnow()
    db.commit()
    db.expire_all()
    return inv_dict(_load(db, p.company_id, inv_id), me, detail=True)


@router.post("/investigations/{inv_id}/transition", summary="Change the status (server-side workflow rules)")
def transition_investigation(inv_id: int, body: TransitionRequest, p: Principal = Depends(require("action.write")),
                             db: Session = Depends(get_db)):
    inv = _load(db, p.company_id, inv_id)
    try:
        AC.transition(db, inv, p.user, body.to_status, body.note, body.resolution_summary)
    except AC.WorkflowError as e:
        raise _err(e) from e
    db.commit()
    db.expire_all()
    return inv_dict(_load(db, p.company_id, inv_id), p.user, detail=True)


# ----------------------------------------------------------------------------- corrective actions

class ActionCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=300)
    description: str = Field("", max_length=5000)
    owner_id: int | None = None
    due_date: dt.date | None = None
    priority: Severity = "medium"


class ActionUpdate(BaseModel):
    title: str | None = Field(None, min_length=3, max_length=300)
    description: str | None = Field(None, max_length=5000)
    owner_id: int | None = None
    due_date: dt.date | None = None
    priority: Severity | None = None
    status: ActionStatus | None = None
    completion_notes: str | None = Field(None, max_length=5000)
    verification_result: Literal["effective", "partially_effective", "not_effective"] | None = None
    verification_notes: str | None = Field(None, max_length=5000)


@router.get("/investigations/{inv_id}/actions", summary="Corrective actions of an investigation")
def list_actions(inv_id: int, p: Principal = Depends(require("action.read")), db: Session = Depends(get_db)):
    inv = _load(db, p.company_id, inv_id)
    return [action_dict(a, p.user) for a in inv.actions]


@router.post("/investigations/{inv_id}/actions", status_code=201, summary="Add a corrective action")
def create_action(inv_id: int, body: ActionCreate, p: Principal = Depends(require("action.write")),
                  db: Session = Depends(get_db)):
    inv = _load(db, p.company_id, inv_id)
    _not_closed(inv)
    if inv.status == "resolved":
        raise HTTPException(409, {"code": "resolved", "message": "The investigation is resolved. Reopen it to add actions."})
    try:
        owner = AC.assignable_user(db, p.company_id, body.owner_id)
    except AC.WorkflowError as e:
        raise _err(e) from e
    if owner and owner.id != p.id and not AC.can_edit_investigation(p.user, inv):
        raise HTTPException(403, {"code": "forbidden", "message": "Only the investigation owner, its creator or an administrator can assign actions to other users."})
    a = CorrectiveAction(company_id=p.company_id, investigation_id=inv.id, title=body.title.strip(),
                         description=body.description.strip(), owner_id=owner.id if owner else None,
                         due_date=body.due_date, priority=body.priority, status="open", created_by_id=p.id)
    db.add(a)
    db.flush()
    AC.add_event(db, inv, "action_created", p.user, {"action_id": a.id, "title": a.title,
                                                     "owner": owner.display_name if owner else None,
                                                     "due_date": _iso(a.due_date), "priority": a.priority})
    audit(db, "action.created", p.company_id, "corrective_action", a.id, {"investigation": inv.number})
    db.commit()
    db.refresh(a)
    return action_dict(a, p.user)


@router.patch("/actions/{action_id}", summary="Update or progress a corrective action")
def update_action(action_id: int, body: ActionUpdate, p: Principal = Depends(require("action.write")),
                  db: Session = Depends(get_db)):
    a = db.get(CorrectiveAction, action_id)
    if a is None or a.company_id != p.company_id:
        raise HTTPException(404, {"code": "not_found", "message": "Action not found"})
    inv = a.investigation
    _not_closed(inv)
    me = p.user
    editing = {k for k in ("title", "description", "owner_id", "due_date", "priority") if getattr(body, k) is not None}
    if editing:
        if not AC.can_edit_investigation(me, inv):
            raise HTTPException(403, {"code": "forbidden", "message": "Only the investigation owner, its creator or an administrator can edit actions."})
        changes = {}
        if body.owner_id is not None and body.owner_id != a.owner_id:
            try:
                new_owner = AC.assignable_user(db, p.company_id, body.owner_id)
            except AC.WorkflowError as e:
                raise _err(e) from e
            changes["owner"] = {"from": a.owner.display_name if a.owner else None, "to": new_owner.display_name}
            a.owner_id = new_owner.id
        if body.due_date is not None and body.due_date != a.due_date:
            changes["due_date"] = {"from": _iso(a.due_date), "to": body.due_date.isoformat()}
            a.due_date = body.due_date
        for f in ("title", "description", "priority"):
            v = getattr(body, f)
            if v is not None and v != getattr(a, f):
                changes[f] = {"from": getattr(a, f) if f != "description" else None, "to": v if f != "description" else None}
                setattr(a, f, v.strip() if isinstance(v, str) else v)
        if changes:
            a.updated_at = utcnow()
            AC.add_event(db, inv, "action_updated", me, {"action_id": a.id, "title": a.title, "changes": changes})
            audit(db, "action.updated", p.company_id, "corrective_action", a.id, {"fields": sorted(changes)})
    if body.status is not None and body.status != a.status:
        if not AC.can_update_action(me, a):
            raise HTTPException(403, {"code": "forbidden", "message": "Only the responsible person, the investigation owner or an administrator can change this action's status."})
        try:
            AC.action_transition(db, a, me, body.status, body.completion_notes, body.verification_result,
                                 body.verification_notes)
        except AC.WorkflowError as e:
            raise _err(e) from e
    db.commit()
    db.refresh(a)
    return action_dict(a, me)


@router.get("/actions/mine", summary="Corrective actions assigned to the current user")
def my_actions(include_finished: bool = False, p: Principal = Depends(require("action.read")), db: Session = Depends(get_db)):
    q = select(CorrectiveAction).where(CorrectiveAction.company_id == p.company_id, CorrectiveAction.owner_id == p.id)
    if not include_finished:
        q = q.where(CorrectiveAction.status.in_(AC.ACTIVE_ACTION_STATUSES + ("done",)))
    acts = db.scalars(q).all()
    ref = AC.today()
    out = [action_dict(a, p.user) for a in sorted(acts, key=lambda a: (a.due_date or dt.date.max, a.id))]
    return {
        "today": ref.isoformat(), "timezone": get_settings().timezone,
        "overdue": [a for a in out if a["overdue"]],
        "due_soon": [a for a in out if not a["overdue"] and a["status"] in AC.ACTIVE_ACTION_STATUSES and a["due_date"]
                     and dt.date.fromisoformat(a["due_date"]) <= ref + dt.timedelta(days=7)],
        "later": [a for a in out if not a["overdue"] and a["status"] in AC.ACTIVE_ACTION_STATUSES and
                  (not a["due_date"] or dt.date.fromisoformat(a["due_date"]) > ref + dt.timedelta(days=7))],
        "awaiting_verification": [a for a in out if a["status"] == "done"],
        "finished": [a for a in out if a["status"] in ("verified", "cancelled")],
    }


# ----------------------------------------------------------------------------- comments & timeline

class CommentCreate(BaseModel):
    body: str = Field(..., min_length=1, max_length=5000)


@router.get("/investigations/{inv_id}/comments")
def list_comments(inv_id: int, p: Principal = Depends(require("action.read")), db: Session = Depends(get_db)):
    _load(db, p.company_id, inv_id)
    rows = db.scalars(select(InvestigationComment).where(InvestigationComment.investigation_id == inv_id)
                      .order_by(InvestigationComment.created_at))
    return [{"id": c.id, "author": c.author_label, "author_id": c.author_id, "body": c.body, "created_at": _iso(c.created_at)} for c in rows]


@router.post("/investigations/{inv_id}/comments", status_code=201)
def add_comment(inv_id: int, body: CommentCreate, p: Principal = Depends(require("action.write")), db: Session = Depends(get_db)):
    inv = _load(db, p.company_id, inv_id)
    if not AC.can_contribute(p.user, inv):
        raise HTTPException(409, {"code": "closed", "message": "Comments are not possible on closed investigations."})
    c = InvestigationComment(company_id=p.company_id, investigation_id=inv.id, author_id=p.id,
                             author_label=p.user.display_name, body=body.body.strip())
    db.add(c)
    db.flush()
    AC.add_event(db, inv, "comment", p.user, {"comment_id": c.id, "body": c.body})
    audit(db, "investigation.commented", p.company_id, "investigation", inv.id, {"comment_id": c.id})
    db.commit()
    return {"id": c.id, "author": c.author_label, "body": c.body, "created_at": _iso(c.created_at)}


@router.get("/investigations/{inv_id}/timeline", summary="Chronological activity timeline")
def timeline(inv_id: int, p: Principal = Depends(require("action.read")), db: Session = Depends(get_db)):
    _load(db, p.company_id, inv_id)
    rows = db.scalars(select(InvestigationEvent).where(InvestigationEvent.investigation_id == inv_id)
                      .order_by(InvestigationEvent.created_at, InvestigationEvent.id))
    return [{"id": e.id, "type": e.event_type, "actor": e.actor_label, "actor_id": e.actor_id, "data": e.data,
             "created_at": _iso(e.created_at)} for e in rows]


# ----------------------------------------------------------------------------- evidence

def _attachment_dict(a: InvestigationAttachment, me: User | None = None) -> dict:
    verified = ES.is_verified(a)
    admin = me is not None and me.role == "admin"
    return {"id": a.id, "filename": a.original_filename, "content_type": a.content_type, "size": a.size,
            "sha256": a.sha256, "description": a.description, "uploaded_by": a.uploaded_by_label,
            "uploaded_by_id": a.uploaded_by_id, "created_at": _iso(a.created_at),
            "scan_status": a.scan_status, "scanner": a.scanner, "scanned_at": _iso(a.scanned_at),
            "scan_detail": a.scan_detail if admin else None,
            "released_at": _iso(a.released_at), "release_note": a.release_note if admin else None,
            "quarantined": not verified,
            # what the CURRENT user may do (the download endpoint enforces the same rules)
            "downloadable": verified or (admin and a.scan_status != "infected"),
            "requires_acknowledgement": (not verified) and admin and a.scan_status != "infected",
            "can_release": admin and not verified and a.scan_status != "infected",
            # v0.3.3: the scanner could not scan this file within FP_EVIDENCE_SCAN_MAX_ATTEMPTS attempts
            "retries_exhausted": ES.retries_exhausted(a),
            "scan_attempts": a.scan_attempts or 0}


@router.get("/investigations/{inv_id}/attachments")
def list_attachments(inv_id: int, p: Principal = Depends(require("action.read")), db: Session = Depends(get_db)):
    _load(db, p.company_id, inv_id)
    rows = db.scalars(select(InvestigationAttachment).where(InvestigationAttachment.investigation_id == inv_id,
                                                            InvestigationAttachment.deleted_at.is_(None))
                      .order_by(InvestigationAttachment.created_at))
    return [_attachment_dict(a, p.user) for a in rows]


@router.post("/investigations/{inv_id}/attachments", status_code=201, summary="Upload evidence (PDF, PNG, JPEG, CSV, XLSX)")
async def upload_attachment(inv_id: int, file: UploadFile = File(...), description: str | None = Form(None, max_length=500),
                            p: Principal = Depends(require("action.write")), db: Session = Depends(get_db)):
    s = get_settings()
    inv = _load(db, p.company_id, inv_id)
    if not AC.can_contribute(p.user, inv):
        raise HTTPException(409, {"code": "closed", "message": "Evidence cannot be added to closed investigations."})
    name = Path(file.filename or "evidence").name
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EVIDENCE:
        raise HTTPException(415, {"code": "file_type", "message": "Allowed evidence types: PDF, PNG, JPEG, CSV, XLSX."})
    limit = s.max_evidence_mb * 1024 * 1024
    data = bytearray()
    while chunk := await file.read(1024 * 1024):
        data.extend(chunk)
        if len(data) > limit:
            raise HTTPException(413, {"code": "file_size", "message": f"File exceeds the {s.max_evidence_mb} MB limit."})
    if not data:
        raise HTTPException(400, {"code": "empty", "message": "The file is empty."})
    ctype, magics = ALLOWED_EVIDENCE[ext]
    if magics and not any(bytes(data[: len(m)]) == m for m in magics):
        raise HTTPException(415, {"code": "file_content", "message": "File content does not match its extension."})
    if ext == ".csv":
        head = bytes(data[:65536])
        if b"\x00" in head:
            raise HTTPException(415, {"code": "file_content", "message": "The CSV file does not look like text."})
    stored = f"{uuid.uuid4().hex}{ext}"
    EStore.write_upload(stored, bytes(data))  # atomically, always into quarantine first
    safe = re.sub(r"[^\w.\- ()äöüÄÖÜß]", "_", name)[:255]
    att = InvestigationAttachment(company_id=p.company_id, investigation_id=inv.id, original_filename=safe,
                                  stored_filename=stored, content_type=ctype, size=len(data),
                                  sha256=hashlib.sha256(data).hexdigest(), description=(description or "").strip() or None,
                                  uploaded_by_id=p.id, uploaded_by_label=p.user.display_name,
                                  scan_status="pending" if ES.scanner_name() != "none" else "not_scanned")
    db.add(att)
    db.flush()
    AC.add_event(db, inv, "attachment_added", p.user, {"attachment_id": att.id, "filename": safe, "size": len(data)})
    audit(db, "investigation.attachment_added", p.company_id, "investigation", inv.id,
          {"attachment_id": att.id, "sha256": att.sha256, "size": att.size})
    try:
        db.commit()
    except Exception:
        db.rollback()
        EStore.discard_upload(stored)   # nothing references the file; never leave it behind
        raise
    if att.scan_status == "pending" and not ES.scanner_unavailable(db):
        try:
            ES.scan_attachment(db, att)   # scanner problems leave the file quarantined; the worker retries
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
            log.exception("Evidence scan after upload failed (attachment %s)", att.id)
        EStore.apply_now([att.id])        # file move AFTER the commit (outbox)
        db.refresh(att)
    return _attachment_dict(att, p.user)


@router.get("/attachments/{att_id}/download", summary="Download evidence (authorized, never a public URL)")
def download_attachment(att_id: int, acknowledge_risk: bool = False, p: Principal = Depends(require("action.read")),
                        db: Session = Depends(get_db)):
    a = db.get(InvestigationAttachment, att_id)
    if a is None or a.company_id != p.company_id or a.deleted_at is not None:
        raise HTTPException(404, {"code": "not_found", "message": "Attachment not found"})
    if a.scan_status == "infected":
        raise HTTPException(403, {"code": "infected", "message": "The malware scanner flagged this file. It cannot be downloaded."})
    if not ES.is_verified(a):
        if p.role != "admin":
            raise HTTPException(403, {"code": "quarantined",
                                      "message": "This file has not been verified by the malware scanner yet and is quarantined."})
        if not acknowledge_risk:
            raise HTTPException(409, {"code": "acknowledge_required",
                                      "message": "Unverified file: confirm that you accept the risk (acknowledge_risk=true)."})
        audit(db, "evidence.unverified_download", p.company_id, "attachment", a.id, {"scan_status": a.scan_status})
        db.commit()
    path = ES.locate(a)
    if path is None:
        raise HTTPException(410, {"code": "missing", "message": "The evidence file is no longer available."})
    if EStore.sha256_of(path) != a.sha256:   # v0.3.3: never serve content that was not the uploaded/scanned file
        log.error("Evidence %s (attachment %s) does not match its SHA-256 – download refused", a.stored_filename, a.id)
        raise HTTPException(409, {"code": "integrity", "message": "The stored file does not match its recorded checksum."})
    # always as download (no inline rendering of user-supplied content)
    return FileResponse(path, media_type=a.content_type, filename=a.original_filename,
                        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"})


class AttachmentRelease(BaseModel):
    note: str = Field(..., min_length=10, max_length=500)


@router.post("/attachments/{att_id}/release", summary="Administrator: release an unverified (not infected) file from quarantine")
def release_attachment(att_id: int, body: AttachmentRelease, p: Principal = Depends(require("action.manage")),
                       db: Session = Depends(get_db)):
    a = db.get(InvestigationAttachment, att_id)
    if a is None or a.company_id != p.company_id or a.deleted_at is not None:
        raise HTTPException(404, {"code": "not_found", "message": "Attachment not found"})
    if ES.is_verified(a):
        raise HTTPException(409, {"code": "already_verified", "message": "The file is not quarantined."})
    try:
        ES.release(db, a, p.user, body.note)
    except PermissionError as exc:
        raise HTTPException(403, {"code": "infected", "message": str(exc)}) from exc
    db.commit()
    EStore.apply_now([a.id])   # move after the commit; the reconciliation job retries on failure
    db.refresh(a)
    return _attachment_dict(a, p.user)


@router.post("/attachments/{att_id}/rescan", summary="Administrator: scan a quarantined file again now")
def rescan_attachment(att_id: int, p: Principal = Depends(require("action.manage")), db: Session = Depends(get_db)):
    a = db.get(InvestigationAttachment, att_id)
    if a is None or a.company_id != p.company_id or a.deleted_at is not None:
        raise HTTPException(404, {"code": "not_found", "message": "Attachment not found"})
    if ES.scanner_name() == "none":
        raise HTTPException(409, {"code": "no_scanner", "message": "No malware scanner is configured (FP_EVIDENCE_SCANNER)."})
    if a.scan_status in ("clean", "infected"):
        raise HTTPException(409, {"code": "already_scanned", "message": f"The file was already scanned: {a.scan_status}."})
    if ES.scanner_unavailable(db):
        h = ES.health(db)
        raise HTTPException(503, {"code": "scanner_unavailable",
                                  "message": f"The malware scanner is unavailable since {h.unavailable_since}: {h.last_error}. "
                                             "Run a scanner check after fixing it."})
    previous_attempts = a.scan_attempts or 0
    a.scan_attempts = 0                       # manual rescan: a fresh set of attempts
    ES.scan_attachment(db, a)
    audit(db, "evidence.rescanned", p.company_id, "attachment", a.id,
          {"result": a.scan_status, "previous_attempts": previous_attempts})
    db.commit()
    EStore.apply_now([a.id])
    db.refresh(a)
    return _attachment_dict(a, p.user)


class AttachmentDelete(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500)


@router.post("/attachments/{att_id}/delete", summary="Remove evidence from the investigation (soft delete, file retained)")
def delete_attachment(att_id: int, body: AttachmentDelete, p: Principal = Depends(require("action.write")),
                      db: Session = Depends(get_db)):
    a = db.get(InvestigationAttachment, att_id)
    if a is None or a.company_id != p.company_id or a.deleted_at is not None:
        raise HTTPException(404, {"code": "not_found", "message": "Attachment not found"})
    inv = _load(db, p.company_id, a.investigation_id)
    _not_closed(inv)
    if not (p.role == "admin" or a.uploaded_by_id == p.id):
        raise HTTPException(403, {"code": "forbidden", "message": "Only the uploader or an administrator can remove evidence."})
    a.deleted_at, a.deleted_by_id = utcnow(), p.id
    AC.add_event(db, inv, "attachment_removed", p.user, {"attachment_id": a.id, "filename": a.original_filename,
                                                          "reason": body.reason.strip()})
    audit(db, "investigation.attachment_removed", p.company_id, "investigation", inv.id, {"attachment_id": a.id})
    db.commit()
    return {"status": "removed"}


# ----------------------------------------------------------------------------- KPI comparison

class KpiComparisonParams(BaseModel):
    baseline_start: dt.date | None = None
    baseline_end: dt.date | None = None
    comparison_start: dt.date | None = None
    comparison_end: dt.date | None = None
    scope: Literal["machine", "line"] | None = None
    shift_id: int | None = None
    product_id: int | None = None


def _comparison(db: Session, inv: Investigation, prm: KpiComparisonParams) -> dict:
    defaults = AC.default_comparison_periods(db, inv)
    bs = prm.baseline_start or defaults["baseline_start"]
    be = prm.baseline_end or defaults["baseline_end"]
    cs = prm.comparison_start or defaults["comparison_start"]
    ce = prm.comparison_end or defaults["comparison_end"]
    if be < bs or ce < cs:
        raise HTTPException(422, {"code": "invalid_period", "message": "Each period's end must not be before its start."})
    if (be - bs).days > 366 or (ce - cs).days > 366:
        raise HTTPException(422, {"code": "invalid_period", "message": "Periods must not exceed 366 days."})
    if cs <= be and ce >= bs:
        raise HTTPException(422, {"code": "overlapping_periods", "message": "Baseline and comparison periods must not overlap."})
    scope = prm.scope or ("machine" if inv.machine_id else "line")
    if scope == "machine" and not inv.machine_id:
        raise HTTPException(422, {"code": "invalid_scope", "message": "This investigation has no machine."})
    base_f = Filters(line_ids=[inv.line_id]) if scope == "line" else Filters(machine_ids=[inv.machine_id])
    if prm.shift_id:
        base_f.shift_ids = [prm.shift_id]
    if prm.product_id:
        base_f.product_ids = [prm.product_id]
    before = load_records(db, inv.company_id, base_f.shifted(bs, be))
    after = load_records(db, inv.company_id, base_f.shifted(cs, ce))
    result = AC.kpi_comparison(before, after)
    result.update({
        "baseline": {"start": bs.isoformat(), "end": be.isoformat()},
        "comparison": {"start": cs.isoformat(), "end": ce.isoformat()},
        "defaults": {k: v.isoformat() for k, v in defaults.items()},
        "scope": scope, "scope_label": inv.machine.code if scope == "machine" else inv.line.code,
        "shift_id": prm.shift_id, "product_id": prm.product_id,
        "series_before": AC.daily_series(before), "series_after": AC.daily_series(after),
    })
    return result


@router.get("/investigations/{inv_id}/kpi-comparison", summary="Observed KPIs before vs. after (not a causal proof)")
def kpi_comparison(inv_id: int, prm: KpiComparisonParams = Depends(), p: Principal = Depends(require("action.read")),
                   db: Session = Depends(get_db)):
    return _comparison(db, _load(db, p.company_id, inv_id), prm)


@router.post("/investigations/{inv_id}/kpi-comparison", summary="Record the current comparison in the timeline")
def record_kpi_comparison(inv_id: int, prm: KpiComparisonParams, p: Principal = Depends(require("action.write")),
                          db: Session = Depends(get_db)):
    inv = _load(db, p.company_id, inv_id)
    if not AC.can_contribute(p.user, inv) and p.role != "admin":
        raise HTTPException(409, {"code": "closed", "message": "Closed investigations are read-only."})
    res = _comparison(db, inv, prm)
    AC.add_event(db, inv, "kpi_comparison_recorded", p.user, {
        "baseline": res["baseline"], "comparison": res["comparison"], "scope": res["scope"],
        "scope_label": res["scope_label"], "sufficient_data": res["sufficient_data"],
        "metrics": [{k: m[k] for k in ("metric", "before", "after", "abs_diff", "pct_change", "direction")} for m in res["metrics"]],
        "disclaimer": res["disclaimer"]})
    audit(db, "investigation.kpi_comparison_recorded", p.company_id, "investigation", inv.id)
    db.commit()
    return res


# ----------------------------------------------------------------------------- dashboard, alerts, settings

@router.get("/action-center/summary", summary="Action Center dashboard (KPIs, charts, priority issues, alerts)")
def action_center_summary(
    start_date: dt.date | None = None, end_date: dt.date | None = None, line_id: int | None = None,
    machine_id: int | None = None, severity: Severity | None = None, status: InvStatus | None = None,
    owner_id: int | None = None, category: Category | None = None, include_repeated: bool = True,
    p: Principal = Depends(require("action.read")), db: Session = Depends(get_db),
):
    # READ-ONLY (v0.3.1): alerts are evaluated by the scheduled worker job, never by a dashboard request.
    conds = inv_conditions(p.company_id, status=status, severity=severity, category=category, line_id=line_id,
                           machine_id=machine_id, owner_id=owner_id, start_date=start_date, end_date=end_date)
    filtered = any((line_id, machine_id, severity, status, owner_id, category, start_date, end_date))
    Al = ActionCenterAlert
    aq = select(Al).where(Al.company_id == p.company_id, Al.resolved_at.is_(None))
    in_scope = Al.investigation_id.in_(select(Investigation.id).where(*conds))
    if filtered:
        machine_alert = Al.investigation_id.is_(None) & Al.machine_id.is_not(None)
        if machine_id:
            machine_alert = machine_alert & (Al.machine_id == machine_id)
        if severity or status or owner_id or category:
            machine_alert = Al.id.is_(None)  # repeated-anomaly alerts have no investigation attributes
        aq = aq.where(in_scope | machine_alert)
    if not include_repeated:
        aq = aq.where(Al.rule != "repeated_anomalies")
    filtered_alerts = db.scalars(aq).all()
    s = AC.summary(db, p.company_id, conds, filtered_alerts)
    s["alert_evaluation"] = AL.status(db, p.company_id)
    s["priority_issues"] = [inv_dict(i, p.user) for i in s["priority_issues"]]
    s["overdue_actions_list"] = [action_dict(a, p.user) for a in s["overdue_actions_list"]]
    sev = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    s["alerts"] = [alert_dict(a) for a in sorted(s["alerts"], key=lambda a: (sev.get(a.severity, 9), a.first_detected_at))]
    return s


@router.post("/action-center/alerts/evaluate", summary="Administrator: run the scheduled alert evaluation now")
def evaluate_alerts_now(p: Principal = Depends(require("action.manage")), db: Session = Depends(get_db)):
    """Runs the same job as the worker (same lock, same retry policy, recorded in job_runs)."""
    run = AL.run_alert_evaluation(p.company_id, trigger=f"manual:{p.user.email}"[:120], max_attempts=1)[0]
    audit(db, "alerts.evaluated_manually", p.company_id, "job_run", run.id, {"status": run.status})
    db.commit()
    return {"run": JOBS.run_dict(run), "evaluation": AL.status(db, p.company_id)}


@router.get("/action-center/jobs", summary="Administrator: recent background job runs")
def recent_jobs(limit: int = Query(50, ge=1, le=500), p: Principal = Depends(require("action.manage")),
                db: Session = Depends(get_db)):
    from ..models import JobRun

    rows = db.scalars(select(JobRun).where((JobRun.company_id == p.company_id) | JobRun.company_id.is_(None))
                      .order_by(JobRun.id.desc()).limit(limit)).all()
    return [JOBS.run_dict(r) for r in rows]


@router.post("/action-center/alerts/{alert_id}/acknowledge", summary="Acknowledge an alert (it stays visible until resolved)")
def acknowledge_alert(alert_id: int, p: Principal = Depends(require("action.write")), db: Session = Depends(get_db)):
    al = db.get(ActionCenterAlert, alert_id)
    if al is None or al.company_id != p.company_id or al.resolved_at is not None:
        raise HTTPException(404, {"code": "not_found", "message": "Alert not found or already resolved"})
    al.acknowledged_at, al.acknowledged_by_id = utcnow(), p.id
    audit(db, "alert.acknowledged", p.company_id, "alert", al.id, {"rule": al.rule, "subject": al.subject_key})
    db.commit()
    return alert_dict(al)


class DueDays(BaseModel):
    critical: int = Field(2, ge=0, le=365)
    high: int = Field(5, ge=0, le=365)
    medium: int = Field(10, ge=0, le=365)
    low: int = Field(20, ge=0, le=365)


class RulesConfig(BaseModel):
    critical_unassigned_hours: float = Field(4, ge=0, le=720)
    critical_open_hours: float = Field(48, ge=1, le=8760)
    pending_verification_days: float = Field(7, ge=1, le=365)
    action_overdue_grace_days: int = Field(0, ge=0, le=90)
    repeated_anomaly_count: int = Field(3, ge=2, le=50)
    repeated_anomaly_window_days: int = Field(30, ge=7, le=365)
    default_due_days: DueDays = Field(default_factory=DueDays)


@router.get("/action-center/settings", summary="Alert rule thresholds and default deadlines")
def get_rules(p: Principal = Depends(require("action.read")), db: Session = Depends(get_db)):
    return {**AC.rules(db, p.company_id), "timezone": get_settings().timezone}


@router.put("/action-center/settings", summary="Configure alert thresholds (admin)")
def put_rules(body: RulesConfig, p: Principal = Depends(require("action.manage")), db: Session = Depends(get_db)):
    set_setting(db, p.company_id, "action_center_rules", body.model_dump())
    audit(db, "action_center.settings_updated", p.company_id, details=body.model_dump())
    db.commit()
    return {**AC.rules(db, p.company_id), "timezone": get_settings().timezone}


@router.get("/action-center/assignees", summary="Active users who can be assigned (analysts and admins)")
def assignees(p: Principal = Depends(require("action.read")), db: Session = Depends(get_db)):
    rows = db.scalars(select(User).where(User.company_id == p.company_id, User.is_active.is_(True),
                                         User.role.in_(("analyst", "admin"))).order_by(User.display_name))
    return [user_ref(u) for u in rows]


@router.get("/action-center/anomaly-links", summary="Which anomaly IDs already have an investigation")
def anomaly_links(ids: list[str] = Query(default_factory=list), p: Principal = Depends(require("action.read")),
                  db: Session = Depends(get_db)):
    links = AC.link_for_anomaly(db, p.company_id, ids[:500])
    out = {}
    for key, lk in links.items():
        inv = db.get(Investigation, lk.investigation_id)
        out[key] = {"investigation_id": inv.id, "number": inv.number, "status": inv.status}
    return out
