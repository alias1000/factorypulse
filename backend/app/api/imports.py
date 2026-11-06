"""Data import endpoints (upload → preview → validate → commit / revert)."""
from __future__ import annotations

import hashlib
import io
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from .deps import Principal, require
from ..models import DefectEvent, DowntimeEvent, ImportJob, ProductionRecord, utcnow
from ..services import importer as imp
from ..services.data_access import get_company
from ..services.sanitize import sanitize_row
from ..services.settings_store import audit

router = APIRouter(prefix="/api/imports", tags=["import"])
ALLOWED = {".csv": "csv", ".xlsx": "xlsx"}


class CommitRequest(BaseModel):
    options: imp.ImportOptions
    import_valid_rows_only: bool = False


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _job_or_404(db: Session, company_id: int, job_id: int) -> ImportJob:
    job = db.get(ImportJob, job_id)
    if job is None or job.company_id != company_id:
        raise HTTPException(404, "Import job not found")
    return job


def _path(job: ImportJob) -> Path:
    p = get_settings().upload_dir / job.stored_filename
    if not p.exists():
        raise HTTPException(410, "The uploaded file is no longer available; please upload it again")
    return p


def _previous_commit(db: Session, company_id: int, sha: str, exclude_id: int | None = None) -> ImportJob | None:
    q = select(ImportJob).where(ImportJob.company_id == company_id, ImportJob.file_sha256 == sha,
                                ImportJob.status == "committed")
    if exclude_id:
        q = q.where(ImportJob.id != exclude_id)
    return db.scalar(q.limit(1))


def _job_dict(job: ImportJob, include_summary: bool = False) -> dict:
    d = {
        "id": job.id, "original_filename": job.original_filename, "file_type": job.file_type,
        "file_size": job.file_size, "status": job.status, "sheet_name": job.sheet_name,
        "total_rows": job.total_rows, "valid_rows": job.valid_rows, "error_rows": job.error_rows,
        "imported_rows": job.imported_rows, "skipped_rows": job.skipped_rows,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "committed_at": job.committed_at.isoformat() if job.committed_at else None,
    }
    if include_summary:
        d["summary"] = job.summary
        d["mapping"] = job.mapping
    return d


@router.get("/fields", summary="Importable target fields")
def fields(_p: Principal = Depends(require("import.read"))):
    return imp.field_catalogue()


@router.get("/template", summary="Download an import template (CSV or XLSX)")
def template(format: str = Query("xlsx", pattern="^(csv|xlsx)$"), _p: Principal = Depends(require("import.read"))):
    header = [f.label for f in imp.FIELDS]
    example = [
        ["2026-10-01", "L1", "M-01", "F", "P-100", 420, 400, 394, 6, 480, 30, 450, 420, 30, "Tool change",
         "Burr", 48],
        ["2026-10-01", "L1", "M-01", "S", "P-100", 420, 380, 375, 5, 480, 30, 450, 405, 45, "Mechanical failure",
         "Dimensional deviation", 48],
    ]
    if format == "csv":
        buf = io.StringIO()
        import csv
        w = csv.writer(buf, delimiter=";")
        w.writerow(header)
        for r in example:
            w.writerow(sanitize_row(r))
        data = buf.getvalue().encode("utf-8-sig")
        return StreamingResponse(io.BytesIO(data), media_type="text/csv",
                                 headers={"Content-Disposition": 'attachment; filename="factorypulse_import_template.csv"'})
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "Production"
    ws.append(header)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="0F2744")
    for r in example:
        ws.append(sanitize_row(r))
    for i, f in enumerate(imp.FIELDS, start=1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = max(14, len(f.label) + 2)
    notes = wb.create_sheet("Field reference")
    notes.append(["Field", "Required", "Type", "Description"])
    for f in imp.FIELDS:
        notes.append([f.label, "yes" if f.required else "no", f.type, f.description])
    notes.append([])
    notes.append(["Times are minutes per record. Ideal cycle time in seconds per unit (configurable on import)."])
    notes.append(["Planned production minutes − downtime minutes must equal operating minutes."])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": 'attachment; filename="factorypulse_import_template.xlsx"'})


@router.post("/upload", summary="Upload a CSV/XLSX file and receive a preview + suggested mapping")
async def upload(request: Request, file: UploadFile = File(...), sheet: str | None = None,
                 header_row: int = Query(1, ge=1, le=50), _p: Principal = Depends(require("import.write")),
                 db: Session = Depends(get_db)):
    s = get_settings()
    company = get_company(db)
    name = Path(file.filename or "upload").name
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(415, "Only .csv and .xlsx files are supported")
    data = bytearray()
    while chunk := await file.read(1024 * 1024):
        data.extend(chunk)
        if len(data) > s.max_upload_bytes:
            raise HTTPException(413, f"File exceeds the {s.max_upload_mb} MB upload limit")
    if not data:
        raise HTTPException(400, "The file is empty")
    ftype = ALLOWED[ext]
    if ftype == "xlsx" and not bytes(data[:4]) == b"PK\x03\x04":
        raise HTTPException(415, "The file is not a valid .xlsx workbook")
    if ftype == "csv" and b"\x00" in data[:8192]:
        raise HTTPException(415, "The file does not look like a text CSV file")

    sha = hashlib.sha256(data).hexdigest()
    stored = f"{uuid.uuid4().hex}.{ftype}"
    path = s.upload_dir / stored
    path.write_bytes(bytes(data))
    try:
        pf = imp.read_table(path, ftype, sheet, header_row, s.max_import_rows)
    except imp.ImportFileError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(422, str(exc)) from exc

    safe_name = re.sub(r"[^\w.\- ()äöüÄÖÜß]", "_", name)[:255]
    job = ImportJob(company_id=company.id, original_filename=safe_name, stored_filename=stored, file_sha256=sha,
                    file_size=len(data), file_type=ftype, sheet_name=pf.sheet, status="uploaded",
                    total_rows=len(pf.frame))
    db.add(job)
    db.flush()
    prev = _previous_commit(db, company.id, sha)
    audit(db, "import.uploaded", company.id, "import_job", job.id,
          {"filename": safe_name, "sha256": sha, "size": len(data), "rows": len(pf.frame)}, _ip(request))
    db.commit()
    return {
        "job": _job_dict(job),
        "columns": pf.columns,
        "preview": imp.preview_rows(pf),
        "row_count": len(pf.frame),
        "sheets": pf.sheets,
        "sheet": pf.sheet,
        "delimiter": pf.delimiter,
        "encoding": pf.encoding,
        "suggested_mapping": imp.suggest_mapping(pf.columns),
        "fields": imp.field_catalogue(),
        "already_imported": _job_dict(prev) if prev else None,
    }


def _run_validation(db: Session, company_id: int, job: ImportJob, opts: imp.ImportOptions):
    s = get_settings()
    try:
        pf = imp.read_table(_path(job), job.file_type, opts.sheet or job.sheet_name, opts.header_row, s.max_import_rows)
        return pf, imp.validate(db, company_id, pf, opts)
    except imp.ImportFileError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/{job_id}/validate", summary="Validate the file with a column mapping (no data is written)")
def validate(job_id: int, opts: imp.ImportOptions, request: Request, _p: Principal = Depends(require("import.write")),
             db: Session = Depends(get_db)):
    company = get_company(db)
    job = _job_or_404(db, company.id, job_id)
    if job.status in ("committed", "reverted"):
        raise HTTPException(409, f"Import job is already {job.status}")
    pf, result = _run_validation(db, company.id, job, opts)
    summary = result.summary()
    prev = _previous_commit(db, company.id, job.file_sha256, job.id)
    summary["already_imported"] = _job_dict(prev) if prev else None
    summary["can_commit"] = prev is None and summary["valid_rows"] > 0
    job.status = "validated"
    job.mapping = opts.model_dump()
    job.sheet_name = pf.sheet
    job.total_rows, job.valid_rows, job.error_rows = result.total_rows, len(result.rows), len(result.error_row_numbers)
    job.summary = summary
    audit(db, "import.validated", company.id, "import_job", job.id,
          {k: summary[k] for k in ("total_rows", "valid_rows", "error_rows", "duplicate_existing_rows")}, _ip(request))
    db.commit()
    return summary


@router.post("/{job_id}/commit", summary="Import valid rows in a single database transaction")
def commit(job_id: int, body: CommitRequest, request: Request, _p: Principal = Depends(require("import.write")),
           db: Session = Depends(get_db)):
    company = get_company(db)
    job = _job_or_404(db, company.id, job_id)
    if job.status in ("committed", "reverted"):
        raise HTTPException(409, f"Import job is already {job.status}")
    prev = _previous_commit(db, company.id, job.file_sha256, job.id)
    if prev:
        raise HTTPException(409, f"This exact file was already imported (job #{prev.id}). Duplicate import refused.")
    _pf, result = _run_validation(db, company.id, job, body.options)  # never trust client-side validation
    summary = result.summary()
    if result.error_row_numbers and not body.import_valid_rows_only:
        raise HTTPException(422, {"message": f"{len(result.error_row_numbers)} row(s) contain errors. Fix the file "
                                             "or explicitly choose to import only the valid rows.",
                                  "summary": summary})
    if not result.rows:
        raise HTTPException(422, {"message": "No valid rows to import.", "summary": summary})
    try:
        stats = imp.commit(db, company.id, job, result)
        job.status = "committed"
        job.committed_at = utcnow()
        job.mapping = body.options.model_dump()
        job.total_rows, job.valid_rows, job.error_rows = result.total_rows, len(result.rows), len(result.error_row_numbers)
        job.imported_rows = stats["imported_rows"]
        job.skipped_rows = result.total_rows - stats["imported_rows"]
        summary["import_result"] = stats
        job.summary = summary
        audit(db, "import.committed", company.id, "import_job", job.id,
              {"imported_rows": stats["imported_rows"], "skipped_rows": job.skipped_rows,
               "created_master_data": stats["created_master_data"]}, _ip(request))
        db.commit()
    except Exception:
        db.rollback()
        job = db.get(ImportJob, job_id)
        job.status = "failed"
        audit(db, "import.failed", company.id, "import_job", job.id, None, _ip(request))
        db.commit()
        raise
    return {"job": _job_dict(job), "imported_rows": stats["imported_rows"], "skipped_rows": job.skipped_rows,
            "created_master_data": stats["created_master_data"]}


class RevertRequest(BaseModel):
    confirm: str = Field(..., max_length=20, description="Must be 'REVERT'")


@router.post("/{job_id}/revert", summary="Delete all records created by a committed import (admin, confirm=REVERT)")
def revert(job_id: int, body: RevertRequest, request: Request, _p: Principal = Depends(require("import.revert")),
           db: Session = Depends(get_db)):
    if body.confirm != "REVERT":
        raise HTTPException(400, {"code": "confirmation_required", "message": "Type REVERT to confirm."})
    company = get_company(db)
    job = _job_or_404(db, company.id, job_id)
    if job.status != "committed":
        raise HTTPException(409, "Only committed imports can be reverted")
    ids = select(ProductionRecord.id).where(ProductionRecord.import_job_id == job.id)
    db.execute(delete(DowntimeEvent).where(DowntimeEvent.record_id.in_(ids)))
    db.execute(delete(DefectEvent).where(DefectEvent.record_id.in_(ids)))
    n = db.execute(delete(ProductionRecord).where(ProductionRecord.import_job_id == job.id)).rowcount
    job.status = "reverted"
    audit(db, "import.reverted", company.id, "import_job", job.id, {"deleted_records": n}, _ip(request))
    db.commit()
    return {"job": _job_dict(job), "deleted_records": n}


@router.get("", summary="Import history (audit trail)")
def list_jobs(limit: int = Query(50, ge=1, le=500), _p: Principal = Depends(require("import.read")),
              db: Session = Depends(get_db)):
    company = get_company(db)
    jobs = db.scalars(select(ImportJob).where(ImportJob.company_id == company.id).order_by(ImportJob.id.desc()).limit(limit))
    return [_job_dict(j) for j in jobs]


@router.get("/{job_id}")
def get_job(job_id: int, _p: Principal = Depends(require("import.read")), db: Session = Depends(get_db)):
    company = get_company(db)
    return _job_dict(_job_or_404(db, company.id, job_id), include_summary=True)
