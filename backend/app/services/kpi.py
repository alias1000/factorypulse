"""Manufacturing KPI engine.

All ratios are returned as decimal fractions (0.853 == 85.3 %). Conversion to
percent happens only in the presentation layer (frontend / reports).

Definitions (see docs/KPI_DEFINITIONS.md):

* Target achievement = Σ actual_qty / Σ planned_qty
* Defect rate        = Σ defective_qty / Σ actual_qty
* Quality            = Σ good_qty / Σ actual_qty
* Availability       = Σ operating_min / Σ planned_production_min
* Performance        = Σ (ideal_cycle_time_sec / 60 × actual_qty) / Σ operating_min
* OEE                = Availability × Performance × Quality, computed on the
                       subset of records where *all* inputs exist.

Each KPI is aggregated only over records that actually contain its inputs
(never treating missing values as zero). Each result reports how many records
contributed ("coverage"). If no record contributes or the denominator is zero
the value is ``None`` and is displayed as "N/A".
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

RECORD_COLUMNS = [
    "planned_qty",
    "actual_qty",
    "good_qty",
    "defective_qty",
    "scheduled_minutes",
    "planned_downtime_minutes",
    "planned_production_minutes",
    "unplanned_downtime_minutes",
    "operating_minutes",
    "ideal_cycle_time_sec",
]


@dataclass
class Metric:
    value: float | None
    records_used: int
    records_total: int
    unit: str  # "units" | "minutes" | "ratio"

    @property
    def coverage(self) -> float | None:
        if self.records_total == 0:
            return None
        return self.records_used / self.records_total

    def to_dict(self) -> dict:
        d = asdict(self)
        d["coverage"] = self.coverage
        return d


def safe_ratio(num: float | None, den: float | None) -> float | None:
    """Division that returns None instead of raising / producing inf or NaN."""
    if num is None or den is None:
        return None
    if isinstance(num, float) and np.isnan(num):
        return None
    if isinstance(den, float) and np.isnan(den):
        return None
    if den == 0:
        return None
    return float(num) / float(den)


def _ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in RECORD_COLUMNS:
        if c not in df.columns:
            df[c] = np.nan
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    return df


def _sum(series: pd.Series) -> float | None:
    if series.notna().sum() == 0:
        return None
    return float(series.sum(skipna=True))


def compute_kpis(df: pd.DataFrame) -> dict[str, dict]:
    """Compute the KPI summary for a set of production records."""
    df = _ensure_columns(df)
    n = len(df)
    out: dict[str, Metric] = {}

    actual = df["actual_qty"]
    out["total_production"] = Metric(_sum(actual) if n else None, int(actual.notna().sum()), n, "units")

    m_plan = df["planned_qty"].notna() & actual.notna()
    planned_sum = _sum(df.loc[m_plan, "planned_qty"])
    out["planned_production"] = Metric(planned_sum, int(m_plan.sum()), n, "units")
    out["target_achievement"] = Metric(
        safe_ratio(_sum(df.loc[m_plan, "actual_qty"]), planned_sum), int(m_plan.sum()), n, "ratio"
    )

    m_good = df["good_qty"].notna() & actual.notna()
    m_def = df["defective_qty"].notna() & actual.notna()
    out["good_units"] = Metric(_sum(df.loc[m_good, "good_qty"]), int(m_good.sum()), n, "units")
    out["defective_units"] = Metric(_sum(df.loc[m_def, "defective_qty"]), int(m_def.sum()), n, "units")
    out["defect_rate"] = Metric(
        safe_ratio(_sum(df.loc[m_def, "defective_qty"]), _sum(df.loc[m_def, "actual_qty"])),
        int(m_def.sum()),
        n,
        "ratio",
    )
    out["quality"] = Metric(
        safe_ratio(_sum(df.loc[m_good, "good_qty"]), _sum(df.loc[m_good, "actual_qty"])),
        int(m_good.sum()),
        n,
        "ratio",
    )

    m_udt = df["unplanned_downtime_minutes"].notna()
    out["unplanned_downtime_minutes"] = Metric(
        _sum(df.loc[m_udt, "unplanned_downtime_minutes"]), int(m_udt.sum()), n, "minutes"
    )
    m_pdt = df["planned_downtime_minutes"].notna()
    out["planned_downtime_minutes"] = Metric(
        _sum(df.loc[m_pdt, "planned_downtime_minutes"]), int(m_pdt.sum()), n, "minutes"
    )
    m_ppt = df["planned_production_minutes"].notna()
    out["planned_production_minutes"] = Metric(
        _sum(df.loc[m_ppt, "planned_production_minutes"]), int(m_ppt.sum()), n, "minutes"
    )
    m_op = df["operating_minutes"].notna()
    out["operating_minutes"] = Metric(_sum(df.loc[m_op, "operating_minutes"]), int(m_op.sum()), n, "minutes")

    m_av = m_ppt & m_op
    out["availability"] = Metric(
        safe_ratio(
            _sum(df.loc[m_av, "operating_minutes"]), _sum(df.loc[m_av, "planned_production_minutes"])
        ),
        int(m_av.sum()),
        n,
        "ratio",
    )

    m_perf = m_op & df["ideal_cycle_time_sec"].notna() & actual.notna()
    ideal_min = (df["ideal_cycle_time_sec"] / 60.0) * actual
    out["performance"] = Metric(
        safe_ratio(_sum(ideal_min[m_perf]), _sum(df.loc[m_perf, "operating_minutes"])),
        int(m_perf.sum()),
        n,
        "ratio",
    )

    # OEE strictly on the subset where every factor is available.
    m_oee = m_av & m_perf & m_good
    sub = df.loc[m_oee]
    a = safe_ratio(_sum(sub["operating_minutes"]), _sum(sub["planned_production_minutes"]))
    p = safe_ratio(_sum(ideal_min[m_oee]), _sum(sub["operating_minutes"]))
    q = safe_ratio(_sum(sub["good_qty"]), _sum(sub["actual_qty"]))
    oee = a * p * q if (a is not None and p is not None and q is not None) else None
    out["oee"] = Metric(oee, int(m_oee.sum()), n, "ratio")

    result = {k: v.to_dict() for k, v in out.items()}
    result["oee"]["components"] = {"availability": a, "performance": p, "quality": q}
    result["record_count"] = n
    return result


def aggregate(df: pd.DataFrame, by: list[str] | str) -> pd.DataFrame:
    """Grouped KPI table. Ratios are NaN when not computable for a group."""
    by = [by] if isinstance(by, str) else list(by)
    df = _ensure_columns(df)
    if df.empty:
        cols = by + [
            "records", "actual_qty", "planned_qty", "good_qty", "defective_qty",
            "unplanned_downtime_minutes", "planned_production_minutes", "operating_minutes",
            "target_achievement", "defect_rate", "quality", "availability", "performance", "oee",
            "units_per_operating_hour",
        ]
        return pd.DataFrame(columns=cols)

    a = df["actual_qty"]
    w = pd.DataFrame(index=df.index)
    for c in by:
        w[c] = df[c]
    w["records"] = 1
    w["actual_qty"] = a
    m_plan = df["planned_qty"].notna() & a.notna()
    w["planned_qty"] = df["planned_qty"].where(m_plan)
    w["_actual_planned"] = a.where(m_plan)
    m_good = df["good_qty"].notna() & a.notna()
    w["good_qty"] = df["good_qty"].where(m_good)
    w["_actual_good"] = a.where(m_good)
    m_def = df["defective_qty"].notna() & a.notna()
    w["defective_qty"] = df["defective_qty"].where(m_def)
    w["_actual_def"] = a.where(m_def)
    w["unplanned_downtime_minutes"] = df["unplanned_downtime_minutes"]
    w["planned_production_minutes"] = df["planned_production_minutes"]
    w["operating_minutes"] = df["operating_minutes"]
    m_av = df["planned_production_minutes"].notna() & df["operating_minutes"].notna()
    w["_op_av"] = df["operating_minutes"].where(m_av)
    w["_ppt_av"] = df["planned_production_minutes"].where(m_av)
    m_perf = df["operating_minutes"].notna() & df["ideal_cycle_time_sec"].notna() & a.notna()
    ideal = (df["ideal_cycle_time_sec"] / 60.0) * a
    w["_ideal_perf"] = ideal.where(m_perf)
    w["_op_perf"] = df["operating_minutes"].where(m_perf)
    m_oee = m_av & m_perf & m_good
    w["_oee_op"] = df["operating_minutes"].where(m_oee)
    w["_oee_ppt"] = df["planned_production_minutes"].where(m_oee)
    w["_oee_ideal"] = ideal.where(m_oee)
    w["_oee_good"] = df["good_qty"].where(m_oee)
    w["_oee_actual"] = a.where(m_oee)
    w["_uph_actual"] = a.where(df["operating_minutes"].notna())

    g = w.groupby(by, dropna=False, sort=True).sum(min_count=1)

    def ratio(num: str, den: str) -> pd.Series:
        d = g[den]
        return (g[num] / d.where(d > 0)).astype(float)

    g["target_achievement"] = ratio("_actual_planned", "planned_qty")
    g["defect_rate"] = ratio("defective_qty", "_actual_def")
    g["quality"] = ratio("good_qty", "_actual_good")
    g["availability"] = ratio("_op_av", "_ppt_av")
    g["performance"] = ratio("_ideal_perf", "_op_perf")
    oa = ratio("_oee_op", "_oee_ppt")
    op = ratio("_oee_ideal", "_oee_op")
    oq = ratio("_oee_good", "_oee_actual")
    g["oee"] = oa * op * oq
    opm = g["operating_minutes"]
    g["units_per_operating_hour"] = (g["_uph_actual"] / (opm.where(opm > 0) / 60.0)).astype(float)
    g = g.drop(columns=[c for c in g.columns if c.startswith("_")])
    return g.reset_index()


def to_jsonable(df: pd.DataFrame) -> list[dict]:
    """Convert a DataFrame to JSON-safe records (NaN -> None, dates -> iso)."""
    if df.empty:
        return []
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[c]):
            out[c] = out[c].dt.strftime("%Y-%m-%d")
    out = out.astype(object).where(pd.notna(out), None)
    recs = out.to_dict(orient="records")
    for r in recs:
        for k, v in r.items():
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
            elif isinstance(v, (np.integer,)):
                r[k] = int(v)
            elif isinstance(v, (np.floating,)):
                r[k] = None if np.isnan(v) else float(v)
    return recs
