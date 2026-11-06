"""Demo dataset integrity and core API behaviour."""
import datetime as dt

import pandas as pd
import pytest

from app.services.demo_data import INJECTED_ANOMALIES, generate


def test_generator_is_deterministic():
    a, b = generate(), generate()
    assert len(a) == len(b)
    assert a[100]["actual_qty"] == b[100]["actual_qty"]


def test_generator_scenario_and_consistency():
    recs = generate()
    df = pd.DataFrame(recs)
    assert df["machine"].nunique() == 12
    assert df["line"].nunique() == 3
    assert df["shift"].nunique() == 3
    assert df["product"].nunique() >= 4
    span = (df["date"].max() - df["date"].min()).days + 1
    assert span == 90
    assert (df["good_qty"] + df["defective_qty"] == df["actual_qty"]).all()
    assert ((df["planned_production_minutes"] - df["unplanned_downtime_minutes"] - df["operating_minutes"]).abs() < 1e-6).all()
    assert (df["scheduled_minutes"] - df["planned_downtime_minutes"] == df["planned_production_minutes"]).all()
    for r in recs[:500]:
        assert sum(m for _, planned, m in r["downtime"] if not planned) == pytest.approx(r["unplanned_downtime_minutes"])
        assert sum(q for _, q in r["defects"]) == r["defective_qty"]
    # No production on Sundays
    assert not any(d.weekday() == 6 for d in df["date"])


def test_demo_load_and_meta(client, demo):
    assert demo["records"] > 2000
    meta = client.get("/api/meta").json()
    assert meta["company"]["is_synthetic"] is True
    assert "Synthetic" in meta["company"]["name"]
    assert len(meta["lines"]) == 3 and len(meta["machines"]) == 12 and len(meta["shifts"]) == 3
    assert meta["data_range"]["start"] == "2026-07-03"
    assert meta["data_range"]["end"] == "2026-09-30"


def test_kpis_match_raw_sums_and_filters(client, demo):
    df = pd.DataFrame(generate())
    k = client.get("/api/kpis").json()
    assert k["total_production"]["value"] == df["actual_qty"].sum()
    assert k["availability"]["value"] == pytest.approx(df["operating_minutes"].sum() / df["planned_production_minutes"].sum())

    meta = client.get("/api/meta").json()
    l1 = next(x for x in meta["lines"] if x["code"] == "L1")
    k1 = client.get("/api/kpis", params={"line_id": l1["id"]}).json()
    assert k1["total_production"]["value"] == df.loc[df["line"] == "L1", "actual_qty"].sum()

    sub = df[(df["date"] >= dt.date(2026, 9, 1)) & (df["date"] <= dt.date(2026, 9, 7))]
    k2 = client.get("/api/kpis", params={"start_date": "2026-09-01", "end_date": "2026-09-07"}).json()
    assert k2["record_count"] == len(sub)
    assert k2["defective_units"]["value"] == sub["defective_qty"].sum()


def test_empty_filter_returns_na(client, demo):
    k = client.get("/api/kpis", params={"start_date": "2030-01-01", "end_date": "2030-01-31"}).json()
    assert k["record_count"] == 0
    assert k["oee"]["value"] is None


def test_overview_quality_downtime_endpoints(client, demo):
    o = client.get("/api/overview").json()
    assert len(o["daily"]) > 60 and len(o["by_line"]) == 3 and len(o["by_shift"]) == 3
    q = client.get("/api/quality").json()
    assert q["pareto"][-1]["cumulative_share"] == pytest.approx(1.0)
    assert q["categorized_defects"] == q["kpis"]["defective_units"]["value"]
    d = client.get("/api/downtime").json()
    total_reason = sum(r["minutes"] for r in d["by_reason"])
    assert total_reason == pytest.approx(d["kpis"]["unplanned_downtime_minutes"]["value"])
    flagged = [m["machine_code"] for m in d["by_machine"] if m["is_high_downtime"]]
    assert flagged == ["M-10"]


def test_comparison_and_events(client, demo):
    c = client.get("/api/production/comparison", params={"start_date": "2026-09-16", "end_date": "2026-09-30"}).json()
    assert c["previous_period"]["start"] == "2026-09-01"
    ev = client.get("/api/downtime/events", params={"search": "mechanical", "sort": "minutes", "page_size": 5}).json()
    assert ev["total"] > 0 and all("Mechanical" in r["reason"] for r in ev["rows"])
    assert ev["rows"][0]["minutes"] >= ev["rows"][-1]["minutes"]


def test_injected_anomalies_documented():
    assert len(INJECTED_ANOMALIES) == 4


def test_validation_errors_are_readable(client):
    r = client.get("/api/production/breakdown", params={"dimension": "nonsense"})
    assert r.status_code == 422
    assert r.json()["errors"][0]["field"] == "dimension"
