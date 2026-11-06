"""CSV / XLSX import pipeline: parse → map → validate → (review) → transactional commit.

Design rules
* Missing measurements are stored as NULL – never silently replaced by zero.
* Negative or non-integer quantities and internally inconsistent rows are rejected.
* good + defective must equal actual when all three are supplied.
* Planned production time − unplanned downtime must equal operating time (±1 min).
* Re-importing an identical file (same SHA-256) is refused; rows whose natural key
  (date, machine, shift, product) already exists are skipped and reported.
* The commit re-validates the stored file server-side and writes everything in
  ONE transaction, so a failure never leaves a partial import.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import math
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    DefectEvent,
    DowntimeEvent,
    ImportJob,
    Machine,
    Product,
    ProductionLine,
    ProductionRecord,
    Shift,
)

# ----------------------------------------------------------------------------- field catalogue

FieldType = Literal["date", "text", "int", "float"]


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    type: FieldType
    required: bool
    synonyms: tuple[str, ...]
    description: str = ""


FIELDS: list[FieldSpec] = [
    FieldSpec("date", "Date", "date", True,
              ("date", "datum", "productiondate", "produktionsdatum", "tag", "day"), "Production date"),
    FieldSpec("line", "Production Line", "text", True,
              ("line", "productionline", "linie", "produktionslinie", "fertigungslinie", "linecode", "lineid"),
              "Line code, e.g. L1"),
    FieldSpec("machine", "Machine ID", "text", True,
              ("machine", "machineid", "machinecode", "maschine", "maschinenid", "maschinennr", "maschinennummer",
               "anlage", "anlagenid"), "Machine code, e.g. M-01"),
    FieldSpec("shift", "Shift", "text", True, ("shift", "schicht", "shiftcode", "schichtcode"), "Shift code, e.g. F"),
    FieldSpec("product", "Product ID", "text", True,
              ("product", "productid", "productcode", "produkt", "produktid", "artikel", "artikelnr",
               "artikelnummer", "material", "materialnr", "teilenummer", "sku"), "Product / article code"),
    FieldSpec("planned_qty", "Planned Quantity", "int", False,
              ("plannedquantity", "plannedqty", "planned", "plannedunits", "sollmenge", "planmenge", "soll",
               "target", "targetqty"), "Planned units"),
    FieldSpec("actual_qty", "Actual Quantity", "int", True,
              ("actualquantity", "actualqty", "actual", "produced", "producedqty", "totalproduced", "istmenge",
               "ist", "menge", "stueckzahl", "gesamtmenge", "output"), "Total produced units (good + defective)"),
    FieldSpec("good_qty", "Good Quantity", "int", False,
              ("goodquantity", "goodqty", "good", "goodunits", "gutteile", "gutmenge", "iomenge", "io"),
              "Good units"),
    FieldSpec("defective_qty", "Defective Quantity", "int", False,
              ("defectivequantity", "defectiveqty", "defective", "defects", "scrap", "rejects", "ausschuss",
               "ausschussmenge", "nio", "niomenge", "fehlteile"), "Defective units"),
    FieldSpec("scheduled_minutes", "Scheduled Minutes", "float", False,
              ("scheduledminutes", "scheduledtime", "shiftminutes", "shiftlength", "schichtdauer", "schichtminuten",
               "schichtzeit"), "Shift length incl. breaks (optional)"),
    FieldSpec("planned_downtime_minutes", "Planned Downtime Minutes", "float", False,
              ("planneddowntimeminutes", "planneddowntime", "breaks", "breakminutes", "geplantestillstandszeit",
               "pausen", "pausenzeit", "geplanterstillstand"), "Breaks / planned maintenance (optional)"),
    FieldSpec("planned_production_minutes", "Planned Production Minutes", "float", False,
              ("plannedproductionminutes", "plannedproductiontime", "plannedminutes", "geplanteproduktionszeit",
               "planbelegungszeit", "belegungszeit", "planproduktionsminuten"),
              "Planned production time (scheduled − planned downtime)"),
    FieldSpec("operating_minutes", "Operating Minutes", "float", False,
              ("operatingminutes", "operatingtime", "runtime", "runminutes", "laufzeit", "betriebszeit",
               "hauptnutzungszeit"), "Actual run time"),
    FieldSpec("downtime_minutes", "Downtime Minutes", "float", False,
              ("downtimeminutes", "downtime", "unplanneddowntime", "unplanneddowntimeminutes", "stillstand",
               "stillstandszeit", "stillstandminuten", "ausfallzeit", "stoerzeit"), "UNPLANNED downtime"),
    FieldSpec("downtime_reason", "Downtime Reason", "text", False,
              ("downtimereason", "reason", "stillstandsgrund", "grund", "stoergrund", "ausfallgrund"),
              "Reason of unplanned downtime"),
    FieldSpec("defect_category", "Defect Category", "text", False,
              ("defectcategory", "defecttype", "defectreason", "fehlerart", "fehlerkategorie", "ausschussgrund",
               "fehlerursache"), "Category of defective units (optional)"),
    FieldSpec("ideal_cycle_time", "Ideal Cycle Time", "float", False,
              ("idealcycletime", "idealcycletimesec", "idealcycletimeseconds", "cycletime", "taktzeit",
               "idealtaktzeit", "sollzykluszeit", "zykluszeit", "idealzykluszeit"), "Ideal time per unit"),
]
FIELD_BY_KEY = {f.key: f for f in FIELDS}
TIME_TOLERANCE_MIN = 1.0
MAX_CODE_LEN = 50
MAX_TEXT_LEN = 200


class ImportOptions(BaseModel):
    mapping: dict[str, str | None] = Field(default_factory=dict, description="field key -> source column name")
    sheet: str | None = None
    header_row: int = Field(1, ge=1, le=50)
    number_format: Literal["auto", "de", "en"] = "auto"
    ideal_cycle_time_unit: Literal["seconds", "minutes"] = "seconds"
    skip_existing_duplicates: bool = True


# ----------------------------------------------------------------------------- parsing helpers

def normalize_header(h: Any) -> str:
    s = str(h or "").strip().lower()
    s = s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s)


def suggest_mapping(columns: list[str]) -> dict[str, str | None]:
    norm = {c: normalize_header(c) for c in columns}
    used: set[str] = set()
    mapping: dict[str, str | None] = {}
    for f in FIELDS:
        hit = None
        for col, n in norm.items():  # exact synonym first
            if col not in used and n in f.synonyms:
                hit = col
                break
        mapping[f.key] = hit
        if hit:
            used.add(hit)
    return mapping


@dataclass
class ParsedFile:
    columns: list[str]
    frame: pd.DataFrame  # object dtype; raw cell values
    delimiter: str | None = None
    encoding: str | None = None
    sheets: list[str] = field(default_factory=list)
    sheet: str | None = None


class ImportFileError(ValueError):
    pass


def _dedupe_columns(cols: list[Any]) -> list[str]:
    out, seen = [], defaultdict(int)
    for i, c in enumerate(cols):
        name = str(c).strip() if c is not None and str(c).strip() and not (isinstance(c, float) and math.isnan(c)) else f"Column {i + 1}"
        seen[name] += 1
        out.append(name if seen[name] == 1 else f"{name} ({seen[name]})")
    return out


def read_table(path: Path, file_type: str, sheet: str | None = None, header_row: int = 1,
               max_rows: int = 100_000) -> ParsedFile:
    if file_type == "csv":
        raw = path.read_bytes()
        text = None
        for enc in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                text = raw.decode(enc)
                encoding = enc
                break
            except UnicodeDecodeError:
                continue
        if text is None:  # pragma: no cover - latin-1 never fails
            raise ImportFileError("Could not decode CSV file")
        if "\x00" in text:
            raise ImportFileError("File looks binary, not a CSV text file")
        sample = text[:20000]
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ";" if sample.count(";") > sample.count(",") else ","
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        rows = list(reader)
        rows = rows[header_row - 1:]
        if not rows:
            raise ImportFileError("The file contains no header row")
        header = _dedupe_columns(rows[0])
        body = [r + [""] * (len(header) - len(r)) for r in rows[1:] if any(str(x).strip() for x in r)]
        if len(body) > max_rows:
            raise ImportFileError(f"Too many rows ({len(body)}); the limit is {max_rows}")
        df = pd.DataFrame([r[: len(header)] for r in body], columns=header, dtype=object)
        return ParsedFile(header, df, delimiter=delimiter, encoding=encoding)

    if file_type == "xlsx":
        from openpyxl import load_workbook

        try:
            wb = load_workbook(path, read_only=True, data_only=True)  # data_only: cached values, no formulas
        except Exception as exc:
            raise ImportFileError(f"Could not open Excel file: {exc.__class__.__name__}") from exc
        try:
            sheets = wb.sheetnames
            ws = wb[sheet] if sheet and sheet in sheets else wb[sheets[0]]
            rows = []
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i < header_row - 1:
                    continue
                rows.append(list(row))
                if len(rows) > max_rows + 1:
                    raise ImportFileError(f"Too many rows; the limit is {max_rows}")
        finally:
            wb.close()
        if not rows:
            raise ImportFileError("The selected sheet is empty")
        # trim trailing empty columns
        width = max((max((i + 1 for i, v in enumerate(r) if v not in (None, "")), default=0) for r in rows), default=0)
        header = _dedupe_columns(rows[0][:width])
        body = [r[:width] + [None] * (width - len(r)) for r in rows[1:]
                if any(v not in (None, "") and str(v).strip() for v in r[:width])]
        df = pd.DataFrame(body, columns=header, dtype=object)
        return ParsedFile(header, df, sheets=sheets, sheet=ws.title)

    raise ImportFileError("Unsupported file type")


def _is_empty(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    return isinstance(v, str) and v.strip() == ""


def parse_number(v: Any, decimal: str) -> float:
    """Parse numbers like '1.234,5' (de) or '1,234.5' (en). Raises ValueError."""
    if isinstance(v, bool):
        raise ValueError("boolean")
    if isinstance(v, (int, float)):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            raise ValueError("nan")
        return float(v)
    s = str(v).strip().replace(" ", "").replace(" ", "").replace("'", "")
    if not s:
        raise ValueError("empty")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".") if decimal == "," else s.replace(",", "")
    elif "." in s and decimal == ",":
        # in German format a lone dot is a thousands separator if followed by exactly 3 digits groups
        if re.fullmatch(r"-?\d{1,3}(\.\d{3})+", s):
            s = s.replace(".", "")
    if not re.fullmatch(r"-?\d+(\.\d+)?([eE][-+]?\d+)?", s):
        raise ValueError("not a number")
    out = float(s)
    if math.isinf(out):
        raise ValueError("inf")
    return out


_DATE_PATTERNS = [
    (re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})"), ("y", "m", "d")),
    (re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$"), ("d", "m", "y")),
    (re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{2})$"), ("d", "m", "yy")),
    (re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$"), ("d", "m", "y")),
    (re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})$"), ("y", "m", "d")),
]


def parse_date(v: Any) -> dt.date:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if 20000 <= v <= 80000:  # Excel serial date
            return dt.date(1899, 12, 30) + dt.timedelta(days=int(v))
        raise ValueError("number is not a date")
    s = str(v).strip()
    for rx, order in _DATE_PATTERNS:
        m = rx.match(s)
        if m:
            parts = dict(zip(order, m.groups()))
            y = int(parts["y"]) if "y" in parts else 2000 + int(parts["yy"])
            return dt.date(y, int(parts["m"]), int(parts["d"]))
    raise ValueError("unrecognised date")


def _display(v: Any) -> str | None:
    if _is_empty(v):
        return None
    if isinstance(v, dt.datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, dt.date):
        return v.isoformat()
    s = str(v)
    return s if len(s) <= 80 else s[:77] + "..."


def preview_rows(pf: ParsedFile, n: int = 20) -> list[dict[str, str | None]]:
    return [{c: _display(v) for c, v in row.items()} for row in pf.frame.head(n).to_dict(orient="records")]


# ----------------------------------------------------------------------------- validation

@dataclass
class Issue:
    row: int
    field: str | None
    column: str | None
    value: str | None
    severity: Literal["error", "warning"]
    code: str
    message: str

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class ValidationResult:
    total_rows: int
    rows: list[dict]  # parsed, valid rows ready for insert (status=valid)
    issues: list[Issue]
    error_row_numbers: set[int]
    warning_row_numbers: set[int]
    duplicate_existing_rows: list[int]
    missing_by_field: dict[str, int]
    new_master_data: dict[str, list[str]]
    date_range: tuple[dt.date | None, dt.date | None]

    def summary(self, max_issues: int = 2000) -> dict:
        errors = [i for i in self.issues if i.severity == "error"]
        warnings = [i for i in self.issues if i.severity == "warning"]
        counts: dict[str, int] = defaultdict(int)
        for i in self.issues:
            counts[i.code] += 1
        return {
            "total_rows": self.total_rows,
            "valid_rows": len(self.rows),
            "error_rows": len(self.error_row_numbers),
            "warning_rows": len(self.warning_row_numbers),
            "duplicate_existing_rows": len(self.duplicate_existing_rows),
            "error_count": len(errors),
            "warning_count": len(warnings),
            "issue_counts": dict(counts),
            "missing_by_field": self.missing_by_field,
            "new_master_data": self.new_master_data,
            "date_range": {
                "start": self.date_range[0].isoformat() if self.date_range[0] else None,
                "end": self.date_range[1].isoformat() if self.date_range[1] else None,
            },
            "issues": [i.to_dict() for i in (errors + warnings)[:max_issues]],
            "issues_truncated": len(self.issues) > max_issues,
        }


def _decimal_for(opts: ImportOptions, pf: ParsedFile) -> str:
    if opts.number_format == "de":
        return ","
    if opts.number_format == "en":
        return "."
    return "," if pf.delimiter == ";" else "."


def validate(db: Session, company_id: int, pf: ParsedFile, opts: ImportOptions) -> ValidationResult:
    mapping = {k: v for k, v in opts.mapping.items() if k in FIELD_BY_KEY and v}
    issues: list[Issue] = []
    unknown = [c for c in mapping.values() if c not in pf.columns]
    if unknown:
        raise ImportFileError(f"Mapped column(s) not found in file: {', '.join(unknown)}")
    missing_required = [f.label for f in FIELDS if f.required and f.key not in mapping]
    if missing_required:
        raise ImportFileError(f"Required field(s) not mapped: {', '.join(missing_required)}")
    if len(set(mapping.values())) != len(mapping):
        raise ImportFileError("Each source column can only be mapped to one field")

    dec = _decimal_for(opts, pf)
    ict_factor = 60.0 if opts.ideal_cycle_time_unit == "minutes" else 1.0
    missing_by_field = {k: 0 for k in mapping}
    parsed_rows: list[dict] = []
    err_rows: set[int] = set()
    warn_rows: set[int] = set()

    def add(row: int, fkey: str | None, value: Any, severity: str, code: str, msg: str) -> None:
        issues.append(Issue(row, fkey, mapping.get(fkey) if fkey else None, _display(value), severity, code, msg))
        (err_rows if severity == "error" else warn_rows).add(row)

    records = pf.frame.to_dict(orient="records")
    for idx, raw in enumerate(records):
        rown = idx + opts.header_row + 1  # spreadsheet row number
        p: dict[str, Any] = {"_row": rown}
        ok = True
        for key, col in mapping.items():
            spec = FIELD_BY_KEY[key]
            v = raw.get(col)
            if _is_empty(v):
                missing_by_field[key] += 1
                p[key] = None
                if spec.required:
                    add(rown, key, v, "error", "missing_required", f"Missing value for required field '{spec.label}'")
                    ok = False
                continue
            try:
                if spec.type == "date":
                    p[key] = parse_date(v)
                elif spec.type == "text":
                    s = str(v).strip()
                    if isinstance(v, float) and v.is_integer():
                        s = str(int(v))
                    limit = MAX_CODE_LEN if key in ("line", "machine", "shift", "product") else MAX_TEXT_LEN
                    if len(s) > limit:
                        add(rown, key, v, "error", "text_too_long", f"'{spec.label}' longer than {limit} characters")
                        ok = False
                    p[key] = s
                else:
                    num = parse_number(v, dec)
                    if num < 0:
                        add(rown, key, v, "error", "negative_value", f"'{spec.label}' must not be negative")
                        ok = False
                    if spec.type == "int":
                        if not float(num).is_integer():
                            add(rown, key, v, "error", "invalid_integer", f"'{spec.label}' must be a whole number")
                            ok = False
                        p[key] = int(round(num))
                    else:
                        p[key] = float(num)
            except (ValueError, OverflowError):
                code = "invalid_date" if spec.type == "date" else "invalid_number"
                add(rown, key, v, "error", code, f"'{spec.label}' could not be read as a {'date' if spec.type == 'date' else 'number'}")
                p[key] = None
                ok = False
        for k in FIELD_BY_KEY:
            p.setdefault(k, None)
        if ok:
            ok = _check_row(p, rown, add, ict_factor)
        p["_ok"] = ok
        parsed_rows.append(p)

    # ---- in-file duplicates & master data consistency
    seen: dict[tuple, int] = {}
    machine_line: dict[str, tuple[str, int]] = {}
    for p in parsed_rows:
        if not p["_ok"]:
            continue
        key = (p["date"], p["machine"], p["shift"], p["product"])
        if key in seen:
            add(p["_row"], "machine", p["machine"], "error", "duplicate_in_file",
                f"Duplicate of row {seen[key]} (same date, machine, shift and product)")
            p["_ok"] = False
            continue
        seen[key] = p["_row"]
        prev = machine_line.get(p["machine"])
        if prev and prev[0] != p["line"]:
            add(p["_row"], "line", p["line"], "error", "machine_line_conflict",
                f"Machine {p['machine']} is assigned to line {prev[0]} in row {prev[1]}")
            p["_ok"] = False
        else:
            machine_line.setdefault(p["machine"], (p["line"], p["_row"]))

    # ---- master data vs database
    lines = {x.code: x for x in db.scalars(select(ProductionLine).where(ProductionLine.company_id == company_id))}
    machines = {x.code: x for x in db.scalars(select(Machine).where(Machine.company_id == company_id))}
    shifts = {x.code: x for x in db.scalars(select(Shift).where(Shift.company_id == company_id))}
    products = {x.code: x for x in db.scalars(select(Product).where(Product.company_id == company_id))}
    line_by_id = {x.id: x.code for x in lines.values()}
    for p in parsed_rows:
        if not p["_ok"]:
            continue
        m = machines.get(p["machine"])
        if m is not None and line_by_id.get(m.line_id) != p["line"]:
            add(p["_row"], "line", p["line"], "error", "machine_line_conflict",
                f"Machine {p['machine']} already belongs to line {line_by_id.get(m.line_id)} in the database")
            p["_ok"] = False

    # ---- duplicates vs database
    dup_existing: list[int] = []
    ok_rows = [p for p in parsed_rows if p["_ok"]]
    if ok_rows:
        dates = [p["date"] for p in ok_rows]
        existing = set()
        q = (
            select(ProductionRecord.date, Machine.code, Shift.code, Product.code)
            .join(Machine, Machine.id == ProductionRecord.machine_id)
            .join(Shift, Shift.id == ProductionRecord.shift_id)
            .join(Product, Product.id == ProductionRecord.product_id)
            .where(ProductionRecord.company_id == company_id,
                   ProductionRecord.date >= min(dates), ProductionRecord.date <= max(dates),
                   Machine.code.in_({p["machine"] for p in ok_rows}))
        )
        for row in db.execute(q):
            existing.add(tuple(row))
        for p in ok_rows:
            if (p["date"], p["machine"], p["shift"], p["product"]) in existing:
                dup_existing.append(p["_row"])
                p["_ok"] = False
                sev = "warning" if opts.skip_existing_duplicates else "error"
                add(p["_row"], "date", p["date"], sev, "duplicate_existing",
                    "A record for this date, machine, shift and product already exists"
                    + (" – it will be skipped" if opts.skip_existing_duplicates else ""))

    valid = [p for p in parsed_rows if p["_ok"]]
    new_md = {
        "lines": sorted({p["line"] for p in valid} - set(lines)),
        "machines": sorted({p["machine"] for p in valid} - set(machines)),
        "shifts": sorted({p["shift"] for p in valid} - set(shifts)),
        "products": sorted({p["product"] for p in valid} - set(products)),
    }
    all_dates = [p["date"] for p in parsed_rows if p.get("date")]
    issues.sort(key=lambda i: (i.row, i.severity != "error"))
    return ValidationResult(
        total_rows=len(parsed_rows),
        rows=valid,
        issues=issues,
        error_row_numbers=err_rows,
        warning_row_numbers=warn_rows - err_rows,
        duplicate_existing_rows=dup_existing,
        missing_by_field=missing_by_field,
        new_master_data=new_md,
        date_range=(min(all_dates) if all_dates else None, max(all_dates) if all_dates else None),
    )


def _check_row(p: dict, rown: int, add, ict_factor: float) -> bool:
    """Cross-field consistency checks. Mutates ``p`` for documented derivations."""
    ok = True
    actual, good, bad = p["actual_qty"], p["good_qty"], p["defective_qty"]
    if actual is not None:
        if good is not None and bad is not None:
            if good + bad != actual:
                add(rown, "good_qty", good, "error", "quantity_mismatch",
                    f"Good ({good}) + defective ({bad}) = {good + bad} does not equal actual quantity ({actual})")
                ok = False
        elif good is not None:
            if good > actual:
                add(rown, "good_qty", good, "error", "good_exceeds_actual", "Good quantity exceeds actual quantity")
                ok = False
            else:
                p["defective_qty"] = actual - good
                add(rown, "defective_qty", None, "warning", "derived_value",
                    "Defective quantity derived as actual − good")
        elif bad is not None:
            if bad > actual:
                add(rown, "defective_qty", bad, "error", "good_exceeds_actual",
                    "Defective quantity exceeds actual quantity")
                ok = False
            else:
                p["good_qty"] = actual - bad
                add(rown, "good_qty", None, "warning", "derived_value", "Good quantity derived as actual − defective")

    for k in ("scheduled_minutes", "planned_downtime_minutes", "planned_production_minutes", "operating_minutes",
              "downtime_minutes"):
        if p[k] is not None and p[k] > 1440:
            add(rown, k, p[k], "error", "minutes_exceed_day",
                f"'{FIELD_BY_KEY[k].label}' exceeds 1440 minutes (24 h) for a single record")
            ok = False
    if not ok:
        return False

    sched, pdt, ppt = p["scheduled_minutes"], p["planned_downtime_minutes"], p["planned_production_minutes"]
    if sched is not None and pdt is not None:
        if pdt > sched + TIME_TOLERANCE_MIN:
            add(rown, "planned_downtime_minutes", pdt, "error", "planned_downtime_exceeds_scheduled",
                "Planned downtime exceeds scheduled time")
            return False
        if ppt is None:
            p["planned_production_minutes"] = ppt = sched - pdt
            add(rown, "planned_production_minutes", None, "warning", "derived_value",
                "Planned production minutes derived as scheduled − planned downtime")
        elif abs(sched - pdt - ppt) > TIME_TOLERANCE_MIN:
            add(rown, "planned_production_minutes", ppt, "error", "time_inconsistent",
                f"Scheduled ({sched:g}) − planned downtime ({pdt:g}) ≠ planned production minutes ({ppt:g})")
            return False

    op, udt = p["operating_minutes"], p["downtime_minutes"]
    if ppt is not None and op is not None and udt is not None:
        if abs(ppt - udt - op) > TIME_TOLERANCE_MIN:
            add(rown, "operating_minutes", op, "error", "time_inconsistent",
                f"Planned production ({ppt:g}) − downtime ({udt:g}) ≠ operating minutes ({op:g})")
            return False
    elif ppt is not None and udt is not None and op is None:
        if udt > ppt + TIME_TOLERANCE_MIN:
            add(rown, "downtime_minutes", udt, "error", "time_inconsistent",
                "Downtime exceeds planned production minutes")
            return False
        p["operating_minutes"] = op = ppt - udt
        add(rown, "operating_minutes", None, "warning", "derived_value",
            "Operating minutes derived as planned production − downtime")
    elif ppt is not None and op is not None and udt is None:
        if op > ppt + TIME_TOLERANCE_MIN:
            add(rown, "operating_minutes", op, "error", "operating_exceeds_planned",
                "Operating minutes exceed planned production minutes")
            return False
        p["downtime_minutes"] = udt = max(ppt - op, 0.0)
        add(rown, "downtime_minutes", None, "warning", "derived_value",
            "Downtime minutes derived as planned production − operating")
    if ppt is not None and op is not None and op > ppt + TIME_TOLERANCE_MIN:
        add(rown, "operating_minutes", op, "error", "operating_exceeds_planned",
            "Operating minutes exceed planned production minutes")
        return False

    ict = p["ideal_cycle_time"]
    if ict is not None:
        if ict <= 0:
            add(rown, "ideal_cycle_time", ict, "error", "nonpositive_cycle_time", "Ideal cycle time must be > 0")
            return False
        p["ideal_cycle_time_sec"] = ict * ict_factor
        if op and actual is not None:
            perf = (p["ideal_cycle_time_sec"] / 60.0) * actual / op
            if perf > 1.05:
                add(rown, "ideal_cycle_time", ict, "warning", "performance_implausible",
                    f"Implied performance is {perf:.0%} (>100 %) – check the ideal cycle time and its unit")
    else:
        p["ideal_cycle_time_sec"] = None

    if actual and op == 0:
        add(rown, "operating_minutes", op, "warning", "output_without_runtime",
            "Units were produced but operating minutes are 0")
    if p["downtime_reason"] and not p["downtime_minutes"]:
        add(rown, "downtime_reason", p["downtime_reason"], "warning", "reason_without_downtime",
            "Downtime reason given without downtime minutes – the reason is ignored")
    if p["downtime_minutes"] and not p["downtime_reason"]:
        add(rown, "downtime_reason", None, "warning", "downtime_without_reason",
            "Downtime without reason – recorded as 'Unspecified'")
    return True


# ----------------------------------------------------------------------------- commit

def commit(db: Session, company_id: int, job: ImportJob, result: ValidationResult) -> dict:
    """Insert all valid rows. Caller owns the transaction (commit/rollback)."""
    lines = {x.code: x for x in db.scalars(select(ProductionLine).where(ProductionLine.company_id == company_id))}
    machines = {x.code: x for x in db.scalars(select(Machine).where(Machine.company_id == company_id))}
    shifts = {x.code: x for x in db.scalars(select(Shift).where(Shift.company_id == company_id))}
    products = {x.code: x for x in db.scalars(select(Product).where(Product.company_id == company_id))}
    created = {"lines": [], "machines": [], "shifts": [], "products": []}

    for p in result.rows:
        if p["line"] not in lines:
            lines[p["line"]] = ProductionLine(company_id=company_id, code=p["line"], name=p["line"])
            db.add(lines[p["line"]])
            created["lines"].append(p["line"])
        if p["shift"] not in shifts:
            shifts[p["shift"]] = Shift(company_id=company_id, code=p["shift"], name=p["shift"], sort_order=len(shifts))
            db.add(shifts[p["shift"]])
            created["shifts"].append(p["shift"])
        if p["product"] not in products:
            products[p["product"]] = Product(company_id=company_id, code=p["product"], name=p["product"])
            db.add(products[p["product"]])
            created["products"].append(p["product"])
    db.flush()
    for p in result.rows:
        if p["machine"] not in machines:
            machines[p["machine"]] = Machine(company_id=company_id, code=p["machine"], name=p["machine"],
                                             line_id=lines[p["line"]].id)
            db.add(machines[p["machine"]])
            created["machines"].append(p["machine"])
    db.flush()

    n = 0
    for p in result.rows:
        rec = ProductionRecord(
            company_id=company_id, date=p["date"], line_id=lines[p["line"]].id,
            machine_id=machines[p["machine"]].id, shift_id=shifts[p["shift"]].id,
            product_id=products[p["product"]].id, planned_qty=p["planned_qty"], actual_qty=p["actual_qty"],
            good_qty=p["good_qty"], defective_qty=p["defective_qty"], scheduled_minutes=p["scheduled_minutes"],
            planned_downtime_minutes=p["planned_downtime_minutes"],
            planned_production_minutes=p["planned_production_minutes"],
            unplanned_downtime_minutes=p["downtime_minutes"], operating_minutes=p["operating_minutes"],
            ideal_cycle_time_sec=p["ideal_cycle_time_sec"], import_job_id=job.id, is_synthetic=False,
        )
        if p["downtime_minutes"]:
            rec.downtime_events.append(DowntimeEvent(
                company_id=company_id, reason=p["downtime_reason"] or "Unspecified", is_planned=False,
                minutes=p["downtime_minutes"]))
        if p["planned_downtime_minutes"]:
            rec.downtime_events.append(DowntimeEvent(
                company_id=company_id, reason="Planned downtime", is_planned=True,
                minutes=p["planned_downtime_minutes"]))
        if p["defective_qty"]:
            rec.defect_events.append(DefectEvent(
                company_id=company_id, category=p["defect_category"] or "Unclassified", quantity=p["defective_qty"]))
        db.add(rec)
        n += 1
    db.flush()
    return {"imported_rows": n, "created_master_data": created}


def field_catalogue() -> list[dict]:
    return [{"key": f.key, "label": f.label, "type": f.type, "required": f.required, "description": f.description}
            for f in FIELDS]


__all__ = ["FIELDS", "ImportOptions", "ImportFileError", "read_table", "suggest_mapping", "validate", "commit",
           "preview_rows", "field_catalogue"]
