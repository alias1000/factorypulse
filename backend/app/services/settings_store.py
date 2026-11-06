"""Per-company application settings stored in ``app_settings`` and audit logging."""
from __future__ import annotations

import copy
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AppSetting, AuditLog

log = logging.getLogger("factorypulse.audit")

DEFAULT_ANOMALY_CONFIG: dict[str, Any] = {
    "window": 20,  # trailing observations used as baseline
    "min_history": 10,  # minimum observations before a point can be scored
    "z_threshold": 4.0,  # robust z-score threshold for "medium"
    "high_z": 6.0,
    "critical_z": 9.0,
    "metrics": ["defect_rate", "downtime_ratio", "target_achievement", "performance"],
    "levels": ["machine", "line"],
    # Minimum absolute deviation from baseline required (in ratio units)
    "min_abs_change": {
        "defect_rate": 0.015,
        "downtime_ratio": 0.10,
        "target_achievement": 0.15,
        "performance": 0.10,
    },
    "use_isolation_forest": False,
    "isolation_forest_contamination": 0.01,
}

DEFAULTS: dict[str, Any] = {
    "anomaly_config": DEFAULT_ANOMALY_CONFIG,
    "default_language": "de",
}


def get_setting(db: Session, company_id: int, key: str) -> Any:
    row = db.scalar(select(AppSetting).where(AppSetting.company_id == company_id, AppSetting.key == key))
    if row is None:
        return copy.deepcopy(DEFAULTS.get(key))
    if isinstance(DEFAULTS.get(key), dict) and isinstance(row.value, dict):
        merged = copy.deepcopy(DEFAULTS[key])
        merged.update(row.value)
        return merged
    return row.value


def set_setting(db: Session, company_id: int, key: str, value: Any) -> None:
    row = db.scalar(select(AppSetting).where(AppSetting.company_id == company_id, AppSetting.key == key))
    if row is None:
        db.add(AppSetting(company_id=company_id, key=key, value=value))
    else:
        row.value = value


def audit(db: Session, event: str, company_id: int | None = None, entity_type: str | None = None,
          entity_id: int | None = None, details: dict | None = None, client_ip: str | None = None) -> None:
    """Persist an audit entry (committed together with the caller's transaction).

    The acting user, client IP and request id are taken from the request context."""
    from .auth import ctx

    c = ctx()
    db.add(AuditLog(company_id=company_id, event=event, entity_type=entity_type, entity_id=entity_id,
                    details=details, client_ip=client_ip or c.get("ip"), user_id=c.get("user_id"),
                    user_email=c.get("user_email")))
    log.info("audit event=%s user=%s entity=%s:%s request=%s details=%s", event, c.get("user_email"),
             entity_type, entity_id, c.get("request_id"), details)
