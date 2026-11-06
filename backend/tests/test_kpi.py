"""KPI engine tests with hand-calculated expected values."""
import math

import numpy as np
import pandas as pd
import pytest

from app.services.kpi import aggregate, compute_kpis, safe_ratio


def rec(**kw):
    base = dict(planned_qty=None, actual_qty=None, good_qty=None, defective_qty=None,
                planned_production_minutes=None, operating_minutes=None, unplanned_downtime_minutes=None,
                planned_downtime_minutes=None, scheduled_minutes=None, ideal_cycle_time_sec=None)
    base.update(kw)
    return base


R1 = rec(planned_qty=120, actual_qty=100, good_qty=95, defective_qty=5, planned_production_minutes=400,
         operating_minutes=360, unplanned_downtime_minutes=40, ideal_cycle_time_sec=180)
R2 = rec(planned_qty=180, actual_qty=200, good_qty=190, defective_qty=10, planned_production_minutes=400,
         operating_minutes=300, unplanned_downtime_minutes=100, ideal_cycle_time_sec=72)


def v(k, key):
    return k[key]["value"]


def test_safe_ratio():
    assert safe_ratio(1, 0) is None
    assert safe_ratio(None, 5) is None
    assert safe_ratio(float("nan"), 5) is None
    assert safe_ratio(3, 4) == 0.75


def test_formulas_hand_calculated():
    k = compute_kpis(pd.DataFrame([R1, R2]))
    assert v(k, "total_production") == 300
    assert v(k, "planned_production") == 300
    assert v(k, "target_achievement") == pytest.approx(1.0)
    assert v(k, "good_units") == 285
    assert v(k, "defective_units") == 15
    assert v(k, "defect_rate") == pytest.approx(15 / 300)
    assert v(k, "quality") == pytest.approx(285 / 300)
    assert v(k, "unplanned_downtime_minutes") == 140
    # Availability = operating / planned production time
    assert v(k, "availability") == pytest.approx(660 / 800)
    # Performance = Σ(ICT[s]/60 × units) / operating min = (3*100 + 1.2*200) / 660
    assert v(k, "performance") == pytest.approx(540 / 660)
    # OEE = A × P × Q
    assert v(k, "oee") == pytest.approx((660 / 800) * (540 / 660) * (285 / 300))
    assert v(k, "oee") == pytest.approx(0.64125)
    assert k["oee"]["records_used"] == 2


def test_ratios_are_decimal_fractions():
    k = compute_kpis(pd.DataFrame([R1, R2]))
    for key in ("availability", "performance", "quality", "oee", "defect_rate"):
        assert 0 <= v(k, key) <= 1


def test_division_by_zero_returns_none():
    k = compute_kpis(pd.DataFrame([rec(planned_qty=0, actual_qty=0, good_qty=0, defective_qty=0,
                                       planned_production_minutes=0, operating_minutes=0, ideal_cycle_time_sec=30)]))
    for key in ("target_achievement", "defect_rate", "quality", "availability", "performance", "oee"):
        assert v(k, key) is None, key


def test_missing_values_are_not_treated_as_zero():
    r3 = rec(actual_qty=50)  # only a count – no plan, no times, no quality split
    k = compute_kpis(pd.DataFrame([R1, R2, r3]))
    assert v(k, "total_production") == 350
    # target achievement only over records with a plan
    assert v(k, "target_achievement") == pytest.approx(300 / 300)
    assert k["target_achievement"]["records_used"] == 2
    assert k["target_achievement"]["coverage"] == pytest.approx(2 / 3)
    # defect rate not diluted by the record without defect information
    assert v(k, "defect_rate") == pytest.approx(15 / 300)
    assert v(k, "oee") == pytest.approx(0.64125)
    assert k["oee"]["records_used"] == 2


def test_oee_not_computed_from_counts_alone():
    df = pd.DataFrame([rec(planned_qty=100, actual_qty=90, good_qty=88, defective_qty=2)])
    k = compute_kpis(df)
    assert v(k, "quality") == pytest.approx(88 / 90)
    assert v(k, "availability") is None
    assert v(k, "performance") is None
    assert v(k, "oee") is None


def test_empty_dataframe():
    k = compute_kpis(pd.DataFrame())
    assert k["record_count"] == 0
    assert v(k, "total_production") is None
    assert v(k, "oee") is None


def test_aggregate_matches_summary():
    df = pd.DataFrame([{**R1, "line": "A"}, {**R2, "line": "A"}, {**R1, "line": "B"}])
    g = aggregate(df, "line").set_index("line")
    assert g.loc["A", "oee"] == pytest.approx(0.64125)
    assert g.loc["A", "actual_qty"] == 300
    one = compute_kpis(pd.DataFrame([R1]))
    assert g.loc["B", "oee"] == pytest.approx(v(one, "oee"))
    assert g.loc["B", "availability"] == pytest.approx(0.9)
    assert g.loc["B", "units_per_operating_hour"] == pytest.approx(100 / 6)


def test_aggregate_missing_group_values_are_nan():
    df = pd.DataFrame([{**rec(actual_qty=10), "line": "X"}])
    g = aggregate(df, "line")
    assert math.isnan(g.loc[0, "oee"])
    assert math.isnan(g.loc[0, "target_achievement"])
    assert g.loc[0, "actual_qty"] == 10


def test_performance_uses_seconds_to_minutes_conversion():
    # 60 units at 60 s ideal = 60 ideal minutes in 120 operating minutes -> 50 %
    k = compute_kpis(pd.DataFrame([rec(actual_qty=60, operating_minutes=120, planned_production_minutes=120,
                                       ideal_cycle_time_sec=60, good_qty=60, defective_qty=0)]))
    assert v(k, "performance") == pytest.approx(0.5)
    assert v(k, "availability") == pytest.approx(1.0)
    assert v(k, "oee") == pytest.approx(0.5)
    assert not np.isnan(v(k, "quality"))
