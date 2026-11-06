"""Import pipeline tests: parsing, validation rules, duplicate protection, transactional commit."""
import io
from pathlib import Path

import pytest

from app.services.importer import parse_date, parse_number, suggest_mapping

SAMPLES = Path(__file__).resolve().parents[2] / "sample_data"


def upload(client, path: Path):
    with open(path, "rb") as fh:
        return client.post("/api/imports/upload", files={"file": (path.name, fh)})


def upload_bytes(client, name: str, data: bytes):
    return client.post("/api/imports/upload", files={"file": (name, io.BytesIO(data))})


# ---------------------------------------------------------------- unit helpers

@pytest.mark.parametrize("raw,dec,expected", [
    ("1.234,5", ",", 1234.5), ("1,234.5", ".", 1234.5), ("48,5", ",", 48.5), ("48.5", ".", 48.5),
    ("1.000", ",", 1000.0), (12, ".", 12.0), ("  7 ", ".", 7.0), ("-3", ".", -3.0),
])
def test_parse_number(raw, dec, expected):
    assert parse_number(raw, dec) == expected


@pytest.mark.parametrize("raw", ["abc", "", "1,2,3x", float("nan")])
def test_parse_number_rejects(raw):
    with pytest.raises(ValueError):
        parse_number(raw, ".")


def test_parse_date_formats():
    import datetime as dt
    assert parse_date("2026-10-01") == dt.date(2026, 10, 1)
    assert parse_date("01.10.2026") == dt.date(2026, 10, 1)
    assert parse_date("01.10.26") == dt.date(2026, 10, 1)
    assert parse_date(46296) == dt.date(2026, 10, 1)  # Excel serial
    with pytest.raises(ValueError):
        parse_date("15/13/2026")


def test_suggest_mapping_german_headers():
    m = suggest_mapping(["Datum", "Linie", "Maschine", "Schicht", "Artikelnummer", "Istmenge", "Gutteile",
                         "Ausschuss", "Stillstandszeit", "Stillstandsgrund", "Taktzeit"])
    assert m["date"] == "Datum" and m["machine"] == "Maschine" and m["actual_qty"] == "Istmenge"
    assert m["defective_qty"] == "Ausschuss" and m["downtime_minutes"] == "Stillstandszeit"
    assert m["ideal_cycle_time"] == "Taktzeit"
    assert m["planned_qty"] is None


# ---------------------------------------------------------------- end-to-end via API

def test_upload_rejects_bad_types_and_sizes(client):
    assert upload_bytes(client, "data.txt", b"a,b").status_code == 415
    assert upload_bytes(client, "fake.xlsx", b"not a zip").status_code == 415
    assert upload_bytes(client, "empty.csv", b"").status_code == 400
    assert upload_bytes(client, "bin.csv", b"\x00\x01\x02").status_code == 415


def test_valid_german_csv_import_flow(client, demo):
    before = client.get("/api/meta").json()["data_range"]["record_count"]
    r = upload(client, SAMPLES / "sample_import_valid_de.csv")
    assert r.status_code == 200, r.text
    up = r.json()
    assert up["delimiter"] == ";"
    assert up["row_count"] == 192
    mapping = up["suggested_mapping"]
    assert all(mapping[k] for k in ("date", "line", "machine", "shift", "product", "actual_qty", "good_qty",
                                    "defective_qty", "planned_production_minutes", "operating_minutes",
                                    "downtime_minutes", "ideal_cycle_time"))
    job = up["job"]["id"]
    opts = {"mapping": mapping}
    v = client.post(f"/api/imports/{job}/validate", json=opts).json()
    assert v["error_rows"] == 0, v["issues"][:5]
    assert v["valid_rows"] == 192 and v["can_commit"]
    c = client.post(f"/api/imports/{job}/commit", json={"options": opts})
    assert c.status_code == 200, c.text
    assert c.json()["imported_rows"] == 192
    meta = client.get("/api/meta").json()
    assert meta["data_range"]["record_count"] == before + 192
    assert meta["data_range"]["end"] == "2026-10-07"
    # imported data shows up in dashboards
    k = client.get("/api/kpis", params={"start_date": "2026-10-01", "end_date": "2026-10-07"}).json()
    assert k["record_count"] == 192 and k["oee"]["value"] is not None
    # decimal comma parsed correctly: 48,5 s
    ev = client.get("/api/downtime/events", params={"start_date": "2026-10-01", "end_date": "2026-10-07"}).json()
    assert ev["total"] > 0

    # same file again -> refused at commit (duplicate import protection)
    up2 = upload(client, SAMPLES / "sample_import_valid_de.csv").json()
    assert up2["already_imported"]["id"] == job
    v2 = client.post(f"/api/imports/{up2['job']['id']}/validate", json=opts).json()
    assert v2["can_commit"] is False and v2["duplicate_existing_rows"] == 192
    c2 = client.post(f"/api/imports/{up2['job']['id']}/commit", json={"options": opts})
    assert c2.status_code == 409

    # revert removes the records again
    rv = client.post(f"/api/imports/{job}/revert", json={"confirm": "REVERT"}).json()
    assert rv["deleted_records"] == 192
    assert client.get("/api/meta").json()["data_range"]["record_count"] == before


def test_xlsx_import(client, demo):
    up = upload(client, SAMPLES / "sample_import_valid_en.xlsx").json()
    assert up["sheet"] == "Production"
    opts = {"mapping": up["suggested_mapping"]}
    v = client.post(f"/api/imports/{up['job']['id']}/validate", json=opts).json()
    assert v["error_rows"] == 0 and v["valid_rows"] == up["row_count"]
    c = client.post(f"/api/imports/{up['job']['id']}/commit", json={"options": opts}).json()
    assert c["imported_rows"] == up["row_count"]


def test_invalid_rows_are_reported_and_block_import(client, demo):
    up = upload(client, SAMPLES / "sample_import_with_errors.csv").json()
    job = up["job"]["id"]
    opts = {"mapping": up["suggested_mapping"]}
    v = client.post(f"/api/imports/{job}/validate", json=opts).json()
    codes = {(i["row"], i["code"]) for i in v["issues"] if i["severity"] == "error"}
    assert (3, "negative_value") in codes
    assert (4, "quantity_mismatch") in codes
    assert (5, "missing_required") in codes
    assert (6, "invalid_date") in codes
    assert (7, "time_inconsistent") in codes
    assert (8, "invalid_number") in codes
    assert (9, "duplicate_in_file") in codes
    assert (10, "machine_line_conflict") in codes
    assert (12, "invalid_integer") in codes
    warn = {(i["row"], i["code"]) for i in v["issues"] if i["severity"] == "warning"}
    assert (11, "downtime_without_reason") in warn
    assert v["valid_rows"] == 2 and v["error_rows"] == 9
    assert all(i["message"] for i in v["issues"])

    before = client.get("/api/meta").json()["data_range"]["record_count"]
    blocked = client.post(f"/api/imports/{job}/commit", json={"options": opts})
    assert blocked.status_code == 422
    assert client.get("/api/meta").json()["data_range"]["record_count"] == before  # nothing written

    partial = client.post(f"/api/imports/{job}/commit", json={"options": opts, "import_valid_rows_only": True})
    assert partial.status_code == 200 and partial.json()["imported_rows"] == 2
    assert client.get("/api/meta").json()["data_range"]["record_count"] == before + 2


def test_missing_values_stay_null_and_derivations(client, demo):
    csv = ("Date,Line,Machine,Shift,Product,Actual Quantity,Defective Quantity,Planned Production Minutes,"
           "Downtime Minutes\n"
           "2026-11-02,L9,X-1,F,NEW-1,100,4,450,50\n"
           "2026-11-02,L9,X-1,S,NEW-1,80,,,\n")
    up = upload_bytes(client, "partial.csv", csv.encode()).json()
    opts = {"mapping": up["suggested_mapping"]}
    v = client.post(f"/api/imports/{up['job']['id']}/validate", json=opts).json()
    assert v["error_rows"] == 0
    assert v["missing_by_field"]["defective_qty"] == 1
    assert v["new_master_data"]["machines"] == ["X-1"]
    assert any(i["code"] == "derived_value" for i in v["issues"])
    client.post(f"/api/imports/{up['job']['id']}/commit", json={"options": opts}).raise_for_status()
    meta = client.get("/api/meta").json()
    line = next(x for x in meta["lines"] if x["code"] == "L9")
    k = client.get("/api/kpis", params={"line_id": line["id"]}).json()
    assert k["total_production"]["value"] == 180
    assert k["defect_rate"]["value"] == pytest.approx(4 / 100)  # second row has no defect info -> excluded
    assert k["availability"]["value"] == pytest.approx(400 / 450)  # operating derived as 450-50
    assert k["performance"]["value"] is None  # no ideal cycle time -> N/A, not fabricated
    assert k["oee"]["value"] is None


def test_unmapped_required_field_is_rejected(client, demo):
    up = upload_bytes(client, "x.csv", b"Date,Line\n2026-11-01,L1\n").json()
    r = client.post(f"/api/imports/{up['job']['id']}/validate", json={"mapping": up["suggested_mapping"]})
    assert r.status_code == 422 and "Required field" in r.json()["detail"]


def test_template_and_history(client):
    assert client.get("/api/imports/template?format=xlsx").status_code == 200
    t = client.get("/api/imports/template?format=csv")
    assert t.status_code == 200 and "Production Line" in t.text
    hist = client.get("/api/imports").json()
    assert isinstance(hist, list)
    # v0.3.3: order-independent – the event filter returns only import events (none, if no import ran yet)
    log = client.get("/api/audit-log", params={"event": "import."}).json()
    assert isinstance(log, list) and all(e["event"].startswith("import.") for e in log)
