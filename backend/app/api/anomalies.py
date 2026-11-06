"""Anomaly detection endpoints."""
from __future__ import annotations

import datetime as dt
from typing import Literal

import pandas as pd

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from .deps import require
from ..services import anomaly as an
from ..services.data_access import Filters, filters_dependency, get_company, load_defects, load_downtime, load_records
from ..services.settings_store import get_setting
from .admin import AnomalyConfig

router = APIRouter(prefix="/api/anomalies", tags=["anomalies"], dependencies=[Depends(require("anomaly.run"))])


class FilterBody(BaseModel):
    start_date: str | None = None
    end_date: str | None = None
    line_ids: list[int] = []
    machine_ids: list[int] = []
    shift_ids: list[int] = []
    product_ids: list[int] = []


class DetectRequest(BaseModel):
    filters: FilterBody = FilterBody()
    config: AnomalyConfig | None = None


def _cfg(db: Session, company_id: int, override: AnomalyConfig | None) -> dict:
    cfg = override.model_dump() if override else get_setting(db, company_id, "anomaly_config")
    if not (cfg["z_threshold"] <= cfg["high_z"] <= cfg["critical_z"]):
        raise HTTPException(422, "Thresholds must satisfy z_threshold ≤ high_z ≤ critical_z")
    return cfg


BASELINE_LOOKBACK_DAYS = 60


def run_detection(db: Session, company_id: int, f: Filters, cfg: dict, is_synthetic: bool) -> dict:
    # The baseline needs history: detect on the selected period plus a look-back window,
    # then report only anomalies inside the selected period.
    hist_f = f
    if f.start_date:
        hist_f = f.shifted(f.start_date - dt.timedelta(days=BASELINE_LOOKBACK_DAYS), f.end_date)
    records = load_records(db, company_id, hist_f)
    result = an.detect(records, cfg, load_downtime(db, company_id, hist_f), load_defects(db, company_id, hist_f))
    if f.start_date and not records.empty:
        in_period = records[records["date"] >= pd.Timestamp(f.start_date)]
        if f.end_date:
            in_period = in_period[in_period["date"] <= pd.Timestamp(f.end_date)]
        result = an.restrict_to_period(result, f.start_date.isoformat(),
                                       f.end_date.isoformat() if f.end_date else None, int(in_period["date"].nunique()))
    result["config"] = cfg
    result["method_note"] = (
        "Rolling robust z-score per machine/line and metric (baseline = median of the previous "
        f"{cfg['window']} production days; spread = 1.4826 × MAD). Detected anomalies are statistical signals, not "
        "confirmed operational causes. No failure prediction is performed.")
    truth = get_setting(db, company_id, "demo_injected_anomalies") if is_synthetic else None
    if truth and result["status"] == "ok":
        in_range = [t for t in truth if (not f.start_date or t["date"] >= f.start_date.isoformat())
                    and (not f.end_date or t["date"] <= f.end_date.isoformat())]
        result["ground_truth_validation"] = an.validate_against_ground_truth(result["anomalies"], in_range)
    return result


@router.post("/detect", summary="Run anomaly detection on the filtered data")
def detect(body: DetectRequest, db: Session = Depends(get_db)):
    c = get_company(db)
    f = Filters.from_dict(body.filters.model_dump())
    result = run_detection(db, c.id, f, _cfg(db, c.id, body.config), c.is_synthetic)
    # Action Center: mark anomalies that already have an investigation (duplicate prevention in the UI)
    from ..models import Investigation
    from ..services.action_center import link_for_anomaly
    links = link_for_anomaly(db, c.id, [a["id"] for a in result.get("anomalies", [])])
    for a in result.get("anomalies", []):
        lk = links.get(a["id"])
        if lk:
            inv = db.get(Investigation, lk.investigation_id)
            a["investigation"] = {"id": inv.id, "number": inv.number, "status": inv.status}
    return result


@router.get("/series", summary="Daily metric series with rolling baseline for one machine or line")
def series(
    level: Literal["machine", "line"] = "machine",
    entity_id: int = Query(...),
    metric: Literal["defect_rate", "downtime_ratio", "target_achievement", "performance"] = "defect_rate",
    f: Filters = Depends(filters_dependency),
    db: Session = Depends(get_db),
):
    c = get_company(db)
    cfg = _cfg(db, c.id, None)
    sub = Filters(f.start_date, f.end_date, shift_ids=f.shift_ids, product_ids=f.product_ids)
    if level == "machine":
        sub.machine_ids = [entity_id]
    else:
        sub.line_ids = [entity_id]
    return {"level": level, "entity_id": entity_id, "metric": metric,
            "points": an.series(load_records(db, c.id, sub), level, entity_id, metric, cfg)}
