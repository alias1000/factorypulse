"""Report generation (PDF + Excel) for all report types."""
import datetime as dt
import io

import pytest
from openpyxl import load_workbook

from app.services.reports.builder import ReportError, resolve_period


def test_resolve_period():
    d = dt.date(2026, 9, 16)  # Wednesday
    assert resolve_period("daily", d, None) == (d, d)
    assert resolve_period("weekly", d, None) == (dt.date(2026, 9, 14), dt.date(2026, 9, 20))
    assert resolve_period("monthly", d, None) == (dt.date(2026, 9, 1), dt.date(2026, 9, 30))
    assert resolve_period("quality", d, dt.date(2026, 9, 30)) == (d, dt.date(2026, 9, 30))
    with pytest.raises(ReportError):
        resolve_period("downtime", d, None)
    with pytest.raises(ReportError):
        resolve_period("downtime", d, dt.date(2026, 9, 1))


@pytest.mark.parametrize("rtype,fmt,start,end,lang", [
    ("daily", "pdf", "2026-09-02", None, "de"),
    ("weekly", "pdf", "2026-09-16", None, "en"),
    ("monthly", "pdf", "2026-08-15", None, "en"),
    ("quality", "pdf", "2026-07-03", "2026-09-30", "en"),
    ("downtime", "pdf", "2026-07-03", "2026-09-30", "de"),
    ("daily", "xlsx", "2026-09-02", None, "en"),
    ("weekly", "xlsx", "2026-09-14", None, "de"),
    ("monthly", "xlsx", "2026-08-01", None, "en"),
    ("quality", "xlsx", "2026-07-03", "2026-09-30", "en"),
    ("downtime", "xlsx", "2026-07-03", "2026-09-30", "en"),
])
def test_report_generation_and_download(client, demo, rtype, fmt, start, end, lang):
    body = {"report_type": rtype, "format": fmt, "start_date": start, "language": lang}
    if end:
        body["end_date"] = end
    r = client.post("/api/reports", json=body)
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["status"] == "completed" and job["file_size"] > 1000
    d = client.get(f"/api/reports/{job['id']}/download")
    assert d.status_code == 200
    if fmt == "pdf":
        assert d.content[:5] == b"%PDF-"
        assert d.headers["content-type"] == "application/pdf"
    else:
        wb = load_workbook(io.BytesIO(d.content))
        summary = wb.worksheets[0]
        text = " ".join(str(c.value) for row in summary.iter_rows() for c in row if c.value)
        assert "Musterwerk" in text
        assert "SYNTHETIC" in text or "SYNTHETISCHE" in text
        assert len(wb.sheetnames) >= 5


def test_monthly_excel_contains_kpis_and_anomalies(client, demo):
    r = client.post("/api/reports", json={"report_type": "monthly", "format": "xlsx", "start_date": "2026-08-01"}).json()
    wb = load_workbook(io.BytesIO(client.get(f"/api/reports/{r['id']}/download").content))
    ws = wb.worksheets[0]
    labels = [ws.cell(i, 1).value for i in range(1, ws.max_row + 1)]
    assert "OEE" in labels and "Availability" in labels
    an = wb["Anomalies"]
    entities = {an.cell(i, 2).value for i in range(2, an.max_row + 1)}
    assert "M-06" in entities and "L3" in entities  # injected 2026-08-12 and 2026-08-26


def test_report_with_filters_and_empty_period(client, demo):
    meta = client.get("/api/meta").json()
    line = meta["lines"][0]["id"]
    r = client.post("/api/reports", json={"report_type": "weekly", "format": "pdf", "start_date": "2026-09-14",
                                          "filters": {"line_ids": [line]}})
    assert r.status_code == 200 and r.json()["filters"]["line_ids"] == [line]
    e = client.post("/api/reports", json={"report_type": "daily", "format": "pdf", "start_date": "2030-01-01"})
    assert e.status_code == 200  # renders "no data" page instead of failing
    bad = client.post("/api/reports", json={"report_type": "quality", "format": "pdf", "start_date": "2026-09-01"})
    assert bad.status_code == 422


def test_report_history_and_delete(client, demo):
    r = client.post("/api/reports", json={"report_type": "daily", "format": "xlsx", "start_date": "2026-09-01"}).json()
    hist = client.get("/api/reports").json()
    assert any(h["id"] == r["id"] for h in hist)
    assert client.delete(f"/api/reports/{r['id']}").status_code == 200
    assert client.get(f"/api/reports/{r['id']}/download").status_code == 404


def test_report_titles_are_distinct_from_kpi_labels():
    """Regression: the quality report title must not be shadowed by the KPI label "Quality"."""
    from app.services.reports.i18n import t

    assert t("en", "title.quality") == "Quality Report" and t("en", "quality") == "Quality"
    assert t("de", "title.quality") == "Qualitätsbericht" and t("de", "quality") == "Qualität"
    for rt in ("daily", "weekly", "monthly", "quality", "downtime"):
        assert t("en", f"title.{rt}") != f"title.{rt}" and t("de", f"title.{rt}") != t("en", f"title.{rt}")
