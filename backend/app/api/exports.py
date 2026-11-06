"""Table exports (CSV / XLSX) with formula-injection protection."""
from __future__ import annotations

import csv
import io

from ..clock import local_now
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from sqlalchemy.orm import Session

from ..db import get_db
from .deps import require
from ..services import analytics as an
from ..services.data_access import Filters, filters_dependency, get_company, load_downtime, load_records
from ..services.sanitize import sanitize_row

router = APIRouter(prefix="/api/exports", tags=["exports"], dependencies=[Depends(require("data.read"))])

PRODUCTION_COLUMNS = [
    ("actual_qty", "Actual quantity", None), ("planned_qty", "Planned quantity", None),
    ("target_achievement", "Target achievement", "pct"), ("good_qty", "Good quantity", None),
    ("defective_qty", "Defective quantity", None), ("defect_rate", "Defect rate", "pct"),
    ("planned_production_minutes", "Planned production min", None), ("operating_minutes", "Operating min", None),
    ("unplanned_downtime_minutes", "Unplanned downtime min", None), ("availability", "Availability", "pct"),
    ("performance", "Performance", "pct"), ("quality", "Quality", "pct"), ("oee", "OEE", "pct"),
    ("units_per_operating_hour", "Units per operating hour", "num"),
]
DIM_COLUMNS = {
    "day": [("date", "Date")], "week": [("week", "ISO week")], "line": [("line_code", "Line"), ("line_name", "Line name")],
    "machine": [("machine_code", "Machine"), ("machine_name", "Machine name"), ("line_code", "Line")],
    "shift": [("shift_code", "Shift"), ("shift_name", "Shift name")],
    "product": [("product_code", "Product"), ("product_name", "Product name")],
}


def _stream(header: list[str], rows: list[list], fmt: str, filename: str, pct_cols: set[int] = frozenset(),
            title: str = "Export", note: str | None = None):
    stamp = local_now().strftime("%Y%m%d-%H%M")
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(header)
        for r in rows:
            w.writerow(sanitize_row(r))
        return StreamingResponse(io.BytesIO(buf.getvalue().encode("utf-8-sig")), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="{filename}_{stamp}.csv"'})
    wb = Workbook()
    ws = wb.active
    ws.title = title[:31]
    ws.append(header)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="0F2744")
    for r in rows:
        ws.append(sanitize_row(r))
    for idx in pct_cols:
        for row in ws.iter_rows(min_row=2, min_col=idx + 1, max_col=idx + 1):
            row[0].number_format = "0.0%"
    for i, h in enumerate(header, start=1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = max(12, min(40, len(h) + 4))
    ws.freeze_panes = "A2"
    if note:
        ws2 = wb.create_sheet("Info")
        ws2.append([note])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="{filename}_{stamp}.xlsx"'})


@router.get("/production", summary="Export the production breakdown table")
def export_production(
    dimension: str = Query("day", pattern="^(day|week|line|machine|shift|product)$"),
    format: str = Query("xlsx", pattern="^(csv|xlsx)$"),
    f: Filters = Depends(filters_dependency),
    db: Session = Depends(get_db),
):
    c = get_company(db)
    rows = an.breakdown(load_records(db, c.id, f), dimension)
    dims = DIM_COLUMNS[dimension]
    header = [d[1] for d in dims] + [m[1] for m in PRODUCTION_COLUMNS]
    pct = {len(dims) + i for i, m in enumerate(PRODUCTION_COLUMNS) if m[2] == "pct"}
    out = [[r.get(d[0]) for d in dims] + [r.get(m[0]) for m in PRODUCTION_COLUMNS] for r in rows]
    note = (f"{c.name} – production by {dimension}. Filters: {f.to_dict()}. Ratios as decimal fractions. "
            "Empty cells = not computable from available data (N/A)."
            + (" SYNTHETIC DEMO DATA." if c.is_synthetic else ""))
    return _stream(header, out, format, f"production_by_{dimension}", pct, "Production", note)


@router.get("/downtime-events", summary="Export unplanned downtime events")
def export_downtime(format: str = Query("xlsx", pattern="^(csv|xlsx)$"), f: Filters = Depends(filters_dependency),
                    db: Session = Depends(get_db)):
    c = get_company(db)
    df = load_downtime(db, c.id, f)
    header = ["Date", "Line", "Machine", "Shift", "Product", "Reason", "Minutes"]
    out = [] if df.empty else [
        [r.date.date().isoformat(), r.line_code, r.machine_code, r.shift_code, r.product_code, r.reason, r.minutes]
        for r in df.itertuples()]
    return _stream(header, out, format, "downtime_events", title="Downtime")


@router.get("/quality", summary="Export defects by machine")
def export_quality(format: str = Query("xlsx", pattern="^(csv|xlsx)$"), f: Filters = Depends(filters_dependency),
                   db: Session = Depends(get_db)):
    c = get_company(db)
    rows = an.breakdown(load_records(db, c.id, f), "machine")
    header = ["Machine", "Machine name", "Line", "Actual quantity", "Good quantity", "Defective quantity",
              "Defect rate", "Quality"]
    out = [[r["machine_code"], r["machine_name"], r["line_code"], r["actual_qty"], r["good_qty"],
            r["defective_qty"], r["defect_rate"], r["quality"]] for r in rows]
    return _stream(header, out, format, "quality_by_machine", {6, 7}, "Quality")
