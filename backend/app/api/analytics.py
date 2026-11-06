"""Read-only analytics endpoints: meta data, KPIs, overview, production, quality, downtime."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from .deps import require
from ..models import DowntimeEvent, Machine, Product, ProductionLine, ProductionRecord, Shift
from ..services import analytics as an
from ..services.data_access import (
    Filters,
    data_date_range,
    filters_dependency,
    get_company,
    load_defects,
    load_downtime,
    load_records,
)
from ..services.kpi import compute_kpis

router = APIRouter(prefix="/api", tags=["analytics"], dependencies=[Depends(require("data.read"))])


@router.get("/meta", summary="Company, master data and available data range")
def meta(db: Session = Depends(get_db)):
    c = get_company(db)
    start, end, n = data_date_range(db, c.id)
    lines = db.scalars(select(ProductionLine).where(ProductionLine.company_id == c.id).order_by(ProductionLine.code)).all()
    machines = db.scalars(select(Machine).where(Machine.company_id == c.id).order_by(Machine.code)).all()
    shifts = db.scalars(select(Shift).where(Shift.company_id == c.id).order_by(Shift.sort_order, Shift.code)).all()
    products = db.scalars(select(Product).where(Product.company_id == c.id).order_by(Product.code)).all()
    return {
        "company": {"id": c.id, "name": c.name, "is_synthetic": c.is_synthetic},
        "data_range": {"start": start.isoformat() if start else None, "end": end.isoformat() if end else None,
                       "record_count": n},
        "lines": [{"id": x.id, "code": x.code, "name": x.name} for x in lines],
        "machines": [{"id": x.id, "code": x.code, "name": x.name, "line_id": x.line_id} for x in machines],
        "shifts": [{"id": x.id, "code": x.code, "name": x.name, "start_time": x.start_time, "end_time": x.end_time}
                   for x in shifts],
        "products": [{"id": x.id, "code": x.code, "name": x.name} for x in products],
    }


@router.get("/kpis", summary="KPI summary for the filtered records")
def kpis(f: Filters = Depends(filters_dependency), db: Session = Depends(get_db)):
    c = get_company(db)
    return compute_kpis(load_records(db, c.id, f))


@router.get("/overview", summary="Overview dashboard: KPIs + chart series")
def overview(f: Filters = Depends(filters_dependency), db: Session = Depends(get_db)):
    c = get_company(db)
    return an.overview(load_records(db, c.id, f), load_downtime(db, c.id, f))


@router.get("/production/breakdown", summary="Production KPIs grouped by a dimension")
def production_breakdown(
    dimension: str = Query("day", pattern="^(day|week|line|machine|shift|product)$"),
    f: Filters = Depends(filters_dependency),
    db: Session = Depends(get_db),
):
    c = get_company(db)
    return {"dimension": dimension, "rows": an.breakdown(load_records(db, c.id, f), dimension)}


@router.get("/production/comparison", summary="Selected period vs. the preceding period of equal length")
def production_comparison(f: Filters = Depends(filters_dependency), db: Session = Depends(get_db)):
    c = get_company(db)
    start, end, _ = data_date_range(db, c.id)
    s = f.start_date or start
    e = f.end_date or end
    if s is None or e is None:
        raise HTTPException(404, "No production data available")
    if e < s:
        raise HTTPException(422, "end_date must not be before start_date")
    length = (e - s).days + 1
    ps, pe = s - dt.timedelta(days=length), s - dt.timedelta(days=1)
    cur = load_records(db, c.id, f.shifted(s, e))
    prev = load_records(db, c.id, f.shifted(ps, pe))
    return an.period_comparison(cur, prev, (s, e), (ps, pe))


@router.get("/quality", summary="Quality dashboard data incl. Pareto")
def quality(f: Filters = Depends(filters_dependency), db: Session = Depends(get_db)):
    c = get_company(db)
    return an.quality_summary(load_records(db, c.id, f), load_defects(db, c.id, f))


@router.get("/downtime", summary="Downtime dashboard data incl. Pareto and high-downtime machines")
def downtime(f: Filters = Depends(filters_dependency), db: Session = Depends(get_db)):
    c = get_company(db)
    return an.downtime_summary(load_records(db, c.id, f), load_downtime(db, c.id, f))


SORTABLE_EVENT_COLS = {
    "date": ProductionRecord.date,
    "minutes": DowntimeEvent.minutes,
    "reason": DowntimeEvent.reason,
    "machine": Machine.code,
    "line": ProductionLine.code,
    "shift": Shift.sort_order,
}


@router.get("/downtime/events", summary="Paginated, searchable downtime event table")
def downtime_events(
    f: Filters = Depends(filters_dependency),
    search: str | None = Query(None, max_length=100),
    sort: str = Query("date", pattern="^(date|minutes|reason|machine|line|shift)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=500),
    include_planned: bool = False,
    db: Session = Depends(get_db),
):
    c = get_company(db)
    R = ProductionRecord
    base = (
        select(
            DowntimeEvent.id, R.date, ProductionLine.code.label("line_code"), Machine.code.label("machine_code"),
            Machine.name.label("machine_name"), Shift.code.label("shift_code"), Product.code.label("product_code"),
            DowntimeEvent.reason, DowntimeEvent.is_planned, DowntimeEvent.minutes,
        )
        .join(R, R.id == DowntimeEvent.record_id)
        .join(ProductionLine, ProductionLine.id == R.line_id)
        .join(Machine, Machine.id == R.machine_id)
        .join(Shift, Shift.id == R.shift_id)
        .join(Product, Product.id == R.product_id)
        .where(R.company_id == c.id)
    )
    if f.start_date:
        base = base.where(R.date >= f.start_date)
    if f.end_date:
        base = base.where(R.date <= f.end_date)
    for ids, col in ((f.line_ids, R.line_id), (f.machine_ids, R.machine_id), (f.shift_ids, R.shift_id),
                     (f.product_ids, R.product_id)):
        if ids:
            base = base.where(col.in_(ids))
    if not include_planned:
        base = base.where(DowntimeEvent.is_planned.is_(False))
    if search:
        like = f"%{search.lower()}%"
        base = base.where(or_(func.lower(DowntimeEvent.reason).like(like), func.lower(Machine.code).like(like),
                              func.lower(Machine.name).like(like), func.lower(ProductionLine.code).like(like)))
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    col = SORTABLE_EVENT_COLS[sort]
    base = base.order_by(col.desc() if order == "desc" else col.asc(), DowntimeEvent.id)
    rows = db.execute(base.offset((page - 1) * page_size).limit(page_size)).mappings().all()
    return {
        "total": total, "page": page, "page_size": page_size,
        "rows": [{**r, "date": r["date"].isoformat()} for r in rows],
    }
