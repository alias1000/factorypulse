"""Collects all data for a report (shared by the PDF and Excel renderers)."""
from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...clock import local_now
from ...models import Company, Machine, Product, ProductionLine, ProductionRecord, Shift
from .. import analytics as an
from ..anomaly import detect
from ..data_access import Filters, load_defects, load_downtime, load_records
from ..kpi import aggregate, compute_kpis
from ..settings_store import get_setting
from .i18n import fmt_num, fmt_pct, fmt_pp, t

REPORT_TYPES = ("daily", "weekly", "monthly", "quality", "downtime")
KPI_KEYS = {
    "daily": ["total_production", "planned_production", "target_achievement", "good_units", "defective_units",
              "defect_rate", "unplanned_downtime_minutes", "availability", "performance", "quality", "oee"],
    "quality": ["total_production", "good_units", "defective_units", "defect_rate", "quality"],
    "downtime": ["unplanned_downtime_minutes", "planned_downtime_minutes", "planned_production_minutes",
                 "operating_minutes", "availability"],
}
KPI_KEYS["weekly"] = KPI_KEYS["monthly"] = KPI_KEYS["daily"]
RATIO_KEYS = {"target_achievement", "defect_rate", "availability", "performance", "quality", "oee"}
ANOMALY_METRICS = {"quality": ["defect_rate"], "downtime": ["downtime_ratio"]}
BASELINE_LOOKBACK_DAYS = 60


class ReportError(ValueError):
    pass


def resolve_period(report_type: str, start: dt.date, end: dt.date | None) -> tuple[dt.date, dt.date]:
    if report_type == "daily":
        return start, start
    if report_type == "weekly":
        s = start - dt.timedelta(days=start.weekday())
        return s, s + dt.timedelta(days=6)
    if report_type == "monthly":
        s = start.replace(day=1)
        return s, s.replace(day=calendar.monthrange(s.year, s.month)[1])
    if end is None:
        raise ReportError("end_date is required for this report type")
    if end < start:
        raise ReportError("end_date must not be before start_date")
    if (end - start).days > 366:
        raise ReportError("The reporting period must not exceed 366 days")
    return start, end


def previous_period(db: Session, company_id: int, report_type: str, s: dt.date, e: dt.date) -> tuple[dt.date, dt.date]:
    if report_type == "daily":
        prev = db.scalar(select(func.max(ProductionRecord.date)).where(
            ProductionRecord.company_id == company_id, ProductionRecord.date < s))
        p = prev or (s - dt.timedelta(days=1))
        return p, p
    if report_type == "monthly":
        pe = s - dt.timedelta(days=1)
        return pe.replace(day=1), pe
    length = (e - s).days + 1
    return s - dt.timedelta(days=length), s - dt.timedelta(days=1)


@dataclass
class ReportData:
    report_type: str
    lang: str
    title: str
    company_name: str
    is_synthetic: bool
    period: tuple[dt.date, dt.date]
    previous: tuple[dt.date, dt.date]
    filters_text: list[tuple[str, str]]
    generated_at: dt.datetime
    kpis: list[dict]
    comparison: list[dict]
    records: pd.DataFrame
    daily: pd.DataFrame
    by_line: pd.DataFrame
    by_machine: pd.DataFrame
    by_shift: pd.DataFrame
    by_product: pd.DataFrame
    defect_pareto: list[dict]
    downtime_pareto: list[dict]
    downtime_machines: pd.DataFrame
    anomalies: list[dict]
    anomaly_status: str
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def has_data(self) -> bool:
        return not self.records.empty


def _filters_text(db: Session, f: Filters, lang: str) -> list[tuple[str, str]]:
    def names(model, ids):
        if not ids:
            return t(lang, "all")
        rows = db.scalars(select(model).where(model.id.in_(ids))).all()
        return ", ".join(sorted(r.code for r in rows)) or t(lang, "all")
    return [
        (t(lang, "lines"), names(ProductionLine, f.line_ids)),
        (t(lang, "machines"), names(Machine, f.machine_ids)),
        (t(lang, "shifts"), names(Shift, f.shift_ids)),
        (t(lang, "products"), names(Product, f.product_ids)),
    ]


def _fmt_kpi(key: str, v, lang: str) -> str:
    if key in RATIO_KEYS:
        return fmt_pct(v, lang)
    return fmt_num(v, lang, 0)


def build(db: Session, company: Company, report_type: str, start: dt.date, end: dt.date | None,
          f: Filters, lang: str = "en", now: dt.datetime | None = None) -> ReportData:
    if report_type not in REPORT_TYPES:
        raise ReportError("Unknown report type")
    lang = lang if lang in ("en", "de") else "en"
    s, e = resolve_period(report_type, start, end)
    ps, pe = previous_period(db, company.id, report_type, s, e)
    cur_f, prev_f = f.shifted(s, e), f.shifted(ps, pe)
    records = load_records(db, company.id, cur_f)
    prev_records = load_records(db, company.id, prev_f)
    downtime = load_downtime(db, company.id, cur_f)
    defects = load_defects(db, company.id, cur_f)

    k = compute_kpis(records)
    kp = compute_kpis(prev_records)
    keys = KPI_KEYS[report_type]
    kpis = [{"key": key, "label": t(lang, key), "value": k[key]["value"], "display": _fmt_kpi(key, k[key]["value"], lang),
             "coverage": k[key]["coverage"]} for key in keys]
    comparison = []
    for key in keys:
        c, p = k[key]["value"], kp[key]["value"]
        if key in RATIO_KEYS:
            delta = None if c is None or p is None else c - p
            change = fmt_pp(delta, lang)
        else:
            delta = None if c is None or p in (None, 0) else (c - p) / p
            change = "N/A" if delta is None else (("+" if delta > 0 else "") + fmt_num(delta * 100, lang, 1) + " %")
        comparison.append({"key": key, "label": t(lang, key), "current": _fmt_kpi(key, c, lang),
                           "previous": _fmt_kpi(key, p, lang), "change": change, "current_raw": c,
                           "previous_raw": p, "delta_raw": delta, "is_ratio": key in RATIO_KEYS})

    daily = aggregate(records, ["date"])
    by_line = aggregate(records, an.DIMENSIONS["line"])
    by_machine = aggregate(records, an.DIMENSIONS["machine"])
    by_shift = aggregate(records, an.DIMENSIONS["shift"])
    if not by_shift.empty:
        by_shift = by_shift.sort_values("shift_order")
    by_product = aggregate(records, an.DIMENSIONS["product"])
    dt_machines = an.flag_high_downtime(by_machine) if not by_machine.empty else by_machine
    if not dt_machines.empty:
        dt_machines = dt_machines.sort_values("unplanned_downtime_minutes", ascending=False)

    # anomalies: baseline needs history before the period
    cfg = dict(get_setting(db, company.id, "anomaly_config"))
    if report_type in ANOMALY_METRICS:
        cfg["metrics"] = ANOMALY_METRICS[report_type]
    hist_f = f.shifted(s - dt.timedelta(days=BASELINE_LOOKBACK_DAYS), e)
    hist = load_records(db, company.id, hist_f)
    det = detect(hist, cfg, load_downtime(db, company.id, hist_f), load_defects(db, company.id, hist_f))
    anomalies = [a for a in det["anomalies"] if s.isoformat() <= a["date"] <= e.isoformat()][:12]

    return ReportData(
        report_type=report_type, lang=lang, title=t(lang, f"title.{report_type}"), company_name=company.name,
        is_synthetic=company.is_synthetic, period=(s, e), previous=(ps, pe),
        filters_text=_filters_text(db, f, lang), generated_at=now or local_now(),
        kpis=kpis, comparison=comparison, records=records, daily=daily, by_line=by_line, by_machine=by_machine,
        by_shift=by_shift, by_product=by_product, defect_pareto=an.pareto(defects, "category", "quantity"),
        downtime_pareto=an.pareto(downtime, "reason", "minutes"), downtime_machines=dt_machines,
        anomalies=anomalies, anomaly_status=det["status"],
        extra={"anomaly_message": det.get("message")},
    )


def fmt_date(d: dt.date, lang: str) -> str:
    return d.strftime("%d.%m.%Y") if lang == "de" else d.isoformat()


def period_text(r: ReportData) -> str:
    s, e = r.period
    if s == e:
        return fmt_date(s, r.lang)
    return f"{fmt_date(s, r.lang)} – {fmt_date(e, r.lang)}"
