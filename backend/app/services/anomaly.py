"""Transparent statistical anomaly detection.

Method (deterministic):
1. Aggregate production records to entity-days (machine-day and line-day).
2. Compute rate metrics that are comparable across days with different shift
   counts: defect rate, unplanned-downtime ratio (downtime / planned production
   time), target achievement (actual / planned) and performance.
3. For each entity and metric the baseline is the rolling MEDIAN of the
   previous ``window`` observations (the current day is excluded). Spread is the
   rolling MAD scaled by 1.4826 (≈ σ for normal data) with a metric-specific
   floor to avoid division by near-zero spread.
4. Robust z = (observed − baseline) / spread, signed so that positive means
   "worse", multiplied by sqrt(exposure / typical exposure) (capped at 1) so that
   short production days (e.g. a single Saturday shift) are not over-flagged. A point is an anomaly when z ≥ threshold AND the absolute change
   exceeds a minimum practical difference.
5. Optional: Isolation Forest (fixed random_state) on per-machine standardized
   features for multivariate outliers.

The output describes *statistical deviations*. It does NOT state root causes
and does NOT predict machine failures. Context lines only quote what is
recorded in the data (e.g. downtime reasons logged that day).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

METRICS = {
    # key: (label, worse_direction (+1 higher is worse, -1 lower is worse), spread floor, unit)
    "defect_rate": ("Defect rate", 1, 0.003, "ratio"),
    "downtime_ratio": ("Unplanned downtime share", 1, 0.03, "ratio"),
    "target_achievement": ("Target achievement (actual / planned)", -1, 0.03, "ratio"),
    "performance": ("Performance", -1, 0.02, "ratio"),
}
METRIC_LABEL_DE = {
    "defect_rate": "Ausschussquote",
    "downtime_ratio": "Anteil ungeplanter Stillstand",
    "target_achievement": "Zielerreichung (Ist / Soll)",
    "performance": "Leistungsgrad",
}
METRIC_PHRASE = {
    "defect_rate": "Unexpected increase in defect rate",
    "downtime_ratio": "Unusual unplanned downtime",
    "target_achievement": "Significant production drop",
    "performance": "Abnormal machine performance (speed loss)",
}


def entity_days(records: pd.DataFrame, level: str) -> pd.DataFrame:
    if records.empty:
        return pd.DataFrame()
    if level == "machine":
        keys = ["machine_id", "machine_code", "machine_name", "line_code"]
    else:
        keys = ["line_id", "line_code", "line_name"]
    df = records.copy()
    df["_ideal_min"] = df["ideal_cycle_time_sec"] / 60.0 * df["actual_qty"]
    m_perf = df["ideal_cycle_time_sec"].notna() & df["operating_minutes"].notna()
    df["_op_perf"] = df["operating_minutes"].where(m_perf)
    df["_ideal_min"] = df["_ideal_min"].where(m_perf)
    m_plan = df["planned_qty"].notna()
    df["_actual_plan"] = df["actual_qty"].where(m_plan)
    m_def = df["defective_qty"].notna()
    df["_actual_def"] = df["actual_qty"].where(m_def)
    m_dt = df["unplanned_downtime_minutes"].notna() & df["planned_production_minutes"].notna()
    df["_udt"] = df["unplanned_downtime_minutes"].where(m_dt)
    df["_ppt_dt"] = df["planned_production_minutes"].where(m_dt)
    g = df.groupby(keys + ["date"]).agg(
        actual_qty=("actual_qty", "sum"),
        planned_qty=("planned_qty", lambda s: s.sum(min_count=1)),
        _actual_plan=("_actual_plan", lambda s: s.sum(min_count=1)),
        defective_qty=("defective_qty", lambda s: s.sum(min_count=1)),
        _actual_def=("_actual_def", lambda s: s.sum(min_count=1)),
        unplanned_downtime_minutes=("_udt", lambda s: s.sum(min_count=1)),
        planned_production_minutes=("_ppt_dt", lambda s: s.sum(min_count=1)),
        _ideal_min=("_ideal_min", lambda s: s.sum(min_count=1)),
        _op_perf=("_op_perf", lambda s: s.sum(min_count=1)),
        exposure_minutes=("planned_production_minutes", lambda s: s.sum(min_count=1)),
    ).reset_index()

    def ratio(a, b):
        return (g[a] / g[b].where(g[b] > 0)).astype(float)

    g["defect_rate"] = ratio("defective_qty", "_actual_def")
    g["downtime_ratio"] = ratio("unplanned_downtime_minutes", "planned_production_minutes")
    g["target_achievement"] = ratio("_actual_plan", "planned_qty")
    g["performance"] = ratio("_ideal_min", "_op_perf")
    g = g.rename(columns={keys[0]: "entity_id", keys[1]: "entity_code", keys[2]: "entity_name"})
    if level == "machine":
        g = g.rename(columns={"line_code": "parent_code"})
    else:
        g["parent_code"] = None
    g["level"] = level
    return g.sort_values(["entity_id", "date"]).reset_index(drop=True)


def _rolling_baseline(s: pd.Series, window: int, min_hist: int, floor: float) -> tuple[pd.Series, pd.Series, pd.Series]:
    prev = s.shift(1)
    roll = prev.rolling(window, min_periods=min_hist)
    med = roll.median()
    mad = roll.apply(lambda x: np.nanmedian(np.abs(x - np.nanmedian(x))), raw=True)
    count = prev.rolling(window, min_periods=1).count()
    scale = np.maximum(1.4826 * mad, floor)
    return med, scale, count


def score(ed: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Add baseline/z-score columns for each metric. Deterministic."""
    out = []
    for _eid, grp in ed.groupby("entity_id", sort=True):
        grp = grp.sort_values("date").copy()
        # Exposure adjustment: a day with less planned production time than usual (e.g. a single Saturday
        # shift) yields noisier rates, so its z-score is shrunk by sqrt(exposure / typical exposure) (≤ 1).
        expo = grp["exposure_minutes"]
        typical = expo.shift(1).rolling(cfg["window"], min_periods=1).median()
        factor = np.sqrt((expo / typical).clip(upper=1.0)).fillna(1.0)
        grp["exposure_factor"] = factor
        for metric, (_label, direction, floor, _u) in METRICS.items():
            s = grp[metric]
            valid = s.dropna()
            med = pd.Series(np.nan, index=grp.index)
            scale = pd.Series(np.nan, index=grp.index)
            cnt = pd.Series(0.0, index=grp.index)
            if len(valid):
                m, sc, c = _rolling_baseline(valid, cfg["window"], cfg["min_history"], floor)
                med.loc[valid.index], scale.loc[valid.index], cnt.loc[valid.index] = m, sc, c
            grp[f"{metric}__baseline"] = med
            grp[f"{metric}__scale"] = scale
            grp[f"{metric}__history"] = cnt
            grp[f"{metric}__z"] = direction * (s - med) / scale * factor
        out.append(grp)
    return pd.concat(out) if out else ed


def _severity(z: float, cfg: dict) -> str:
    if z >= cfg["critical_z"]:
        return "critical"
    if z >= cfg["high_z"]:
        return "high"
    return "medium"


def _fmt_ratio(v: float) -> str:
    return f"{v * 100:.1f} %"


def _explain(row: pd.Series, metric: str, cfg: dict) -> str:
    label = METRICS[metric][0]
    obs, base, z = row[metric], row[f"{metric}__baseline"], row[f"{metric}__z"]
    who = f"{'Machine' if row['level'] == 'machine' else 'Line'} {row['entity_code']}"
    detail = ""
    if metric == "defect_rate" and pd.notna(row.get("defective_qty")):
        detail = f" ({int(row['defective_qty']):,} defective of {int(row['actual_qty']):,} units)"
    elif metric == "downtime_ratio" and pd.notna(row.get("unplanned_downtime_minutes")):
        detail = (f" ({row['unplanned_downtime_minutes']:.0f} min unplanned downtime in "
                  f"{row['planned_production_minutes']:.0f} min planned production time)")
    elif metric == "target_achievement" and pd.notna(row.get("planned_qty")):
        detail = f" ({int(row['actual_qty']):,} produced vs. {int(row['planned_qty']):,} planned units)"
    return (
        f"{label} of {who} on {row['date']:%Y-%m-%d} was {_fmt_ratio(obs)}{detail}, compared with a baseline "
        f"median of {_fmt_ratio(base)} over the previous {int(row[f'{metric}__history'])} production days "
        f"(robust z-score {z:.1f}, threshold {cfg['z_threshold']:.1f}). This is a statistical deviation – "
        f"the operational cause has not been determined from the data."
    )


def _fmt_ratio_de(v: float) -> str:
    return f"{v * 100:.1f} %".replace(".", ",")


def _int_de(v) -> str:
    return f"{int(v):,}".replace(",", ".")


def _explain_de(row: pd.Series, metric: str, cfg: dict) -> str:
    label = METRIC_LABEL_DE[metric]
    obs, base, z = row[metric], row[f"{metric}__baseline"], row[f"{metric}__z"]
    who = f"{'Maschine' if row['level'] == 'machine' else 'Linie'} {row['entity_code']}"
    detail = ""
    if metric == "defect_rate" and pd.notna(row.get("defective_qty")):
        detail = f" ({_int_de(row['defective_qty'])} Ausschussteile von {_int_de(row['actual_qty'])} Stück)"
    elif metric == "downtime_ratio" and pd.notna(row.get("unplanned_downtime_minutes")):
        detail = (f" ({row['unplanned_downtime_minutes']:.0f} min ungeplanter Stillstand bei "
                  f"{row['planned_production_minutes']:.0f} min geplanter Produktionszeit)")
    elif metric == "target_achievement" and pd.notna(row.get("planned_qty")):
        detail = f" ({_int_de(row['actual_qty'])} produziert vs. {_int_de(row['planned_qty'])} geplant)"
    z_de = f"{z:.1f}".replace(".", ",")
    thr_de = f"{cfg['z_threshold']:.1f}".replace(".", ",")
    return (
        f"{label} von {who} am {row['date']:%d.%m.%Y}: {_fmt_ratio_de(obs)}{detail}, verglichen mit einem "
        f"Basislinien-Median von {_fmt_ratio_de(base)} über die vorherigen {int(row[f'{metric}__history'])} "
        f"Produktionstage (robuster z-Wert {z_de}, Schwelle {thr_de}). Dies ist eine statistische Abweichung – "
        "die betriebliche Ursache wurde aus den Daten nicht ermittelt."
    )


def detect(records: pd.DataFrame, cfg: dict, downtime: pd.DataFrame | None = None,
           defects: pd.DataFrame | None = None) -> dict:
    levels = [lv for lv in cfg.get("levels", ["machine", "line"]) if lv in ("machine", "line")]
    metrics = [m for m in cfg.get("metrics", list(METRICS)) if m in METRICS]
    if records.empty:
        return {"status": "insufficient_data", "message": "No production records in the selected range.",
                "anomalies": [], "stats": {}}

    days = records["date"].nunique()
    needed = cfg["min_history"] + 1
    if days < needed:
        return {"status": "insufficient_data",
                "message": (f"Anomaly detection needs at least {needed} production days per machine/line to build a "
                            f"baseline; the selected data contains {days}. Results would be statistically unreliable, "
                            "so no detection was performed."),
                "anomalies": [], "stats": {"production_days": int(days)}}

    anomalies: list[dict] = []
    scored_points = 0
    for level in levels:
        ed = entity_days(records, level)
        if ed.empty:
            continue
        sc = score(ed, cfg)
        for metric in metrics:
            z = sc[f"{metric}__z"]
            scored_points += int(z.notna().sum())
            change = (sc[metric] - sc[f"{metric}__baseline"]).abs()
            mask = (z >= cfg["z_threshold"]) & (change >= cfg["min_abs_change"].get(metric, 0))
            for _, row in sc[mask].iterrows():
                zval = float(row[f"{metric}__z"])
                a = {
                    "id": f"{level}-{row['entity_code']}-{row['date']:%Y%m%d}-{metric}",
                    "date": row["date"].strftime("%Y-%m-%d"),
                    "level": level,
                    "entity_id": int(row["entity_id"]),
                    "entity_code": row["entity_code"],
                    "entity_name": row["entity_name"],
                    "line_code": row["parent_code"] if level == "machine" else row["entity_code"],
                    "metric": metric,
                    "metric_label": METRICS[metric][0],
                    "category": METRIC_PHRASE[metric],
                    "observed": float(row[metric]),
                    "baseline": float(row[f"{metric}__baseline"]),
                    "baseline_spread": float(row[f"{metric}__scale"]),
                    "baseline_days": int(row[f"{metric}__history"]),
                    "z_score": zval,
                    "severity": _severity(zval, cfg),
                    "method": "rolling_robust_zscore",
                    "explanation": _explain(row, metric, cfg),
                    "explanation_de": _explain_de(row, metric, cfg),
                    "recorded_context": _context(row, level, metric, downtime, defects),
                    "status": "detected_unconfirmed",
                }
                anomalies.append(a)

    if cfg.get("use_isolation_forest"):
        anomalies.extend(_isolation_forest(records, cfg, {a["id"].rsplit("-", 1)[0] for a in anomalies}))

    sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    anomalies.sort(key=lambda a: (sev_rank[a["severity"]], -a["z_score"] if a["z_score"] else 0, a["date"]))
    counts = {s: sum(1 for a in anomalies if a["severity"] == s) for s in ("critical", "high", "medium", "low")}
    return {
        "status": "ok",
        "message": None,
        "anomalies": anomalies,
        "stats": {"production_days": int(days), "scored_points": scored_points, "by_severity": counts,
                  "total": len(anomalies)},
    }


def _context(row, level, metric, downtime, defects) -> list[dict]:
    """Facts recorded in the data for the same entity/day – explicitly NOT a cause analysis."""
    out: list[dict] = []
    day = row["date"]
    col = "machine_id" if level == "machine" else "line_id"
    if metric in ("downtime_ratio", "target_achievement", "performance") and downtime is not None and not downtime.empty:
        d = downtime[(downtime["date"] == day) & (downtime[col] == row["entity_id"])]
        if not d.empty:
            top = d.groupby("reason")["minutes"].sum().sort_values(ascending=False).head(3)
            out.append({"kind": "downtime_reasons", "unit": "min",
                        "items": [{"name": r, "value": float(m)} for r, m in top.items()],
                        "text": "Downtime reasons recorded that day: " + ", ".join(f"{r} ({m:.0f} min)" for r, m in top.items())})
    if metric == "defect_rate" and defects is not None and not defects.empty:
        d = defects[(defects["date"] == day) & (defects[col] == row["entity_id"])]
        if not d.empty:
            top = d.groupby("category")["quantity"].sum().sort_values(ascending=False).head(3)
            out.append({"kind": "defect_categories", "unit": "units",
                        "items": [{"name": c, "value": float(q)} for c, q in top.items()],
                        "text": "Defect categories recorded that day: " + ", ".join(f"{c} ({int(q)})" for c, q in top.items())})
    return out


def _isolation_forest(records: pd.DataFrame, cfg: dict, already: set[str]) -> list[dict]:
    from sklearn.ensemble import IsolationForest

    ed = entity_days(records, "machine")
    feats = list(METRICS)
    ed = ed.dropna(subset=feats)
    if len(ed) < 200:
        return []
    z = ed[feats].copy()
    meds = ed.groupby("entity_id")[feats].transform("median")
    mads = ed.groupby("entity_id")[feats].transform(lambda s: np.median(np.abs(s - s.median())))
    floors = pd.Series({k: v[2] for k, v in METRICS.items()})
    scale = (1.4826 * mads).clip(lower=floors, axis=1)
    z = (z - meds) / scale
    model = IsolationForest(n_estimators=200, contamination=cfg["isolation_forest_contamination"], random_state=42)
    pred = model.fit_predict(z.values)
    out = []
    for idx in np.where(pred == -1)[0]:
        row = ed.iloc[idx]
        signed = {m: z.iloc[idx][m] * METRICS[m][1] for m in feats}
        metric = max(signed, key=signed.get)
        key = f"machine-{row['entity_code']}-{row['date']:%Y%m%d}"
        if key in already or signed[metric] <= 0:
            continue
        out.append({
            "id": f"{key}-{metric}-if",
            "date": row["date"].strftime("%Y-%m-%d"), "level": "machine", "entity_id": int(row["entity_id"]),
            "entity_code": row["entity_code"], "entity_name": row["entity_name"], "line_code": row["parent_code"],
            "metric": metric, "metric_label": METRICS[metric][0], "category": "Multivariate outlier",
            "observed": float(row[metric]), "baseline": float(meds.iloc[idx][metric]),
            "baseline_spread": float(scale.iloc[idx][metric]), "baseline_days": int((ed["entity_id"] == row["entity_id"]).sum()),
            "z_score": float(signed[metric]), "severity": "low", "method": "isolation_forest",
            "explanation": (f"Isolation Forest marked machine {row['entity_code']} on {row['date']:%Y-%m-%d} as a "
                            f"multivariate outlier across defect rate, downtime, target achievement and performance. "
                            f"The largest deviation is {METRICS[metric][0].lower()} at {_fmt_ratio(row[metric])} vs. the "
                            f"machine median of {_fmt_ratio(meds.iloc[idx][metric])}. Statistical signal only – cause not determined."),
            "explanation_de": (f"Isolation Forest markierte Maschine {row['entity_code']} am {row['date']:%d.%m.%Y} als "
                               f"multivariaten Ausreißer. Größte Abweichung: {METRIC_LABEL_DE[metric]} "
                               f"{_fmt_ratio_de(row[metric])} vs. Maschinen-Median {_fmt_ratio_de(meds.iloc[idx][metric])}. "
                               "Nur statistisches Signal – Ursache nicht ermittelt."),
            "recorded_context": [], "status": "detected_unconfirmed",
        })
    return out


def series(records: pd.DataFrame, level: str, entity_id: int, metric: str, cfg: dict) -> list[dict]:
    ed = entity_days(records, level)
    if ed.empty:
        return []
    ed = ed[ed["entity_id"] == entity_id]
    if ed.empty:
        return []
    sc = score(ed, cfg)
    out = []
    for _, r in sc.iterrows():
        base, scale = r[f"{metric}__baseline"], r[f"{metric}__scale"]
        direction = METRICS[metric][1]
        out.append({
            "date": r["date"].strftime("%Y-%m-%d"),
            "value": None if pd.isna(r[metric]) else float(r[metric]),
            "baseline": None if pd.isna(base) else float(base),
            "threshold": None if pd.isna(base) else float(base + direction * cfg["z_threshold"] * scale),
            "z_score": None if pd.isna(r[f"{metric}__z"]) else float(r[f"{metric}__z"]),
        })
    return out


def validate_against_ground_truth(anomalies: list[dict], truth: list[dict]) -> list[dict]:
    """For SYNTHETIC demo data only: check whether injected anomalies were detected."""
    res = []
    for t in truth:
        hits = [a for a in anomalies if a["date"] == t["date"] and a["entity_code"] == t["entity"]]
        res.append({**t, "detected": bool(hits), "matched_metrics": sorted({a["metric"] for a in hits}),
                    "best_severity": hits[0]["severity"] if hits else None})
    return res


def restrict_to_period(result: dict, start: str | None, end: str | None, production_days: int | None = None) -> dict:
    """Keep only anomalies inside [start, end] (ISO dates) after detecting on a longer history."""
    if result.get("status") != "ok":
        return result
    kept = [a for a in result["anomalies"] if (not start or a["date"] >= start) and (not end or a["date"] <= end)]
    counts = {sev: sum(1 for a in kept if a["severity"] == sev) for sev in ("critical", "high", "medium", "low")}
    stats = dict(result["stats"], by_severity=counts, total=len(kept))
    if production_days is not None:
        stats["production_days"] = production_days
    return {**result, "anomalies": kept, "stats": stats}
