"""Deterministic SYNTHETIC demo dataset.

Everything generated here is fictional and labelled as synthetic
(``Company.is_synthetic`` / ``ProductionRecord.is_synthetic``). It must never be
presented as real customer results.

Scenario: "Musterwerk Präzisionsteile GmbH" (fictional), 3 lines, 12 machines,
3 shifts, 6 products, 90 calendar days. Mon–Fri 3 shifts, Saturday early shift
only, Sunday no production.

A handful of anomalies are injected on purpose (see INJECTED_ANOMALIES) so the
anomaly detector can be validated against a known ground truth. Machine M-10 is
modelled as an older machine with chronically higher downtime.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import (
    AppSetting,
    Company,
    DefectEvent,
    DowntimeEvent,
    ImportJob,
    Machine,
    Product,
    ProductionLine,
    ProductionRecord,
    ReportJob,
    Shift,
)

DEMO_COMPANY_NAME = "Musterwerk Präzisionsteile GmbH (Synthetic Demo)"
DEMO_END_DATE = dt.date(2026, 9, 30)
DEMO_DAYS = 90
SEED = 20260930

LINES = [
    ("L1", "Linie 1 – CNC-Drehen"),
    ("L2", "Linie 2 – CNC-Fräsen"),
    ("L3", "Linie 3 – Montage & Prüfung"),
]
MACHINES = [
    # code, name, line, perf_base, downtime_events_lambda, defect_factor
    ("M-01", "Drehzentrum 01", "L1", 0.93, 1.0, 1.0),
    ("M-02", "Drehzentrum 02", "L1", 0.91, 1.0, 1.1),
    ("M-03", "Drehzentrum 03", "L1", 0.90, 1.1, 0.9),
    ("M-04", "Drehzentrum 04", "L1", 0.88, 1.2, 1.2),
    ("M-05", "Fräszentrum 05", "L2", 0.92, 1.0, 1.0),
    ("M-06", "Fräszentrum 06", "L2", 0.90, 1.1, 1.1),
    ("M-07", "Fräszentrum 07", "L2", 0.91, 0.9, 0.9),
    ("M-08", "Fräszentrum 08", "L2", 0.87, 1.3, 1.3),
    ("M-09", "Montagestation 09", "L3", 0.92, 0.9, 1.0),
    ("M-10", "Montagestation 10 (Altanlage)", "L3", 0.86, 2.6, 1.2),
    ("M-11", "Montagestation 11", "L3", 0.91, 1.0, 0.9),
    ("M-12", "Prüfstand 12", "L3", 0.93, 0.8, 0.8),
]
PRODUCTS = [
    # code, name, line, ideal cycle time [s/unit], base defect rate
    ("P-100", "Flanschwelle FW-20", "L1", 48.0, 0.015),
    ("P-110", "Gewindebolzen GB-12", "L1", 30.0, 0.010),
    ("P-200", "Lagergehäuse LG-40", "L2", 95.0, 0.020),
    ("P-210", "Montageplatte MP-8", "L2", 70.0, 0.014),
    ("P-300", "Antriebseinheit AE-3", "L3", 140.0, 0.022),
    ("P-310", "Sensormodul SM-1", "L3", 60.0, 0.016),
]
SHIFTS = [
    # code, name, start, end, perf factor, defect add
    ("F", "Frühschicht", "06:00", "14:00", 1.00, 0.000),
    ("S", "Spätschicht", "14:00", "22:00", 0.985, 0.002),
    ("N", "Nachtschicht", "22:00", "06:00", 0.96, 0.005),
]
DOWNTIME_REASONS = [
    # reason, weight, mean minutes, sigma (lognormal)
    ("Mechanical failure", 0.22, 28, 0.6),
    ("Electrical failure", 0.10, 22, 0.6),
    ("Tool change", 0.22, 9, 0.35),
    ("Setup / changeover", 0.12, 25, 0.4),
    ("Material shortage", 0.12, 18, 0.5),
    ("Quality inspection", 0.12, 10, 0.4),
    ("Staff shortage", 0.10, 20, 0.5),
]
DEFECT_CATEGORIES = {
    "L1": [("Dimensional deviation", 40), ("Surface defect", 25), ("Burr", 20), ("Material defect", 10), ("Other", 5)],
    "L2": [("Dimensional deviation", 35), ("Surface defect", 30), ("Burr", 25), ("Material defect", 10)],
    "L3": [("Assembly error", 45), ("Functional test failed", 25), ("Missing component", 20), ("Surface defect", 10)],
}
SCHEDULED_MIN = 480.0
BREAK_MIN = 30.0
PLANNING_FACTOR = 0.75  # production plan assumes 75 % of theoretical output


@dataclass(frozen=True)
class InjectedAnomaly:
    date: dt.date
    entity: str  # machine code or line code
    level: str  # machine | line
    metric: str
    description: str


INJECTED_ANOMALIES = [
    InjectedAnomaly(dt.date(2026, 8, 12), "M-06", "machine", "defect_rate",
                    "Injected defect spike (~9 % dimensional deviations) on all shifts"),
    InjectedAnomaly(dt.date(2026, 8, 26), "L3", "line", "target_achievement",
                    "Injected material shortage on Line 3 (≈180 min per shift) causing a production drop"),
    InjectedAnomaly(dt.date(2026, 9, 2), "M-02", "machine", "downtime_ratio",
                    "Injected 300 min mechanical breakdown in the night shift"),
    InjectedAnomaly(dt.date(2026, 9, 16), "M-07", "machine", "performance",
                    "Injected slow running (~55 % performance) without recorded downtime"),
]


def _work_shifts(day: dt.date) -> list[str]:
    wd = day.weekday()
    if wd < 5:
        return ["F", "S", "N"]
    if wd == 5:
        return ["F"]
    return []


def generate(end_date: dt.date = DEMO_END_DATE, days: int = DEMO_DAYS, seed: int = SEED) -> list[dict]:
    """Return a list of record dicts (with nested downtime / defect events)."""
    rng = np.random.default_rng(seed)
    start = end_date - dt.timedelta(days=days - 1)
    products_by_line: dict[str, list[tuple]] = {}
    for p in PRODUCTS:
        products_by_line.setdefault(p[2], []).append(p)
    shift_info = {s[0]: s for s in SHIFTS}
    reasons = [r[0] for r in DOWNTIME_REASONS]
    reason_w = np.array([r[1] for r in DOWNTIME_REASONS])
    reason_w = reason_w / reason_w.sum()
    reason_params = {r[0]: (r[2], r[3]) for r in DOWNTIME_REASONS}

    records: list[dict] = []
    prev_product: dict[str, str] = {}
    for d_i in range(days):
        day = start + dt.timedelta(days=d_i)
        week = day.isocalendar()[1]
        for m_idx, (m_code, _m_name, line, perf_base, dt_lambda, defect_factor) in enumerate(MACHINES):
            line_products = products_by_line[line]
            product = line_products[(week + m_idx) % len(line_products)]
            p_code, _p_name, _pl, ict, base_defect = product
            # slight slow drift in machine efficiency over the period
            drift = 0.01 * np.sin(2 * np.pi * d_i / 45 + m_idx)
            for s_code in _work_shifts(day):
                _, _, _, _, s_perf, s_def = shift_info[s_code]
                events: list[tuple[str, bool, float]] = []
                planned_dt = BREAK_MIN
                events.append(("Planned break", True, BREAK_MIN))
                if rng.random() < 0.04:
                    planned_dt += 60.0
                    events.append(("Planned maintenance", True, 60.0))
                ppt = SCHEDULED_MIN - planned_dt

                # unplanned stops
                n_ev = rng.poisson(dt_lambda * 1.7)
                changeover = prev_product.get(m_code) not in (None, p_code)
                if changeover:
                    events.append(("Setup / changeover", False, float(round(rng.normal(40, 8)))))
                for _ in range(n_ev):
                    r = rng.choice(reasons, p=reason_w)
                    mean, sig = reason_params[r]
                    mins = float(min(round(rng.lognormal(np.log(mean), sig)), 120))
                    events.append((r, False, max(mins, 2.0)))
                prev_product[m_code] = p_code

                perf = rng.normal(perf_base * s_perf + drift - 0.06, 0.025)
                defect_p = base_defect * defect_factor + s_def + rng.normal(0, 0.002)

                # ---- injected anomalies -------------------------------------
                if day == dt.date(2026, 8, 12) and m_code == "M-06":
                    defect_p = 0.09
                if day == dt.date(2026, 8, 26) and line == "L3":
                    events.append(("Material shortage", False, 180.0))
                if day == dt.date(2026, 9, 2) and m_code == "M-02" and s_code == "N":
                    events.append(("Mechanical failure", False, 300.0))
                if day == dt.date(2026, 9, 16) and m_code == "M-07":
                    perf = 0.55
                # --------------------------------------------------------------

                udt = sum(e[2] for e in events if not e[1])
                if udt > ppt * 0.85:  # keep physically possible
                    scale = (ppt * 0.85) / udt
                    events = [(r, pl, round(m * scale) if not pl else m) for r, pl, m in events]
                    udt = sum(e[2] for e in events if not e[1])
                operating = ppt - udt
                perf = float(np.clip(perf, 0.4, 0.99))
                actual = int(np.floor(operating * 60.0 / ict * perf))
                planned_qty = int(round(ppt * 60.0 / ict * PLANNING_FACTOR))
                defect_p = float(np.clip(defect_p, 0.0, 0.5))
                defective = int(rng.binomial(actual, defect_p)) if actual > 0 else 0
                good = actual - defective

                cats = DEFECT_CATEGORIES[line]
                if day == dt.date(2026, 8, 12) and m_code == "M-06":
                    cats = [("Dimensional deviation", 85), ("Surface defect", 10), ("Burr", 5)]
                cw = np.array([c[1] for c in cats], dtype=float)
                split = rng.multinomial(defective, cw / cw.sum()) if defective else np.zeros(len(cats), int)
                defects = [(cats[i][0], int(q)) for i, q in enumerate(split) if q > 0]

                # merge downtime events of same reason
                merged: dict[tuple[str, bool], float] = {}
                for r, pl, mins in events:
                    merged[(r, pl)] = merged.get((r, pl), 0.0) + float(mins)

                records.append(
                    {
                        "date": day,
                        "line": line,
                        "machine": m_code,
                        "shift": s_code,
                        "product": p_code,
                        "planned_qty": planned_qty,
                        "actual_qty": actual,
                        "good_qty": good,
                        "defective_qty": defective,
                        "scheduled_minutes": SCHEDULED_MIN,
                        "planned_downtime_minutes": planned_dt,
                        "planned_production_minutes": ppt,
                        "unplanned_downtime_minutes": float(udt),
                        "operating_minutes": float(operating),
                        "ideal_cycle_time_sec": ict,
                        "downtime": [(r, pl, m) for (r, pl), m in merged.items()],
                        "defects": defects,
                    }
                )
    return records


def clear_company_data(db: Session, company_id: int) -> None:
    from ..models import (ActionCenterAlert, CorrectiveAction, Investigation, InvestigationAnomalyLink,
                          InvestigationAttachment, InvestigationComment, InvestigationEvent)
    from ..models import EvidenceFileOp

    # evidence file-operation outbox (v0.3.3) – explicit, SQLite does not enforce ON DELETE CASCADE
    db.execute(delete(EvidenceFileOp).where(EvidenceFileOp.attachment_id.in_(
        select(InvestigationAttachment.id).where(InvestigationAttachment.company_id == company_id))))
    # Action Center first: investigations reference lines/machines with ON DELETE RESTRICT
    for model in (ActionCenterAlert, InvestigationEvent, InvestigationComment, InvestigationAttachment,
                  InvestigationAnomalyLink, CorrectiveAction, Investigation):
        db.execute(delete(model).where(model.company_id == company_id))
    for model in (DefectEvent, DowntimeEvent):
        db.execute(delete(model).where(model.company_id == company_id))
    db.execute(delete(ProductionRecord).where(ProductionRecord.company_id == company_id))
    for model in (ImportJob, ReportJob, Machine, Product, Shift, ProductionLine):
        db.execute(delete(model).where(model.company_id == company_id))
    db.execute(
        delete(AppSetting).where(
            AppSetting.company_id == company_id, AppSetting.key == "demo_injected_anomalies"
        )
    )


def load_demo(db: Session, company: Company) -> dict:
    """Replace all data of ``company`` with the synthetic demo dataset (one transaction)."""
    records = generate()
    clear_company_data(db, company.id)
    company.name = DEMO_COMPANY_NAME
    company.is_synthetic = True

    line_obj = {}
    for code, name in LINES:
        line_obj[code] = ProductionLine(company_id=company.id, code=code, name=name)
        db.add(line_obj[code])
    db.flush()
    mach_obj = {}
    for code, name, line, *_ in MACHINES:
        mach_obj[code] = Machine(company_id=company.id, code=code, name=name, line_id=line_obj[line].id)
        db.add(mach_obj[code])
    prod_obj = {}
    for code, name, *_ in PRODUCTS:
        prod_obj[code] = Product(company_id=company.id, code=code, name=name)
        db.add(prod_obj[code])
    shift_obj = {}
    for i, (code, name, start, end, *_rest) in enumerate(SHIFTS):
        shift_obj[code] = Shift(
            company_id=company.id, code=code, name=name, start_time=start, end_time=end, sort_order=i
        )
        db.add(shift_obj[code])
    db.flush()

    n_dt = n_def = 0
    for r in records:
        rec = ProductionRecord(
            company_id=company.id,
            date=r["date"],
            line_id=line_obj[r["line"]].id,
            machine_id=mach_obj[r["machine"]].id,
            shift_id=shift_obj[r["shift"]].id,
            product_id=prod_obj[r["product"]].id,
            planned_qty=r["planned_qty"],
            actual_qty=r["actual_qty"],
            good_qty=r["good_qty"],
            defective_qty=r["defective_qty"],
            scheduled_minutes=r["scheduled_minutes"],
            planned_downtime_minutes=r["planned_downtime_minutes"],
            planned_production_minutes=r["planned_production_minutes"],
            unplanned_downtime_minutes=r["unplanned_downtime_minutes"],
            operating_minutes=r["operating_minutes"],
            ideal_cycle_time_sec=r["ideal_cycle_time_sec"],
            is_synthetic=True,
        )
        rec.downtime_events = [
            DowntimeEvent(company_id=company.id, reason=reason, is_planned=pl, minutes=m)
            for reason, pl, m in r["downtime"]
        ]
        rec.defect_events = [
            DefectEvent(company_id=company.id, category=c, quantity=q) for c, q in r["defects"]
        ]
        n_dt += len(rec.downtime_events)
        n_def += len(rec.defect_events)
        db.add(rec)

    db.add(
        AppSetting(
            company_id=company.id,
            key="demo_injected_anomalies",
            value=[
                {"date": a.date.isoformat(), "entity": a.entity, "level": a.level, "metric": a.metric,
                 "description": a.description}
                for a in INJECTED_ANOMALIES
            ],
        )
    )
    db.flush()
    from .demo_action_center import seed as seed_action_center
    ac_stats = seed_action_center(db, company)
    return {
        "action_center": ac_stats,
        "records": len(records),
        "downtime_events": n_dt,
        "defect_events": n_def,
        "start_date": records[0]["date"].isoformat(),
        "end_date": records[-1]["date"].isoformat(),
    }


def company_has_data(db: Session, company_id: int) -> bool:
    return db.scalar(select(ProductionRecord.id).where(ProductionRecord.company_id == company_id).limit(1)) is not None
