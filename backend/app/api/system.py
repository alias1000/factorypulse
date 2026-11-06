"""Health / readiness checks and backup management."""
from __future__ import annotations

import io
import shutil
import tarfile
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .. import __version__
from ..config import get_settings
from ..db import engine, get_db
from ..models import User
from ..services import backup as B
from ..services.data_access import get_company
from ..services.settings_store import audit
from .admin import DestructiveConfirm, verify_reauth
from .deps import Principal, require

router = APIRouter(prefix="/api", tags=["system"])
backup_router = APIRouter(prefix="/api/admin/backups", tags=["backups"])


def run_checks(db: Session) -> list[dict]:
    """Deployment/readiness checks. status: ok | warn | fail (fail => not ready)."""
    s = get_settings()
    checks: list[dict] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    t = time.perf_counter()
    try:
        db.execute(text("SELECT 1"))
        add("database", "ok", f"{engine.dialect.name} reachable ({(time.perf_counter() - t) * 1000:.0f} ms)")
    except Exception as exc:  # pragma: no cover
        add("database", "fail", f"not reachable: {exc.__class__.__name__}")
        return checks

    rev, head = B.current_revision(engine), B.head_revision()
    add("migrations", "ok" if rev == head else "fail", f"schema {rev}, application expects {head}")

    for sub in ("uploads", "reports", "evidence", "evidence/quarantine", "backups"):
        p = s.data_dir / sub
        try:
            probe = p / f".probe-{uuid.uuid4().hex}"
            probe.write_bytes(b"x")
            probe.unlink()
            add(f"storage:{sub}", "ok", "writable")
        except OSError as exc:
            add(f"storage:{sub}", "fail", f"not writable: {exc.strerror}")
    free_gb = shutil.disk_usage(s.data_dir).free / 1e9
    add("disk_space", "ok" if free_gb >= 2 else "warn" if free_gb >= 0.5 else "fail", f"{free_gb:.1f} GB free")

    try:
        import weasyprint  # noqa: F401
        add("pdf_engine", "ok", "WeasyPrint available")
    except Exception as exc:  # pragma: no cover
        add("pdf_engine", "fail", f"WeasyPrint not usable: {exc.__class__.__name__}")

    if engine.dialect.name == "postgresql":
        srv, dump_v, rest_v = B.server_major(engine), B.tool_version("pg_dump"), B.tool_version("pg_restore")
        ok = dump_v is not None and rest_v is not None and dump_v >= srv and rest_v >= srv
        add("backup_tools", "ok" if ok else "warn",
            f"PostgreSQL {srv}; pg_dump {dump_v or 'missing'}, pg_restore {rest_v or 'missing'}")
        age = B.last_backup_age_hours()
        add("last_backup", "warn" if age is None or age > 24 * 7 else "ok",
            "no backup yet" if age is None else f"{age:.1f} h ago")
    else:
        add("backup_tools", "warn", f"backups need PostgreSQL (current: {engine.dialect.name})")

    # v0.3.1: the alert rules are evaluated by the separate worker process (python -m app.worker)
    from ..models import Company
    from ..services import alerts as AL

    for cid in db.scalars(select(Company.id).order_by(Company.id)).all():
        st = AL.status(db, cid)
        detail = {"ok": f"last run {st['evaluated_at']} (every {st['interval_seconds']} s)",
                  "never": "no alert evaluation yet – is the worker service running?",
                  "stale": f"last successful run {st['evaluated_at']} is older than 3 intervals – check the worker",
                  "failed": f"last run failed: {(st['last_error'] or '')[:150]}"}[st["state"]]
        add("alert_worker", "ok" if st["state"] == "ok" else "warn", detail)
    _evidence_checks(db, add)

    admins = db.scalar(select(User.id).where(User.role == "admin", User.is_active.is_(True)).limit(1))
    add("administrator", "ok" if admins else "warn", "active administrator exists" if admins else "no administrator account")

    if not s.is_dev:
        add("secure_cookies", "ok" if s.secure_cookies else "warn",
            "Secure cookie flag on" if s.secure_cookies else "FP_COOKIE_SECURE=false – only acceptable without TLS in a lab")
        add("demo_tools", "warn" if s.demo_tools_enabled else "ok",
            "demo/data-reset tools ENABLED in production" if s.demo_tools_enabled else "disabled")
    return checks


def _evidence_checks(db: Session, add) -> None:
    """v0.3.3: scanner availability comes from the persisted explicit health check (never from whether the
    last scan run happened to have work); quarantine and file-storage consistency are reported separately."""
    from ..services import evidence_scan as ES
    from ..services import evidence_store as EStore

    s = get_settings()
    if s.evidence_scanner == "none":
        add("evidence_scanner", "warn",
            "no malware scanner: uploaded evidence stays quarantined until an administrator releases it")
    else:
        h = ES.health(db)
        if h is None or (h.checked_at is None and h.status != "unavailable"):
            add("evidence_scanner", "warn", f"{ES.scanner_label()}: no health check yet – is the worker service running?")
        elif h.status == "unavailable":
            add("evidence_scanner", "warn",
                f"{h.scanner} UNAVAILABLE since {h.unavailable_since.isoformat() if h.unavailable_since else '?'} "
                f"({h.consecutive_failures} consecutive failure(s)): {(h.last_error or '')[:200]} – new evidence stays "
                "quarantined; the warning clears after a successful scanner check")
        elif ES.health_is_stale(h):
            add("evidence_scanner", "warn",
                f"{h.scanner}: last health check {h.checked_at.isoformat()} is older than 3 intervals – check the worker")
        else:
            add("evidence_scanner", "ok", f"{h.scanner} healthy (checked {h.checked_at.isoformat()}; {(h.detail or '')[:120]})")

    q = ES.quarantine_counts(db)
    detail = (f"pending {q['pending']}, retrying {q['retrying']}, retries exhausted {q['retries_exhausted']} "
              f"(max {q['max_attempts']} attempts), not scanned {q['not_scanned']}")
    waiting = q["pending"] + q["retrying"]
    status = "warn" if q["retries_exhausted"] or (waiting and ES.scanner_unavailable(db)) else "ok"
    if q["retries_exhausted"]:
        detail += " – files with exhausted retries stay quarantined: rescan after fixing the scanner, or review and release"
    add("evidence_quarantine", status, detail)
    if q["infected"]:
        add("evidence_infected", "warn", f"{q['infected']} evidence file(s) flagged as infected (quarantined)")

    st = EStore.storage_status(db)
    # pending operations are normally executed right after the commit; orphaned files are unreachable for
    # every user (informational) – failed operations, misplaced or missing files need attention
    problems = {k: v for k, v in st.items() if v and k not in ("ops_pending", "orphaned")}
    add("evidence_storage", "warn" if problems else "ok",
        ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in st.items()) +
        (" – run the evidence reconciliation (Admin → System)" if problems else ""))


@router.get("/health", tags=["system"], summary="Liveness (public)")
def health():
    return {"status": "ok", "version": __version__}


@router.get("/health/ready", tags=["system"], summary="Readiness (public, no details) – 503 when not ready")
def ready(db: Session = Depends(get_db)):
    checks = run_checks(db)
    ok = all(c["status"] != "fail" for c in checks)
    return JSONResponse(status_code=200 if ok else 503, content={
        "status": "ready" if ok else "not_ready", "version": __version__,
        "checks": {c["name"]: c["status"] for c in checks}})


@router.get("/admin/system", summary="Detailed system status (admin)")
def system_status(_p: Principal = Depends(require("system.read")), db: Session = Depends(get_db)):
    s = get_settings()
    checks = run_checks(db)
    return {"status": "ready" if all(c["status"] != "fail" for c in checks) else "not_ready",
            "version": __version__, "build": s.build_sha, "environment": s.environment, "checks": checks}


# ----------------------------------------------------------------------------- evidence (v0.3.3)

def _evidence_status(db: Session, company_id: int) -> dict:
    from ..services import evidence_scan as ES
    from ..services import evidence_store as EStore
    from ..services import jobs as J

    return {"scanner": get_settings().evidence_scanner, "scanner_label": ES.scanner_label(),
            "health": ES.health_dict(ES.health(db)), "health_stale": ES.health_is_stale(ES.health(db)),
            "quarantine": ES.quarantine_counts(db, company_id), "storage": EStore.storage_status(db),
            "last_health_check": J.run_dict(J.latest(db, ES.HEALTH_JOB, None)),
            "last_scan": J.run_dict(J.latest(db, ES.JOB, company_id)),
            "last_reconcile": J.run_dict(J.latest(db, EStore.JOB, None))}


@router.get("/admin/evidence/status", summary="Evidence scanner health, quarantine and storage consistency (admin)")
def evidence_status(p: Principal = Depends(require("system.read")), db: Session = Depends(get_db)):
    return _evidence_status(db, p.company_id)


@router.post("/admin/evidence/scanner/check", summary="Run the explicit scanner health check now (admin)")
def evidence_scanner_check(p: Principal = Depends(require("action.manage")), db: Session = Depends(get_db)):
    from ..services import evidence_scan as ES
    from ..services import evidence_store as EStore

    if ES.scanner_name() == "none":
        raise HTTPException(409, {"code": "no_scanner", "message": "No malware scanner is configured (FP_EVIDENCE_SCANNER)."})
    trigger = f"manual:{p.user.email}"
    run = ES.run_health_job(trigger=trigger, max_attempts=1)[0]
    scanned = None
    db.expire_all()
    if run.status == "succeeded" and not ES.scanner_unavailable(db):
        # verified recovery: work through the backlog right away (the worker would do it on its next cycle)
        scanned = _run_dict(ES.run_scan_job(p.company_id, trigger=trigger, max_attempts=1)[0])
        EStore.run_reconcile_job(None, trigger=trigger, max_attempts=1)
    audit(db, "evidence.scanner_checked", p.company_id, details={"health_run": run.details, "scan_run": scanned})
    db.commit()
    db.expire_all()
    return {"run": _run_dict(run), "scan_run": scanned, **_evidence_status(db, p.company_id)}


@router.post("/admin/evidence/reconcile", summary="Reconcile evidence files with the database now (admin)")
def evidence_reconcile(p: Principal = Depends(require("action.manage")), db: Session = Depends(get_db)):
    from ..services import evidence_store as EStore

    run = EStore.run_reconcile_job(None, trigger=f"manual:{p.user.email}", max_attempts=1)[0]
    audit(db, "evidence.reconciled", p.company_id, details={"status": run.status, "result": run.details})
    db.commit()
    db.expire_all()
    return {"run": _run_dict(run), **_evidence_status(db, p.company_id)}


def _run_dict(run):
    from ..services import jobs as J

    return J.run_dict(run)


# ----------------------------------------------------------------------------- backups

class BackupCreate(BaseModel):
    note: str | None = Field(None, max_length=200)


def _err(exc: B.BackupError) -> HTTPException:
    return HTTPException(409 if "in progress" in str(exc) else 400, {"code": "backup_error", "message": str(exc)})


@backup_router.get("", summary="List backups")
def list_backups(_p: Principal = Depends(require("backup.manage"))):
    return B.list_backups()


@backup_router.post("", summary="Create a backup now (database + uploaded files + reports)")
def create_backup(body: BackupCreate, p: Principal = Depends(require("backup.manage")), db: Session = Depends(get_db)):
    try:
        man = B.create_backup(engine, p.user.email, "manual", body.note)
    except B.BackupError as exc:
        raise _err(exc) from exc
    audit(db, "backup.created", p.company_id, details={"name": man["name"], "db_size": man["database"]["size"]})
    db.commit()
    return man


@backup_router.get("/{name}/download", summary="Download a backup as .tar")
def download_backup(name: str, p: Principal = Depends(require("backup.manage")), db: Session = Depends(get_db)):
    try:
        d = B.get_backup_dir(name)
    except B.BackupError as exc:
        raise HTTPException(404, str(exc)) from exc
    audit(db, "backup.downloaded", p.company_id, details={"name": name})
    db.commit()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for f in sorted(d.iterdir()):
            tar.add(f, arcname=f"{name}/{f.name}")
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/x-tar",
                             headers={"Content-Disposition": f'attachment; filename="{name}.tar"'})


@backup_router.post("/{name}/restore", summary="Restore a backup (admin, password + RESTORE). Logs out all users.")
def restore_backup(name: str, body: DestructiveConfirm, p: Principal = Depends(require("backup.manage")),
                   db: Session = Depends(get_db)):
    verify_reauth(db, p, body, "RESTORE", "backup.restore")
    email, company_id = p.user.email, p.company_id
    db.commit()
    db.close()  # release our connection before the restore drops and recreates tables
    try:
        result = B.restore_backup(engine, name, email)
    except B.BackupError as exc:
        raise _err(exc) from exc
    from ..db import SessionLocal
    from ..services.auth import ctx
    with SessionLocal() as s2:
        # the acting user may not exist in the restored users table -> keep only the e-mail
        if ctx().get("user_id") and s2.get(User, ctx()["user_id"]) is None:
            ctx()["user_id"] = None  # the audit table now comes from the backup: append the restore event
        company = get_company(s2)
        audit(s2, "backup.restored", company.id if company else company_id, details={**result, "by": email})
        s2.commit()
    return result


@backup_router.post("/{name}/delete", summary="Delete a backup (admin, password + DELETE)")
def delete_backup(name: str, body: DestructiveConfirm, p: Principal = Depends(require("backup.manage")),
                  db: Session = Depends(get_db)):
    verify_reauth(db, p, body, "DELETE", "backup.delete")
    try:
        B.delete_backup(name)
    except B.BackupError as exc:
        raise HTTPException(404, str(exc)) from exc
    audit(db, "backup.deleted", p.company_id, details={"name": name})
    db.commit()
    return {"status": "deleted"}
