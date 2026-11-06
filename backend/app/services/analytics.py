"""Analytics built on top of the KPI engine (overview, production, quality, downtime)."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from .kpi import aggregate, compute_kpis, to_jsonable

DIMENSIONS = {
    "day": ["date"],
    "week": ["week"],
    "line": ["line_id", "line_code", "line_name"],
    "machine": ["machine_id", "machine_code", "machine_name", "line_code"],
    "shift": ["shift_id", "shift_code", "shift_name", "shift_order"],
    "product": ["product_id", "product_code", "product_name"],
}


def _with_week(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.assign(week=pd.Series(dtype=str))
    iso = df["date"].dt.isocalendar()
    return df.assign(week=iso["year"].astype(str) + "-W" + iso["week"].astype(int).map("{:02d}".format))


def breakdown(df: pd.DataFrame, dimension: str) -> list[dict]:
    if dimension not in DIMENSIONS:
        raise ValueError(f"Unknown dimension '{dimension}'")
    if dimension == "week":
        df = _with_week(df)
    g = aggregate(df, DIMENSIONS[dimension])
    if dimension == "shift" and not g.empty:
        g = g.sort_values("shift_order")
    return to_jsonable(g)


def overview(records: pd.DataFrame, downtime: pd.DataFrame) -> dict:
    return {
        "kpis": compute_kpis(records),
        "daily": breakdown(records, "day"),
        "by_line": breakdown(records, "line"),
        "by_shift": breakdown(records, "shift"),
        "downtime_by_reason": downtime_by(downtime, ["reason"]),
    }


def period_comparison(current: pd.DataFrame, previous: pd.DataFrame,
                      cur_range: tuple[dt.date, dt.date], prev_range: tuple[dt.date, dt.date]) -> dict:
    cur = compute_kpis(current)
    prev = compute_kpis(previous)
    keys = ["total_production", "planned_production", "target_achievement", "defect_rate", "quality",
            "availability", "performance", "oee", "unplanned_downtime_minutes"]
    rows = []
    for k in keys:
        c, p = cur[k]["value"], prev[k]["value"]
        delta = None if c is None or p is None else c - p
        rel = None if delta is None or not p else delta / abs(p)
        rows.append({"metric": k, "unit": cur[k]["unit"], "current": c, "previous": p,
                     "delta": delta, "relative_change": rel})
    # productivity: units per operating hour
    def uph(df: pd.DataFrame) -> float | None:
        if df.empty:
            return None
        m = df["operating_minutes"].notna()
        op = df.loc[m, "operating_minutes"].sum()
        return float(df.loc[m, "actual_qty"].sum() / (op / 60.0)) if op > 0 else None
    cu, pu = uph(current), uph(previous)
    d = None if cu is None or pu is None else cu - pu
    rows.append({"metric": "units_per_operating_hour", "unit": "units/h", "current": cu, "previous": pu,
                 "delta": d, "relative_change": None if d is None or not pu else d / pu})
    return {
        "current_period": {"start": cur_range[0].isoformat(), "end": cur_range[1].isoformat(),
                           "records": cur["record_count"]},
        "previous_period": {"start": prev_range[0].isoformat(), "end": prev_range[1].isoformat(),
                            "records": prev["record_count"]},
        "metrics": rows,
    }


# ----------------------------------------------------------------------------- quality

def pareto(df: pd.DataFrame, cat_col: str, val_col: str) -> list[dict]:
    if df.empty:
        return []
    g = df.groupby(cat_col)[val_col].sum().sort_values(ascending=False)
    g = g[g > 0]
    total = g.sum()
    if total <= 0:
        return []
    out = []
    cum = 0.0
    for cat, v in g.items():
        cum += v
        out.append({"category": cat, "value": float(v), "share": float(v / total), "cumulative_share": float(cum / total)})
    return out


def quality_summary(records: pd.DataFrame, defects: pd.DataFrame) -> dict:
    k = compute_kpis(records)
    daily = aggregate(records, ["date"])[["date", "actual_qty", "defective_qty", "defect_rate"]] if not records.empty else pd.DataFrame()

    def by(dim: str) -> list[dict]:
        g = aggregate(records, DIMENSIONS[dim])
        if g.empty:
            return []
        cols = DIMENSIONS[dim] + ["actual_qty", "good_qty", "defective_qty", "defect_rate", "quality"]
        g = g[cols].sort_values("defect_rate", ascending=False, na_position="last")
        return to_jsonable(g)

    classified = float(defects["quantity"].sum()) if not defects.empty else 0.0
    total_def = k["defective_units"]["value"] or 0.0
    return {
        "kpis": {key: k[key] for key in ("total_production", "good_units", "defective_units", "defect_rate", "quality")},
        "daily": to_jsonable(daily),
        "by_machine": by("machine"),
        "by_line": by("line"),
        "by_product": by("product"),
        "by_shift": by("shift"),
        "pareto": pareto(defects, "category", "quantity"),
        "categorized_defects": classified,
        "uncategorized_defects": max(total_def - classified, 0.0),
    }


# ----------------------------------------------------------------------------- downtime

def downtime_by(downtime: pd.DataFrame, by: list[str]) -> list[dict]:
    if downtime.empty:
        return []
    g = downtime.groupby(by, dropna=False).agg(minutes=("minutes", "sum"), events=("id", "count")).reset_index()
    g = g.sort_values("minutes", ascending=False)
    return to_jsonable(g)


def flag_high_downtime(machine_rows: pd.DataFrame) -> pd.DataFrame:
    """Flag machines whose unplanned-downtime ratio is an upper outlier (Tukey: > Q3 + 1.5·IQR)."""
    m = machine_rows.copy()
    m["downtime_ratio"] = m["unplanned_downtime_minutes"] / m["planned_production_minutes"].where(
        m["planned_production_minutes"] > 0)
    vals = m["downtime_ratio"].dropna()
    m["is_high_downtime"] = False
    m["fleet_median_ratio"] = np.nan
    m["upper_fence"] = np.nan
    if len(vals) >= 4:
        q1, q3 = np.percentile(vals, [25, 75])
        fence = q3 + 1.5 * (q3 - q1)
        med = float(np.median(vals))
        m["fleet_median_ratio"] = med
        m["upper_fence"] = fence
        m["is_high_downtime"] = m["downtime_ratio"] > fence
    return m


def downtime_summary(records: pd.DataFrame, downtime: pd.DataFrame) -> dict:
    k = compute_kpis(records)
    mach = aggregate(records, DIMENSIONS["machine"])
    if not mach.empty:
        mach = flag_high_downtime(mach)
        mach = mach[DIMENSIONS["machine"] + ["unplanned_downtime_minutes", "planned_production_minutes",
                                             "downtime_ratio", "availability", "is_high_downtime",
                                             "fleet_median_ratio", "upper_fence"]]
        mach = mach.sort_values("unplanned_downtime_minutes", ascending=False)

    def by_dim(dim: str) -> list[dict]:
        g = aggregate(records, DIMENSIONS[dim])
        if g.empty:
            return []
        g["downtime_ratio"] = g["unplanned_downtime_minutes"] / g["planned_production_minutes"].where(
            g["planned_production_minutes"] > 0)
        cols = DIMENSIONS[dim] + ["unplanned_downtime_minutes", "planned_production_minutes", "downtime_ratio", "availability"]
        return to_jsonable(g[cols])

    daily = aggregate(records, ["date"])
    daily_out = []
    if not daily.empty:
        daily["downtime_ratio"] = daily["unplanned_downtime_minutes"] / daily["planned_production_minutes"].where(
            daily["planned_production_minutes"] > 0)
        daily_out = to_jsonable(daily[["date", "unplanned_downtime_minutes", "planned_production_minutes", "downtime_ratio", "availability"]])

    by_reason_daily = []
    if not downtime.empty:
        p = downtime.groupby(["date", "reason"])["minutes"].sum().reset_index()
        by_reason_daily = to_jsonable(p)

    return {
        "kpis": {key: k[key] for key in ("unplanned_downtime_minutes", "planned_downtime_minutes",
                                         "planned_production_minutes", "operating_minutes", "availability")},
        "event_count": int(len(downtime)),
        "by_reason": downtime_by(downtime, ["reason"]),
        "pareto": pareto(downtime, "reason", "minutes"),
        "by_machine": to_jsonable(mach) if not mach.empty else [],
        "by_line": by_dim("line"),
        "by_shift": by_dim("shift"),
        "daily": daily_out,
        "daily_by_reason": by_reason_daily,
    }
