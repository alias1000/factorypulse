"""Generate SYNTHETIC sample import files in ../sample_data.

python -m scripts.generate_sample_files   (run from backend/)
"""
import csv
import datetime as dt
from pathlib import Path

from openpyxl import Workbook

from app.services.demo_data import generate

OUT = Path(__file__).resolve().parents[2] / "sample_data"
OUT.mkdir(exist_ok=True)


def flat(r):
    unplanned = [(reason, m) for reason, planned, m in r["downtime"] if not planned]
    reason = max(unplanned, key=lambda x: x[1])[0] if unplanned else ""
    cat = max(r["defects"], key=lambda x: x[1])[0] if r["defects"] else ""
    return r, reason, cat


def de_num(x):
    return f"{x:.1f}".replace(".", ",") if isinstance(x, float) and not float(x).is_integer() else str(int(x)) if isinstance(x, float) else str(x)


# 1) German CSV (semicolon, DD.MM.YYYY, decimal comma, German headers) – week 1 of October
week1 = generate(end_date=dt.date(2026, 10, 7), days=7, seed=7)
de_header = ["Datum", "Linie", "Maschine", "Schicht", "Artikelnummer", "Sollmenge", "Istmenge", "Gutteile",
             "Ausschuss", "Schichtdauer", "Pausenzeit", "Planbelegungszeit", "Laufzeit", "Stillstandszeit",
             "Stillstandsgrund", "Fehlerart", "Taktzeit"]
with open(OUT / "sample_import_valid_de.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.writer(fh, delimiter=";")
    w.writerow(de_header)
    for r, reason, cat in map(flat, week1):
        w.writerow([r["date"].strftime("%d.%m.%Y"), r["line"], r["machine"], r["shift"], r["product"], r["planned_qty"],
                    r["actual_qty"], r["good_qty"], r["defective_qty"], de_num(r["scheduled_minutes"]),
                    de_num(r["planned_downtime_minutes"]), de_num(r["planned_production_minutes"]),
                    de_num(r["operating_minutes"]), de_num(r["unplanned_downtime_minutes"]), reason, cat,
                    de_num(r["ideal_cycle_time_sec"] + 0.5)])

# 2) English XLSX – week 2 of October
week2 = generate(end_date=dt.date(2026, 10, 14), days=7, seed=14)
en_header = ["Date", "Production Line", "Machine ID", "Shift", "Product ID", "Planned Quantity", "Actual Quantity",
             "Good Quantity", "Defective Quantity", "Planned Production Minutes", "Operating Minutes",
             "Downtime Minutes", "Downtime Reason", "Defect Category", "Ideal Cycle Time"]
wb = Workbook()
ws = wb.active
ws.title = "Production"
ws.append(en_header)
for r, reason, cat in map(flat, week2):
    ws.append([r["date"], r["line"], r["machine"], r["shift"], r["product"], r["planned_qty"], r["actual_qty"],
               r["good_qty"], r["defective_qty"], r["planned_production_minutes"], r["operating_minutes"],
               r["unplanned_downtime_minutes"], reason or None, cat or None, r["ideal_cycle_time_sec"]])
    ws.cell(ws.max_row, 1).number_format = "yyyy-mm-dd"
wb.save(OUT / "sample_import_valid_en.xlsx")

# 3) CSV with deliberate errors
rows = [
    en_header,
    ["2026-10-15", "L1", "M-01", "F", "P-100", 420, 400, 394, 6, 450, 420, 30, "Tool change", "Burr", 48],     # valid
    ["2026-10-15", "L1", "M-02", "F", "P-100", 420, -5, 0, 0, 450, 420, 30, "Tool change", "", 48],            # negative
    ["2026-10-15", "L1", "M-03", "F", "P-100", 420, 400, 390, 6, 450, 420, 30, "Tool change", "Burr", 48],     # 390+6≠400
    ["2026-10-15", "L1", "", "F", "P-100", 420, 400, 394, 6, 450, 420, 30, "", "", 48],                       # missing machine
    ["15/13/2026", "L1", "M-04", "F", "P-100", 420, 400, 394, 6, 450, 420, 30, "", "", 48],                   # invalid date
    ["2026-10-15", "L2", "M-05", "F", "P-200", 230, 200, 196, 4, 450, 300, 30, "Material shortage", "", 95],  # 450-30≠300
    ["2026-10-15", "L2", "M-06", "F", "P-200", 230, "abc", 196, 4, 450, 420, 30, "", "", 95],                 # not a number
    ["2026-10-15", "L1", "M-01", "F", "P-100", 420, 400, 394, 6, 450, 420, 30, "Tool change", "Burr", 48],     # duplicate of row 2
    ["2026-10-15", "L2", "M-01", "S", "P-100", 420, 400, 394, 6, 450, 420, 30, "", "", 48],                   # M-01 belongs to L1
    ["2026-10-15", "L3", "M-09", "F", "P-300", 150, 140, 137, 3, 450, 400, 50, "", "", 140],                  # valid, warning: no reason
    ["2026-10-15", "L3", "M-10", "F", "P-300", 150, 12.5, 12, 0, 450, 400, 50, "Mechanical failure", "", 140],# not integer
]
with open(OUT / "sample_import_with_errors.csv", "w", newline="", encoding="utf-8") as fh:
    csv.writer(fh).writerows(rows)
print("written to", OUT)
