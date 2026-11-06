# KPI Definitions

Implementation: `backend/app/services/kpi.py`. Tests: `backend/tests/test_kpi.py`, which checks the engine against hand-calculated values.

## Conventions

- Ratios are computed and returned by the API as **decimal fractions** (0.853). They are converted to percent (85.3 %) only in the UI and in reports.
- Aggregations use **ratios of sums**, never averages of ratios. A day with 3 shifts therefore carries 3× the weight of a Saturday with a single shift.
- Each KPI is aggregated **only over records that contain all of its inputs**. Missing values (NULL) are never treated as 0. Every KPI reports `records_used / records_total` (its coverage), and the UI shows "Based on X of Y records" when coverage is incomplete.
- If no record qualifies, or a denominator is 0, the value is `null`, displayed as **N/A**.

## Time model (per record, all in minutes)

```
Scheduled time              (shift length, e.g. 480)                 scheduled_minutes            optional
− Planned downtime          (breaks, planned maintenance)            planned_downtime_minutes     optional
= Planned production time   (PPT, OEE base)                          planned_production_minutes
− Unplanned downtime        (breakdowns, shortages, setups, …)       unplanned_downtime_minutes
= Operating time            (run time)                               operating_minutes
```

Ideal cycle time is stored in **seconds per unit** (`ideal_cycle_time_sec`). On import you can choose whether the source column is in seconds or minutes, and the value is converted.

When importing:

- If scheduled, planned downtime and PPT are all given, `scheduled − planned downtime = PPT` must hold (±1 min tolerance).
- If PPT, downtime and operating time are all given, `PPT − downtime = operating` must hold (±1 min).
- If exactly one of these values is missing, it is **derived** from the identity. This is reported as a warning, never done silently.
- Operating time must not exceed PPT. No record may exceed 1,440 min.

"Total Downtime" in the dashboards means **unplanned** downtime. Planned downtime is shown separately.

## Formulas

| KPI | Formula | Records used |
|---|---|---|
| Total production | Σ actual_qty | all |
| Planned production | Σ planned_qty | records with a plan |
| Target achievement | Σ actual_qty ÷ Σ planned_qty | records with a plan |
| Good units | Σ good_qty | records with good_qty |
| Defective units | Σ defective_qty | records with defective_qty |
| Defect rate | Σ defective_qty ÷ Σ actual_qty | records with defective_qty |
| Quality | Σ good_qty ÷ Σ actual_qty | records with good_qty |
| Availability | Σ operating_min ÷ Σ PPT_min | records with both |
| Performance | Σ (ideal_cycle_time_sec ÷ 60 × actual_qty) ÷ Σ operating_min | records with ICT and operating time |
| OEE | Availability × Performance × Quality | **only** records containing PPT, operating time, ICT and good_qty; A, P and Q are recomputed on this common subset |
| Units per operating hour | Σ actual_qty ÷ (Σ operating_min ÷ 60) | records with operating time |
| Downtime share (anomaly/downtime pages) | Σ unplanned_downtime_min ÷ Σ PPT_min | records with both |

OEE is **never** derived from production counts alone. Without time data and an ideal cycle time it is N/A.

### Worked example (from the test suite)

| Record | Actual | Good | PPT | Operating | ICT |
|---|---|---|---|---|---|
| R1 | 100 | 95 | 400 | 360 | 180 s |
| R2 | 200 | 190 | 400 | 300 | 72 s |

- A = 660 / 800 = 82.5 %
- P = (3 min × 100 + 1.2 min × 200) / 660 = 540 / 660 = 81.8 %
- Q = 285 / 300 = 95 %
- OEE = 0.825 × 0.818 × 0.95 = **64.1 %**

## Import consistency rules for quantities

- Quantities must be non-negative whole numbers.
- `good + defective = actual` must hold whenever all three are supplied.
- If only one of good/defective is supplied, the other is derived as `actual − supplied` (with a warning).

## Derived analytics

- **Pareto analysis** (defects, downtime): categories sorted descending, with share and cumulative share. An 80 % reference line is drawn.
- **Machines with unusually high downtime**: a machine is flagged when its downtime share is above Q3 + 1.5 × IQR across all machines in the current filter (Tukey fence; requires at least 4 machines). This is a statistical comparison, not a root cause.
- **Period comparison**: the selected period is compared with the immediately preceding period of the same length. Ratios are compared in percentage points, counts as relative change.

## Anomaly detection

See `backend/app/services/anomaly.py`.

- **Unit of analysis:** entity-day, either machine-day or line-day.
- **Metrics:** defect rate, downtime share, target achievement and performance. All are rates, which keeps days with different numbers of shifts comparable.
- **Baseline:** the rolling median of the previous `window` production days (default 20). The current day is excluded. A day is scored only once at least `min_history` days are available (default 10).
- **Spread:** 1.4826 × MAD, with a per-metric floor that prevents division by near-zero spread.
- **Robust z:** `(observed − baseline) / spread`, signed so that a positive value always means worse. It is multiplied by √(exposure ÷ typical exposure), capped at 1, so that short days are not over-flagged.
- **Flagging:** a point is flagged when z ≥ threshold (default 4) **and** the absolute change is at least a practical minimum (e.g. 1.5 pp for defect rate).
- **Severity:** medium ≥ 4, high ≥ 6, critical ≥ 9. All thresholds are configurable.
- **Look-back:** when a date range is selected, detection uses up to 60 earlier days as baseline history and reports only anomalies inside the selected range.
- **Too little history:** if the history is too short, the result is `insufficient_data` with an explanation, and nothing is guessed.
- **Isolation Forest (optional):** runs with a fixed `random_state` on per-machine standardized features. Findings are labelled "low" severity and "multivariate outlier".

Explanations are generated from templates that use measured values only. Context lines show only what was **recorded in the data that day**, such as downtime reasons, and are explicitly labelled as not being a cause.
