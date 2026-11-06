"""Settings, demo-data management and audit log."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import __version__
from ..config import get_settings
from ..db import get_db
from ..models import AuditLog, ImportJob, ReportJob, User
from ..services.data_access import get_company
from ..services.demo_data import clear_company_data, load_demo
from ..services.auth import verify_password
from ..services.settings_store import audit, get_setting, set_setting
from .deps import Principal, require

router = APIRouter(prefix="/api", tags=["settings"])

MetricName = Literal["defect_rate", "downtime_ratio", "target_achievement", "performance"]


class MinAbsChange(BaseModel):
    defect_rate: float = Field(0.015, ge=0, le=1)
    downtime_ratio: float = Field(0.10, ge=0, le=1)
    target_achievement: float = Field(0.15, ge=0, le=5)
    performance: float = Field(0.10, ge=0, le=5)


class AnomalyConfig(BaseModel):
    window: int = Field(20, ge=5, le=120)
    min_history: int = Field(10, ge=5, le=120)
    z_threshold: float = Field(4.0, ge=1.5, le=20)
    high_z: float = Field(6.0, ge=1.5, le=50)
    critical_z: float = Field(9.0, ge=1.5, le=100)
    metrics: list[MetricName] = Field(default_factory=lambda: ["defect_rate", "downtime_ratio", "target_achievement", "performance"])
    levels: list[Literal["machine", "line"]] = Field(default_factory=lambda: ["machine", "line"])
    min_abs_change: MinAbsChange = Field(default_factory=MinAbsChange)
    use_isolation_forest: bool = False
    isolation_forest_contamination: float = Field(0.01, gt=0, le=0.2)


class SettingsUpdate(BaseModel):
    company_name: str | None = Field(None, min_length=1, max_length=200)
    default_language: Literal["de", "en"] | None = None
    anomaly_config: AnomalyConfig | None = None


def _settings_payload(db: Session) -> dict:
    c = get_company(db)
    s = get_settings()
    return {
        "company_name": c.name,
        "company_is_synthetic": c.is_synthetic,
        "default_language": get_setting(db, c.id, "default_language"),
        "anomaly_config": get_setting(db, c.id, "anomaly_config"),
        "system": {
            "environment": s.environment,
            "max_upload_mb": s.max_upload_mb,
            "max_evidence_mb": s.max_evidence_mb,
            "evidence_scanner": s.evidence_scanner,
            "max_import_rows": s.max_import_rows,
            "allowed_file_types": [".csv", ".xlsx"],
            "external_ai_apis": False,
            "demo_tools_enabled": s.demo_tools_enabled,
            "timezone": s.timezone,
            "version": __version__,
            "build": s.build_sha,
            "database": s.database_url.split(":", 1)[0],
        },
    }


@router.get("/settings")
def read_settings(_p: Principal = Depends(require("settings.read")), db: Session = Depends(get_db)):
    return _settings_payload(db)


@router.put("/settings")
def update_settings(body: SettingsUpdate, _p: Principal = Depends(require("settings.write")),
                    db: Session = Depends(get_db)):
    c = get_company(db)
    changed = []
    if body.company_name is not None:
        c.name = body.company_name.strip()
        changed.append("company_name")
    if body.default_language is not None:
        set_setting(db, c.id, "default_language", body.default_language)
        changed.append("default_language")
    if body.anomaly_config is not None:
        cfg = body.anomaly_config
        if not (cfg.z_threshold <= cfg.high_z <= cfg.critical_z):
            raise HTTPException(422, "Thresholds must satisfy z_threshold ≤ high_z ≤ critical_z")
        set_setting(db, c.id, "anomaly_config", cfg.model_dump())
        changed.append("anomaly_config")
    audit(db, "settings.updated", c.id, "company", c.id, {"fields": changed})
    db.commit()
    return _settings_payload(db)


# ----------------------------------------------------------------------------- destructive actions

class DestructiveConfirm(BaseModel):
    """Destructive actions require the typed confirmation word AND the admin's current password."""
    password: str = Field(..., max_length=256)
    confirm: str = Field(..., max_length=20)


def verify_reauth(db: Session, p: Principal, body: DestructiveConfirm, expected: str, event: str) -> None:
    if body.confirm != expected:
        raise HTTPException(400, {"code": "confirmation_required", "message": f"Type {expected} to confirm."})
    ok, _ = verify_password(db.get(User, p.id).password_hash, body.password)
    if not ok:
        audit(db, f"{event}.reauth_failed", p.company_id, "user", p.id)
        db.commit()
        raise HTTPException(403, {"code": "reauth_failed", "message": "Password incorrect – action not performed."})


def _require_demo_tools() -> None:
    if not get_settings().demo_tools_enabled:
        raise HTTPException(403, {"code": "demo_tools_disabled",
                                  "message": "Demo/data-reset tools are disabled in this environment (FP_ENABLE_DEMO_TOOLS)."})


def _company_files(db: Session, company_id: int) -> list[Path]:
    """Private files belonging to the company (removed after the DB transaction commits)."""
    s = get_settings()
    out = [s.upload_dir / n for n in db.scalars(select(ImportJob.stored_filename).where(ImportJob.company_id == company_id))]
    out += [s.report_dir / n for n in db.scalars(select(ReportJob.stored_filename).where(
        ReportJob.company_id == company_id, ReportJob.stored_filename.is_not(None)))]
    from ..models import InvestigationAttachment
    for n in db.scalars(select(InvestigationAttachment.stored_filename).where(
            InvestigationAttachment.company_id == company_id)):
        out += [s.evidence_dir / n, s.quarantine_dir / n]  # released/clean or quarantined
    return out


def _remove_files(paths: list[Path]) -> int:
    n = 0
    for f in paths:
        try:
            f.unlink()
            n += 1
        except FileNotFoundError:
            pass
    return n


@router.post("/admin/demo/load", summary="Replace ALL company data with the synthetic demo dataset (admin, re-auth)")
def demo_load(body: DestructiveConfirm, p: Principal = Depends(require("data.destroy")), db: Session = Depends(get_db)):
    _require_demo_tools()
    verify_reauth(db, p, body, "RELOAD", "demo.load")
    c = get_company(db)
    files = _company_files(db, c.id)
    stats = load_demo(db, c)
    audit(db, "demo.loaded", c.id, "company", c.id, stats)
    db.commit()
    stats["files_removed"] = _remove_files(files)
    # populate the alert table immediately (otherwise the next worker cycle does it)
    from ..services.alerts import run_alert_evaluation
    stats["alert_evaluation"] = run_alert_evaluation(c.id, trigger="demo.load", max_attempts=1)[0].status
    return {"status": "ok", **stats}


@router.post("/admin/data/delete", summary="Delete all production data, master data, imports and reports (admin, re-auth)")
def delete_all(body: DestructiveConfirm, p: Principal = Depends(require("data.destroy")), db: Session = Depends(get_db)):
    _require_demo_tools()
    verify_reauth(db, p, body, "DELETE", "data.delete")
    c = get_company(db)
    files = _company_files(db, c.id)
    clear_company_data(db, c.id)
    c.is_synthetic = False
    audit(db, "data.deleted", c.id, "company", c.id, {"files": len(files)})
    db.commit()
    return {"status": "ok", "files_removed": _remove_files(files)}


@router.get("/audit-log")
def audit_log(limit: int = Query(100, ge=1, le=1000), event: str | None = Query(None, max_length=60),
              _p: Principal = Depends(require("audit.read")), db: Session = Depends(get_db)):
    c = get_company(db)
    q = select(AuditLog).where(AuditLog.company_id == c.id)
    if event:
        q = q.where(AuditLog.event.like(f"{event}%"))
    rows = db.scalars(q.order_by(AuditLog.id.desc()).limit(limit)).all()
    return [{"id": r.id, "event": r.event, "entity_type": r.entity_type, "entity_id": r.entity_id,
             "details": r.details, "user_email": r.user_email, "client_ip": r.client_ip,
             "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]
