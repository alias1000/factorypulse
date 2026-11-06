"""Manual report generation (PDF / Excel) and downloads.

Scheduled report generation is intentionally NOT implemented in the MVP.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from .deps import Principal, require
from ..models import ReportJob, utcnow
from ..services.data_access import Filters, get_company
from ..services.reports.builder import ReportError, build
from ..services.settings_store import audit
from .anomalies import FilterBody

router = APIRouter(prefix="/api/reports", tags=["reports"])
log = logging.getLogger("factorypulse.reports")
MEDIA = {"pdf": "application/pdf", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


class ReportRequest(BaseModel):
    report_type: Literal["daily", "weekly", "monthly", "quality", "downtime"]
    format: Literal["pdf", "xlsx"] = "pdf"
    start_date: dt.date
    end_date: dt.date | None = None
    filters: FilterBody = FilterBody()
    language: Literal["en", "de"] = "en"


def _dict(j: ReportJob) -> dict:
    return {"id": j.id, "report_type": j.report_type, "format": j.file_format,
            "period_start": j.period_start.isoformat(), "period_end": j.period_end.isoformat(),
            "filters": j.filters, "language": j.language, "status": j.status, "download_name": j.download_name,
            "file_size": j.file_size, "error": j.error,
            "created_at": j.created_at.isoformat() if j.created_at else None,
            "completed_at": j.completed_at.isoformat() if j.completed_at else None}


@router.post("", summary="Generate a report now")
def create_report(body: ReportRequest, request: Request, _p: Principal = Depends(require("report.generate")),
                  db: Session = Depends(get_db)):
    company = get_company(db)
    fb = body.filters.model_dump()
    fb["start_date"] = fb["end_date"] = None
    f = Filters.from_dict(fb)
    try:
        data = build(db, company, body.report_type, body.start_date, body.end_date, f, body.language)
    except ReportError as exc:
        raise HTTPException(422, str(exc)) from exc
    job = ReportJob(company_id=company.id, report_type=body.report_type, file_format=body.format,
                    period_start=data.period[0], period_end=data.period[1], filters=f.to_dict(),
                    language=body.language, status="pending")
    db.add(job)
    db.flush()
    try:
        if body.format == "pdf":
            from ..services.reports.pdf import render_pdf
            content = render_pdf(data)
        else:
            from ..services.reports.excel import render_xlsx
            content = render_xlsx(data)
    except Exception as exc:  # pragma: no cover - logged and reported to the user
        log.exception("Report generation failed")
        job.status, job.error = "failed", f"{exc.__class__.__name__}: report rendering failed"
        db.commit()
        raise HTTPException(500, "Report generation failed. See server logs.") from exc
    stored = f"{uuid.uuid4().hex}.{body.format}"
    (get_settings().report_dir / stored).write_bytes(content)
    slug = re.sub(r"[^A-Za-z0-9]+", "_", company.name).strip("_")[:40] or "report"
    job.stored_filename = stored
    job.download_name = f"{slug}_{body.report_type}_{data.period[0]:%Y%m%d}-{data.period[1]:%Y%m%d}.{body.format}"
    job.file_size = len(content)
    job.status = "completed"
    job.completed_at = utcnow()
    audit(db, "report.generated", company.id, "report_job", job.id,
          {"type": body.report_type, "format": body.format, "size": len(content)},
          request.client.host if request.client else None)
    db.commit()
    return _dict(job)


@router.get("", summary="Report history")
def list_reports(limit: int = Query(50, ge=1, le=500), _p: Principal = Depends(require("report.read")),
                 db: Session = Depends(get_db)):
    company = get_company(db)
    rows = db.scalars(select(ReportJob).where(ReportJob.company_id == company.id).order_by(ReportJob.id.desc()).limit(limit))
    return [_dict(j) for j in rows]


@router.get("/{report_id}/download", summary="Download a generated report")
def download(report_id: int, _p: Principal = Depends(require("report.read")), db: Session = Depends(get_db)):
    company = get_company(db)
    job = db.get(ReportJob, report_id)
    if job is None or job.company_id != company.id or job.status != "completed" or not job.stored_filename:
        raise HTTPException(404, "Report not found")
    path = get_settings().report_dir / job.stored_filename
    if not path.exists():
        raise HTTPException(410, "Report file no longer available")
    return FileResponse(path, media_type=MEDIA[job.file_format], filename=job.download_name)


@router.delete("/{report_id}", summary="Delete a generated report")
def delete_report(report_id: int, _p: Principal = Depends(require("report.delete")), db: Session = Depends(get_db)):
    company = get_company(db)
    job = db.get(ReportJob, report_id)
    if job is None or job.company_id != company.id:
        raise HTTPException(404, "Report not found")
    if job.stored_filename:
        (get_settings().report_dir / job.stored_filename).unlink(missing_ok=True)
    db.delete(job)
    db.commit()
    return {"status": "deleted"}
