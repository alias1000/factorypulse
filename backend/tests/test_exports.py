"""Export tests including spreadsheet formula-injection protection."""
import io

from openpyxl import load_workbook

from app.services.sanitize import sanitize_cell


def test_sanitize_cell():
    assert sanitize_cell("=1+1") == "'=1+1"
    assert sanitize_cell("+49") == "'+49"
    assert sanitize_cell("@SUM(A1)") == "'@SUM(A1)"
    assert sanitize_cell("-cmd") == "'-cmd"
    assert sanitize_cell("M-01") == "M-01"
    assert sanitize_cell(-5) == -5


def test_production_exports(client, demo):
    x = client.get("/api/exports/production", params={"dimension": "machine", "format": "xlsx"})
    assert x.status_code == 200
    wb = load_workbook(io.BytesIO(x.content))
    ws = wb["Production"]
    assert ws.max_row == 13  # header + 12 machines
    assert ws.cell(1, 1).value == "Machine"
    c = client.get("/api/exports/production", params={"dimension": "day", "format": "csv"})
    assert c.status_code == 200 and c.text.lstrip("﻿").startswith("Date;")
    assert client.get("/api/exports/downtime-events", params={"format": "csv", "machine_id": 1}).status_code == 200
    assert client.get("/api/exports/quality").status_code == 200


def test_formula_injection_in_exports(client, demo):
    csv = ("Date,Line,Machine,Shift,Product,Actual Quantity\n"
           "2026-11-03,L1,M-01,F,=HYPERLINK(\"http://evil\"),10\n")
    up = client.post("/api/imports/upload", files={"file": ("evil.csv", io.BytesIO(csv.encode()))}).json()
    opts = {"mapping": up["suggested_mapping"]}
    client.post(f"/api/imports/{up['job']['id']}/commit", json={"options": opts}).raise_for_status()
    r = client.get("/api/exports/production", params={"dimension": "product", "format": "csv"})
    assert "'=HYPERLINK" in r.text
    x = client.get("/api/exports/production", params={"dimension": "product", "format": "xlsx"})
    ws = load_workbook(io.BytesIO(x.content))["Production"]
    values = [ws.cell(i, 1).value for i in range(2, ws.max_row + 1)]
    assert any(isinstance(v, str) and v.startswith("'=HYPERLINK") for v in values)
    assert all(ws.cell(i, 1).data_type != "f" for i in range(2, ws.max_row + 1))
