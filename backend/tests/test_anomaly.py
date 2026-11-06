"""Anomaly detection: injected ground truth, determinism, small datasets, configurability."""
import pandas as pd

from app.services.anomaly import detect
from app.services.settings_store import DEFAULT_ANOMALY_CONFIG


def run(client, **kw):
    r = client.post("/api/anomalies/detect", json=kw)
    assert r.status_code == 200, r.text
    return r.json()


def test_injected_anomalies_are_detected(client, demo):
    d = run(client)
    assert d["status"] == "ok"
    gt = d["ground_truth_validation"]
    assert len(gt) == 4 and all(t["detected"] for t in gt), gt
    by = {(t["entity"], t["date"]): t for t in gt}
    assert "defect_rate" in by[("M-06", "2026-08-12")]["matched_metrics"]
    assert "target_achievement" in by[("L3", "2026-08-26")]["matched_metrics"]
    assert "downtime_ratio" in by[("M-02", "2026-09-02")]["matched_metrics"]
    assert "performance" in by[("M-07", "2026-09-16")]["matched_metrics"]
    top = d["anomalies"][0]
    for k in ("date", "entity_code", "metric", "observed", "baseline", "severity", "explanation"):
        assert top[k] is not None
    assert all("cause has not been determined" in a["explanation"] for a in d["anomalies"])
    assert all(a["status"] == "detected_unconfirmed" for a in d["anomalies"])
    # injected events dominate the critical list
    crit = {(a["entity_code"], a["date"]) for a in d["anomalies"] if a["severity"] == "critical"}
    assert ("M-06", "2026-08-12") in crit and ("M-07", "2026-09-16") in crit


def test_detection_is_deterministic(client, demo):
    a, b = run(client), run(client)
    assert [x["id"] for x in a["anomalies"]] == [x["id"] for x in b["anomalies"]]


def test_threshold_is_configurable(client, demo):
    base = run(client)["stats"]["total"]
    cfg = dict(DEFAULT_ANOMALY_CONFIG, z_threshold=8.0, high_z=10.0, critical_z=12.0)
    strict = run(client, config=cfg)["stats"]["total"]
    assert strict < base
    bad = client.post("/api/anomalies/detect", json={"config": dict(cfg, z_threshold=9, high_z=5)})
    assert bad.status_code == 422


def test_small_dataset_is_reported_as_insufficient(client, demo):
    d = run(client, filters={"start_date": "2026-07-03", "end_date": "2026-07-10"})  # no earlier history
    assert d["status"] == "insufficient_data"
    assert "unreliable" in d["message"] and d["anomalies"] == []


def test_empty_records():
    assert detect(pd.DataFrame(), DEFAULT_ANOMALY_CONFIG)["status"] == "insufficient_data"


def test_isolation_forest_optional(client, demo):
    cfg = dict(DEFAULT_ANOMALY_CONFIG, use_isolation_forest=True, isolation_forest_contamination=0.02)
    d = run(client, config=cfg)
    methods = {a["method"] for a in d["anomalies"]}
    assert "rolling_robust_zscore" in methods
    assert d == run(client, config=cfg)  # deterministic (fixed random_state)


def test_series_endpoint(client, demo):
    meta = client.get("/api/meta").json()
    m06 = next(m for m in meta["machines"] if m["code"] == "M-06")
    s = client.get("/api/anomalies/series", params={"entity_id": m06["id"], "metric": "defect_rate"}).json()
    spike = next(p for p in s["points"] if p["date"] == "2026-08-12")
    assert spike["value"] > spike["threshold"] > spike["baseline"]


def test_period_uses_lookback_history(client, demo):
    d = run(client, filters={"start_date": "2026-09-01", "end_date": "2026-09-30"})
    assert d["status"] == "ok"
    assert all("2026-09-01" <= a["date"] <= "2026-09-30" for a in d["anomalies"])
    assert {g["entity"] for g in d["ground_truth_validation"]} == {"M-02", "M-07"}
    assert all(g["detected"] for g in d["ground_truth_validation"])
