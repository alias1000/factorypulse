"""Action Center: investigations, workflow, corrective actions, comments, evidence, KPI comparison,
rules/alerts, permissions, demo data and backup coverage."""
import datetime as dt
import io

import pandas as pd
import pytest

from app.clock import local_today
from conftest import TEST_PASSWORD, email_for

PDF = b"%PDF-1.4\n%test evidence\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def users(client):
    return {u["email"]: u for u in client.get("/api/action-center/assignees").json()}


def ids(client):
    meta = client.get("/api/meta").json()
    return ({l["code"]: l["id"] for l in meta["lines"]}, {m["code"]: m for m in meta["machines"]})


def create(client, **kw):
    lines, machines = ids(client)
    body = {"title": "Test investigation", "description": "desc", "category": "excessive_downtime",
            "severity": "high", "line_id": lines["L1"], "machine_id": machines["M-01"]["id"]}
    body.update(kw)
    return client.post("/api/investigations", json=body)


# --------------------------------------------------------------------------- demo data

def test_demo_contains_complete_workflow(client, demo):
    assert demo["action_center"]["investigations"] == 8 and demo["action_center"]["anomalies_linked"] == 4
    rows = client.get("/api/investigations", params={"page_size": 50}).json()["rows"]
    statuses = {r["status"] for r in rows}
    assert {"open", "in_progress", "pending_verification", "resolved", "closed", "reopened"} <= statuses
    assert all(r["is_synthetic"] for r in rows)
    assert any(r["severity"] == "critical" and r["owner"] is None for r in rows)
    s = client.get("/api/action-center/summary").json()
    assert s["kpis"]["overdue_actions"] >= 2 and s["kpis"]["critical_open"] >= 2
    rules = {a["rule"] for a in s["alerts"]}
    assert {"critical_unassigned", "action_overdue", "pending_verification_too_long"} <= rules
    assert s["timezone"] == "Europe/Berlin"


# --------------------------------------------------------------------------- creation & permissions

def test_admin_creates_and_assigns(client, demo, analyst_id):
    r = create(client, owner_id=analyst_id)
    assert r.status_code == 201, r.text
    inv = r.json()
    assert inv["number"].startswith(f"INV-{local_today().year}-") and inv["status"] == "open"
    assert inv["owner"]["id"] == analyst_id and inv["source"] == "manual"
    assert inv["due_date"] == (local_today() + dt.timedelta(days=5)).isoformat()  # default for "high"
    tl = client.get(f"/api/investigations/{inv['id']}/timeline").json()
    assert [e["type"] for e in tl][:2] == ["created", "assigned"]
    log = client.get("/api/audit-log", params={"event": "investigation."}).json()
    assert any(e["event"] == "investigation.created" and e["entity_id"] == inv["id"] for e in log)


def test_validation_errors(client, demo):
    lines, machines = ids(client)
    assert create(client, machine_id=machines["M-05"]["id"]).json()["code"] == "machine_line_mismatch"
    assert create(client, category="bogus").status_code == 422
    assert create(client, title="x").status_code == 422
    viewer_id = next(u["id"] for u in client.get("/api/users").json() if u["email"] == email_for("viewer"))
    assert create(client, owner_id=viewer_id).json()["code"] == "invalid_assignee"


def test_viewer_is_read_only(client, as_role, demo):
    inv = create(client).json()
    v = as_role("viewer")
    assert v.get("/api/investigations").status_code == 200
    assert v.get(f"/api/investigations/{inv['id']}").status_code == 200
    assert v.get(f"/api/investigations/{inv['id']}/timeline").status_code == 200
    assert v.get("/api/action-center/summary").status_code == 200
    lines, _ = ids(client)
    for method, path, body in [
        ("POST", "/api/investigations", {"title": "abc", "category": "other", "severity": "low", "line_id": lines["L1"]}),
        ("PATCH", f"/api/investigations/{inv['id']}", {"title": "changed"}),
        ("POST", f"/api/investigations/{inv['id']}/transition", {"to_status": "in_progress"}),
        ("POST", f"/api/investigations/{inv['id']}/comments", {"body": "hi"}),
        ("POST", f"/api/investigations/{inv['id']}/actions", {"title": "do it"}),
        ("PUT", "/api/action-center/settings", {}),
    ]:
        r = v.request(method, path, json=body)
        assert r.status_code == 403, (method, path, r.text)
    up = v.post(f"/api/investigations/{inv['id']}/attachments", files={"file": ("a.pdf", PDF)})
    assert up.status_code == 403


def test_analyst_assignment_rules(client, as_role, demo, analyst_id):
    a = as_role("analyst")
    admin_id = users(client)[email_for("admin")]["id"]
    assert create(a, owner_id=admin_id).status_code == 403  # cannot assign others
    own = create(a, owner_id=analyst_id)
    assert own.status_code == 201
    # analyst cannot edit an investigation they neither own nor created
    foreign = create(client).json()
    assert a.patch(f"/api/investigations/{foreign['id']}", json={"title": "Hijacked title"}).status_code == 403
    # ... but may take ownership of an unassigned one
    took = a.patch(f"/api/investigations/{foreign['id']}", json={"owner_id": analyst_id})
    assert took.status_code == 200 and took.json()["owner"]["id"] == analyst_id
    assert a.patch(f"/api/investigations/{foreign['id']}", json={"title": "Now allowed"}).status_code == 200


# --------------------------------------------------------------------------- workflow

def test_status_transitions(client, as_role, demo, analyst_id):
    a = as_role("analyst")
    before = {e["id"] for e in client.get("/api/audit-log", params={"event": "investigation.status_changed", "limit": 1000}).json()}
    inv = create(a, owner_id=analyst_id).json()
    iid = inv["id"]

    def tr(c, to, **kw):
        return c.post(f"/api/investigations/{iid}/transition", json={"to_status": to, **kw})

    assert tr(a, "resolved").json()["code"] == "invalid_transition"
    assert tr(a, "in_progress").status_code == 200
    assert tr(a, "pending_verification").json()["code"] == "actions_required"
    act = a.post(f"/api/investigations/{iid}/actions", json={"title": "Inspect bearings", "owner_id": analyst_id,
                                                             "due_date": local_today().isoformat()}).json()
    assert tr(a, "pending_verification").json()["code"] == "actions_open"
    assert a.patch(f"/api/actions/{act['id']}", json={"status": "done"}).json()["code"] == "notes_required"
    assert a.patch(f"/api/actions/{act['id']}", json={"status": "done", "completion_notes": "Bearing replaced"}).status_code == 200
    assert tr(a, "pending_verification").status_code == 200
    assert tr(a, "resolved").json()["code"] == "resolution_required"
    assert tr(a, "resolved", resolution_summary="Bearing replaced, downtime back to normal range").status_code == 200
    assert tr(a, "closed").status_code == 403  # admin only
    assert tr(client, "closed").status_code == 200
    # closed is read-only
    assert a.post(f"/api/investigations/{iid}/comments", json={"body": "late"}).status_code == 409
    assert client.patch(f"/api/investigations/{iid}", json={"title": "edit closed"}).status_code == 409
    assert tr(client, "reopened").json()["code"] == "reason_required"
    r = tr(client, "reopened", note="Downtime recurred")
    assert r.status_code == 200 and r.json()["reopen_count"] == 1 and r.json()["closed_at"] is None
    events = [(e["type"], (e["data"] or {}).get("to")) for e in client.get(f"/api/investigations/{iid}/timeline").json()]
    for step in ["in_progress", "pending_verification", "resolved", "closed", "reopened"]:
        assert ("status_changed", step) in events
    log = client.get("/api/audit-log", params={"event": "investigation.status_changed"}).json()
    # ids are reused on SQLite after a demo reload: count only entries written by this test
    assert sum(1 for e in log if e["entity_id"] == iid and e["id"] not in before) == 5


def test_corrective_action_permissions_and_verification(client, as_role, demo, analyst_id):
    admin_id = users(client)[email_for("admin")]["id"]
    inv = create(client, owner_id=admin_id).json()
    act = client.post(f"/api/investigations/{inv['id']}/actions",
                      json={"title": "Review maintenance history", "owner_id": analyst_id, "priority": "high",
                            "due_date": (local_today() - dt.timedelta(days=1)).isoformat()}).json()
    assert act["overdue"] and act["code"].startswith("ACT-")
    a = as_role("analyst")
    mine = a.get("/api/actions/mine").json()
    assert any(x["id"] == act["id"] for x in mine["overdue"])
    # responsible analyst progresses the action, but cannot edit its fields (not investigation owner)
    assert a.patch(f"/api/actions/{act['id']}", json={"status": "in_progress"}).status_code == 200
    assert a.patch(f"/api/actions/{act['id']}", json={"due_date": "2030-01-01"}).status_code == 403
    assert a.patch(f"/api/actions/{act['id']}", json={"status": "done", "completion_notes": "History reviewed"}).status_code == 200
    # verification only by the investigation owner/admin
    assert a.patch(f"/api/actions/{act['id']}", json={"status": "verified", "verification_result": "effective"}).status_code == 403
    assert client.patch(f"/api/actions/{act['id']}", json={"status": "verified"}).json()["code"] == "verification_required"
    v = client.patch(f"/api/actions/{act['id']}", json={"status": "verified", "verification_result": "partially_effective",
                                                        "verification_notes": "Downtime reduced but not eliminated"})
    assert v.status_code == 200 and v.json()["verified_by"]["id"] == admin_id
    assert a.patch(f"/api/actions/{act['id']}", json={"status": "in_progress"}).status_code == 403  # verified → admin only
    assert as_role("viewer").patch(f"/api/actions/{act['id']}", json={"status": "in_progress"}).status_code == 403
    types = [e["type"] for e in client.get(f"/api/investigations/{inv['id']}/timeline").json()]
    assert "action_created" in types and "action_verified" in types and types.count("action_status_changed") >= 2


def test_overdue_and_deadlines(client, demo, analyst_id):
    inv = create(client, owner_id=analyst_id, severity="critical",
                 due_date=(local_today() - dt.timedelta(days=3)).isoformat()).json()
    assert inv["overdue"] and inv["days_overdue"] == 3
    rows = client.get("/api/investigations", params={"overdue": True}).json()["rows"]
    assert any(r["id"] == inv["id"] for r in rows)
    low = create(client, severity="low").json()
    assert low["due_date"] == (local_today() + dt.timedelta(days=20)).isoformat()


# --------------------------------------------------------------------------- anomaly integration

def test_create_from_anomaly_with_snapshot_and_duplicate_prevention(client, as_role, demo):
    a = as_role("analyst")
    key = "machine-M-10-20260826-downtime_ratio"  # detected but not linked by the demo data
    det = client.post("/api/anomalies/detect", json={}).json()
    assert any(x["id"] == key for x in det["anomalies"])
    pre = a.post("/api/investigations/anomaly-prefill", json={"anomaly_id": key, "lang": "de"}).json()
    assert pre["duplicate"] is None and pre["proposal"]["category"] == "excessive_downtime"
    assert "Ursache ist nicht festgestellt" in pre["proposal"]["description"]
    prop = pre["proposal"]
    lines, machines = ids(client)
    r = a.post("/api/investigations", json={**prop, "line_id": lines["L1"], "machine_id": None, "anomaly_id": key})
    assert r.status_code == 201, r.text
    inv = r.json()
    # scope comes from the anomaly, not from the (tampered) request
    assert inv["machine"]["code"] == "M-10" and inv["line"]["code"] == "L3" and inv["source"] == "anomaly"
    snap = inv["anomaly_links"][0]["snapshot"]
    assert snap["observed"] > snap["baseline"] and "detection_config" in snap and "explanation" in snap
    dup = a.post("/api/investigations", json={**prop, "anomaly_id": key})
    assert dup.status_code == 409 and dup.json()["investigation_id"] == inv["id"]
    assert a.post("/api/investigations/anomaly-prefill", json={"anomaly_id": key}).json()["duplicate"]["id"] == inv["id"]
    det2 = client.post("/api/anomalies/detect", json={}).json()
    assert next(x for x in det2["anomalies"] if x["id"] == key)["investigation"]["number"] == inv["number"]


def test_anomaly_ids_are_validated(client, demo):
    assert client.post("/api/investigations/anomaly-prefill", json={"anomaly_id": "../../etc"}).status_code == 422
    r = client.post("/api/investigations/anomaly-prefill", json={"anomaly_id": "machine-M-01-20260705-defect_rate"})
    assert r.status_code == 422 and r.json()["code"] == "anomaly_not_reproducible"


# --------------------------------------------------------------------------- comments & evidence

def test_comments_and_evidence(client, as_role, demo, monkeypatch, fake_scanner):
    inv = create(client).json()
    a = as_role("analyst")
    c = a.post(f"/api/investigations/{inv['id']}/comments", json={"body": "Operator interview scheduled"})
    assert c.status_code == 201
    assert a.post(f"/api/investigations/{inv['id']}/comments", json={"body": ""}).status_code == 422
    up = a.post(f"/api/investigations/{inv['id']}/attachments", files={"file": ("report.pdf", PDF)},
                data={"description": "Maintenance report"})
    assert up.status_code == 201, up.text
    att = up.json()
    assert len(att["sha256"]) == 64 and att["scan_status"] == "clean" and att["quarantined"] is False
    assert a.post(f"/api/investigations/{inv['id']}/attachments", files={"file": ("photo.png", PNG)}).status_code == 201
    assert a.post(f"/api/investigations/{inv['id']}/attachments", files={"file": ("tool.exe", b"MZ")}).status_code == 415
    assert a.post(f"/api/investigations/{inv['id']}/attachments", files={"file": ("fake.pdf", b"<html>")}).status_code == 415
    assert a.post(f"/api/investigations/{inv['id']}/attachments", files={"file": ("bin.csv", b"a,b\x00c")}).status_code == 415
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "max_evidence_mb", 1)
    big = a.post(f"/api/investigations/{inv['id']}/attachments", files={"file": ("big.pdf", PDF + b"0" * (1024 * 1024))})
    assert big.status_code == 413
    # viewers may download; the response is a forced download with sandbox CSP
    d = as_role("viewer").get(f"/api/attachments/{att['id']}/download")
    assert d.status_code == 200 and d.content == PDF and "attachment" in d.headers["content-disposition"]
    assert d.headers["content-security-policy"] == "sandbox"
    # evidence is not reachable without authentication
    from conftest import login
    assert login(client.app, None).get(f"/api/attachments/{att['id']}/download").status_code == 401
    # removal: uploader or admin only, with reason; soft delete
    assert client.post(f"/api/attachments/{att['id']}/delete", json={"reason": "x"}).status_code == 422
    other = create(client).json()
    up2 = client.post(f"/api/investigations/{other['id']}/attachments", files={"file": ("admin.pdf", PDF)}).json()
    assert a.post(f"/api/attachments/{up2['id']}/delete", json={"reason": "not mine"}).status_code == 403
    assert a.post(f"/api/attachments/{att['id']}/delete", json={"reason": "wrong file"}).status_code == 200
    assert client.get(f"/api/attachments/{att['id']}/download").status_code == 404
    types = [e["type"] for e in client.get(f"/api/investigations/{inv['id']}/timeline").json()]
    assert {"comment", "attachment_added", "attachment_removed"} <= set(types)


# --------------------------------------------------------------------------- KPI comparison

def test_kpi_comparison_matches_kpi_engine(client, demo):
    from app.db import SessionLocal
    from app.services.data_access import Filters, get_company, load_records
    from app.services.kpi import compute_kpis

    inv = next(r for r in client.get("/api/investigations", params={"search": "M-06"}).json()["rows"])
    params = {"baseline_start": "2026-07-29", "baseline_end": "2026-08-11",
              "comparison_start": "2026-08-20", "comparison_end": "2026-09-02"}
    res = client.get(f"/api/investigations/{inv['id']}/kpi-comparison", params=params).json()
    assert res["sufficient_data"] and res["scope_label"] == "M-06"
    with SessionLocal() as db:
        mid = inv["machine"]["id"]
        cid = get_company(db).id
        b = compute_kpis(load_records(db, cid, Filters(dt.date(2026, 7, 29), dt.date(2026, 8, 11), machine_ids=[mid])))
        af = compute_kpis(load_records(db, cid, Filters(dt.date(2026, 8, 20), dt.date(2026, 9, 2), machine_ids=[mid])))
    m = {x["metric"]: x for x in res["metrics"]}
    assert m["oee"]["before"] == pytest.approx(b["oee"]["value"]) and m["oee"]["after"] == pytest.approx(af["oee"]["value"])
    assert m["oee"]["abs_diff"] == pytest.approx(af["oee"]["value"] - b["oee"]["value"])
    assert m["defect_rate"]["direction"] == ("better" if af["defect_rate"]["value"] < b["defect_rate"]["value"] else "worse")
    days_b = res["production_days_before"]
    assert m["unplanned_downtime_minutes"]["before_per_day"] == pytest.approx(b["unplanned_downtime_minutes"]["value"] / days_b)
    assert "not proof" in res["disclaimer"]
    assert len(res["series_before"]) == days_b


def test_kpi_comparison_insufficient_data_and_validation(client, demo):
    inv = client.get("/api/investigations", params={"search": "M-06"}).json()["rows"][0]
    res = client.get(f"/api/investigations/{inv['id']}/kpi-comparison", params={
        "baseline_start": "2026-08-01", "baseline_end": "2026-08-10",
        "comparison_start": "2027-01-01", "comparison_end": "2027-01-10"}).json()
    assert res["sufficient_data"] is False and all(m["before"] is None and m["after"] is None for m in res["metrics"])
    overlap = client.get(f"/api/investigations/{inv['id']}/kpi-comparison", params={
        "baseline_start": "2026-08-01", "baseline_end": "2026-08-20",
        "comparison_start": "2026-08-15", "comparison_end": "2026-08-30"})
    assert overlap.status_code == 422 and overlap.json()["code"] == "overlapping_periods"


def test_record_comparison_in_timeline(client, demo):
    inv = client.get("/api/investigations", params={"search": "M-07"}).json()["rows"][0]
    r = client.post(f"/api/investigations/{inv['id']}/kpi-comparison", json={})
    assert r.status_code == 200
    ev = client.get(f"/api/investigations/{inv['id']}/timeline").json()[-1]
    assert ev["type"] == "kpi_comparison_recorded" and "disclaimer" in ev["data"]


# --------------------------------------------------------------------------- rules & summary

def test_alerts_are_deduplicated_and_resolve(client, demo, analyst_id):
    s1 = client.get("/api/action-center/summary").json()
    s2 = client.get("/api/action-center/summary").json()
    ids1 = sorted(a["id"] for a in s1["alerts"])
    assert ids1 == sorted(a["id"] for a in s2["alerts"])
    unassigned = next(a for a in s1["alerts"] if a["rule"] == "critical_unassigned")
    first = unassigned["first_detected_at"]
    assert first and unassigned["details"]["threshold_hours"] == 4
    client.patch(f"/api/investigations/{unassigned['investigation_id']}", json={"owner_id": analyst_id}).raise_for_status()
    # dashboard requests are read-only (v0.3.1): the alert stays until the scheduled evaluation runs
    assert any(a["id"] == unassigned["id"] for a in client.get("/api/action-center/summary").json()["alerts"])
    ev = client.post("/api/action-center/alerts/evaluate").json()
    assert ev["run"]["status"] == "succeeded" and ev["evaluation"]["state"] == "ok"
    s3 = client.get("/api/action-center/summary").json()
    assert not any(a["id"] == unassigned["id"] for a in s3["alerts"])
    ack = client.post(f"/api/action-center/alerts/{s3['alerts'][0]['id']}/acknowledge")
    assert ack.status_code == 200 and ack.json()["acknowledged_at"]


def test_configurable_rules_and_repeated_anomalies(client, as_role, demo):
    assert as_role("analyst").put("/api/action-center/settings", json={}).status_code == 403
    r = client.put("/api/action-center/settings", json={"repeated_anomaly_count": 2, "repeated_anomaly_window_days": 90,
                                                        "critical_unassigned_hours": 100})
    assert r.status_code == 200 and r.json()["repeated_anomaly_count"] == 2
    client.post("/api/action-center/alerts/evaluate").raise_for_status()
    s = client.get("/api/action-center/summary").json()
    rules = [a["rule"] for a in s["alerts"]]
    assert "repeated_anomalies" in rules and "critical_unassigned" not in rules
    rep = next(a for a in s["alerts"] if a["rule"] == "repeated_anomalies")
    assert rep["details"]["anomaly_days"] >= 2
    client.put("/api/action-center/settings", json={}).raise_for_status()  # back to defaults
    client.post("/api/action-center/alerts/evaluate").raise_for_status()


def test_summary_kpis_match_database(client, demo):
    s = client.get("/api/action-center/summary").json()
    rows = client.get("/api/investigations", params={"page_size": 200}).json()["rows"]
    open_rows = [r for r in rows if r["status"] in ("open", "in_progress", "pending_verification", "reopened")]
    assert s["kpis"]["open_total"] == len(open_rows)
    assert s["kpis"]["critical_open"] == sum(1 for r in open_rows if r["severity"] == "critical")
    assert s["kpis"]["in_progress"] == sum(1 for r in rows if r["status"] == "in_progress")
    assert sum(x["count"] for x in s["by_severity"]) == len(open_rows)
    lines, _ = ids(client)
    f = client.get("/api/action-center/summary", params={"line_id": lines["L3"], "include_repeated": False}).json()
    assert f["kpis"]["open_total"] == sum(1 for r in open_rows if r["line"]["code"] == "L3")


def test_list_search_sort_paginate(client, demo):
    page = client.get("/api/investigations", params={"page_size": 3, "page": 2, "sort": "number", "order": "asc"}).json()
    assert page["total"] == 8 and [r["number"] for r in page["rows"]] == ["INV-2026-0004", "INV-2026-0005", "INV-2026-0006"]
    crit = client.get("/api/investigations", params={"severity": "critical", "open_only": True}).json()
    assert crit["total"] == 2
    assert client.get("/api/investigations", params={"search": "Ölfilm"}).json()["total"] == 1


# --------------------------------------------------------------------------- backup coverage

def test_backup_restore_includes_action_center(app, client, demo, fake_scanner):
    from app.db import engine
    if engine.dialect.name != "postgresql":
        pytest.skip("backups require PostgreSQL")
    inv = create(client).json()
    att = client.post(f"/api/investigations/{inv['id']}/attachments", files={"file": ("evidence.pdf", PDF)}).json()
    q_pdf = PDF + b"SCANNER-CRASH"
    q = client.post(f"/api/investigations/{inv['id']}/attachments", files={"file": ("unverified.pdf", q_pdf)}).json()
    assert att["scan_status"] == "clean" and q["scan_status"] == "failed" and q["quarantined"]
    name = client.post("/api/admin/backups", json={"note": "ac"}).json()["name"]
    client.post(f"/api/attachments/{att['id']}/delete", json={"reason": "removed after backup"}).raise_for_status()
    later = create(client, title="Created after backup").json()
    r = client.post(f"/api/admin/backups/{name}/restore", json={"password": TEST_PASSWORD, "confirm": "RESTORE"})
    assert r.status_code == 200, r.text
    me = client.post("/api/auth/login", json={"email": email_for("admin"), "password": TEST_PASSWORD}).json()
    client.headers["X-CSRF-Token"] = me["csrf_token"]
    assert client.get(f"/api/investigations/{later['id']}").status_code == 404
    d = client.get(f"/api/attachments/{att['id']}/download")
    assert d.status_code == 200 and d.content == PDF
    # quarantined evidence is part of the backup too (v0.3.1: evidence/quarantine/)
    dq = client.get(f"/api/attachments/{q['id']}/download", params={"acknowledge_risk": True})
    assert dq.status_code == 200 and dq.content == q_pdf


def test_demo_reload_removes_evidence_files(client, demo):
    from app.config import get_settings
    inv = create(client).json()
    client.post(f"/api/investigations/{inv['id']}/attachments", files={"file": ("e.pdf", PDF)}).raise_for_status()

    def files():
        return [p for p in get_settings().evidence_dir.rglob("*") if p.is_file()]
    assert files()  # quarantined (no scanner configured)
    client.post("/api/admin/demo/load", json={"password": TEST_PASSWORD, "confirm": "RELOAD"}).raise_for_status()
    assert not files()


def test_kpi_comparison_unit():
    from app.services.action_center import kpi_comparison
    rec = lambda d, op, ppt=450: {"date": pd.Timestamp(d), "actual_qty": 100, "good_qty": 98, "defective_qty": 2,
                                  "planned_production_minutes": ppt, "operating_minutes": op,
                                  "unplanned_downtime_minutes": ppt - op, "ideal_cycle_time_sec": 120, "planned_qty": 100}
    before = pd.DataFrame([rec(f"2026-01-0{i}", 300) for i in range(1, 5)])
    after = pd.DataFrame([rec(f"2026-02-0{i}", 400) for i in range(1, 3)])  # only 2 days → insufficient
    r = kpi_comparison(before, after)
    assert r["sufficient_data"] is False and r["metrics"][0]["before"] is None
    after = pd.DataFrame([rec(f"2026-02-0{i}", 400) for i in range(1, 5)])
    r = kpi_comparison(before, after)
    av = next(m for m in r["metrics"] if m["metric"] == "availability")
    assert av["before"] == pytest.approx(300 / 450) and av["after"] == pytest.approx(400 / 450)
    assert av["pct_change"] == pytest.approx((400 - 300) / 300) and av["direction"] == "better"
    dtm = next(m for m in r["metrics"] if m["metric"] == "unplanned_downtime_minutes")
    assert dtm["before_per_day"] == 150 and dtm["after_per_day"] == 50 and dtm["direction"] == "better"
