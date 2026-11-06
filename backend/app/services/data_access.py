"""Query helpers: filters and DataFrame loaders shared by analytics, reports, anomalies."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pandas as pd
from fastapi import Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Company,
    DefectEvent,
    DowntimeEvent,
    Machine,
    Product,
    ProductionLine,
    ProductionRecord,
    Shift,
)


@dataclass
class Filters:
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    line_ids: list[int] = field(default_factory=list)
    machine_ids: list[int] = field(default_factory=list)
    shift_ids: list[int] = field(default_factory=list)
    product_ids: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "line_ids": self.line_ids,
            "machine_ids": self.machine_ids,
            "shift_ids": self.shift_ids,
            "product_ids": self.product_ids,
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> Filters:
        d = d or {}
        return cls(
            start_date=dt.date.fromisoformat(d["start_date"]) if d.get("start_date") else None,
            end_date=dt.date.fromisoformat(d["end_date"]) if d.get("end_date") else None,
            line_ids=list(d.get("line_ids") or []),
            machine_ids=list(d.get("machine_ids") or []),
            shift_ids=list(d.get("shift_ids") or []),
            product_ids=list(d.get("product_ids") or []),
        )

    def shifted(self, start: dt.date, end: dt.date) -> Filters:
        return Filters(start, end, self.line_ids, self.machine_ids, self.shift_ids, self.product_ids)


def filters_dependency(
    start_date: dt.date | None = Query(None, description="Inclusive start date (YYYY-MM-DD)"),
    end_date: dt.date | None = Query(None, description="Inclusive end date (YYYY-MM-DD)"),
    line_id: list[int] = Query(default_factory=list, description="Production line id(s)"),
    machine_id: list[int] = Query(default_factory=list, description="Machine id(s)"),
    shift_id: list[int] = Query(default_factory=list, description="Shift id(s)"),
    product_id: list[int] = Query(default_factory=list, description="Product id(s)"),
) -> Filters:
    return Filters(start_date, end_date, line_id, machine_id, shift_id, product_id)


def get_company(db: Session) -> Company:
    """Company of the authenticated user (set by the auth dependency); falls back to the single
    company for unauthenticated system tasks (startup seeding, CLI)."""
    from .auth import ctx

    cid = ctx().get("company_id")
    if cid is not None:
        company = db.get(Company, cid)
        if company is not None:
            return company
    company = db.scalar(select(Company).order_by(Company.id).limit(1))
    if company is None:
        company = Company(name="My Manufacturing Company", is_synthetic=False)
        db.add(company)
        db.commit()
        db.refresh(company)
    return company


def _apply_filters(stmt, company_id: int, f: Filters):
    R = ProductionRecord
    stmt = stmt.where(R.company_id == company_id)
    if f.start_date:
        stmt = stmt.where(R.date >= f.start_date)
    if f.end_date:
        stmt = stmt.where(R.date <= f.end_date)
    if f.line_ids:
        stmt = stmt.where(R.line_id.in_(f.line_ids))
    if f.machine_ids:
        stmt = stmt.where(R.machine_id.in_(f.machine_ids))
    if f.shift_ids:
        stmt = stmt.where(R.shift_id.in_(f.shift_ids))
    if f.product_ids:
        stmt = stmt.where(R.product_id.in_(f.product_ids))
    return stmt


RECORD_SELECT_COLS = [
    ProductionRecord.id,
    ProductionRecord.date,
    ProductionRecord.line_id,
    ProductionRecord.machine_id,
    ProductionRecord.shift_id,
    ProductionRecord.product_id,
    ProductionRecord.planned_qty,
    ProductionRecord.actual_qty,
    ProductionRecord.good_qty,
    ProductionRecord.defective_qty,
    ProductionRecord.scheduled_minutes,
    ProductionRecord.planned_downtime_minutes,
    ProductionRecord.planned_production_minutes,
    ProductionRecord.unplanned_downtime_minutes,
    ProductionRecord.operating_minutes,
    ProductionRecord.ideal_cycle_time_sec,
    ProductionLine.code.label("line_code"),
    ProductionLine.name.label("line_name"),
    Machine.code.label("machine_code"),
    Machine.name.label("machine_name"),
    Shift.code.label("shift_code"),
    Shift.name.label("shift_name"),
    Shift.sort_order.label("shift_order"),
    Product.code.label("product_code"),
    Product.name.label("product_name"),
]


def _joined(stmt):
    R = ProductionRecord
    return (
        stmt.join(ProductionLine, ProductionLine.id == R.line_id)
        .join(Machine, Machine.id == R.machine_id)
        .join(Shift, Shift.id == R.shift_id)
        .join(Product, Product.id == R.product_id)
    )


def load_records(db: Session, company_id: int, f: Filters) -> pd.DataFrame:
    stmt = _apply_filters(_joined(select(*RECORD_SELECT_COLS)), company_id, f)
    stmt = stmt.order_by(ProductionRecord.date, ProductionRecord.machine_id, ProductionRecord.shift_id)
    rows = db.execute(stmt).mappings().all()
    df = pd.DataFrame(rows, columns=[c.key for c in RECORD_SELECT_COLS])
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def load_downtime(db: Session, company_id: int, f: Filters, include_planned: bool = False) -> pd.DataFrame:
    cols = [
        DowntimeEvent.id,
        DowntimeEvent.reason,
        DowntimeEvent.is_planned,
        DowntimeEvent.minutes,
        DowntimeEvent.record_id,
    ] + RECORD_SELECT_COLS[1:6] + RECORD_SELECT_COLS[16:]
    stmt = _joined(select(*cols).join(ProductionRecord, ProductionRecord.id == DowntimeEvent.record_id))
    stmt = _apply_filters(stmt, company_id, f)
    if not include_planned:
        stmt = stmt.where(DowntimeEvent.is_planned.is_(False))
    rows = db.execute(stmt.order_by(ProductionRecord.date)).mappings().all()
    df = pd.DataFrame(rows, columns=[c.key for c in cols])
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def load_defects(db: Session, company_id: int, f: Filters) -> pd.DataFrame:
    cols = [
        DefectEvent.id,
        DefectEvent.category,
        DefectEvent.quantity,
        DefectEvent.record_id,
    ] + RECORD_SELECT_COLS[1:6] + RECORD_SELECT_COLS[16:]
    stmt = _joined(select(*cols).join(ProductionRecord, ProductionRecord.id == DefectEvent.record_id))
    stmt = _apply_filters(stmt, company_id, f)
    rows = db.execute(stmt.order_by(ProductionRecord.date)).mappings().all()
    df = pd.DataFrame(rows, columns=[c.key for c in cols])
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def data_date_range(db: Session, company_id: int) -> tuple[dt.date | None, dt.date | None, int]:
    row = db.execute(
        select(
            func.min(ProductionRecord.date), func.max(ProductionRecord.date), func.count(ProductionRecord.id)
        ).where(ProductionRecord.company_id == company_id)
    ).one()
    return row[0], row[1], int(row[2])
