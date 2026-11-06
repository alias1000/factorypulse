"""FactoryPulse AI – FastAPI application entry point."""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__
from .api import action_center, admin, analytics, anomalies, auth, exports, imports, reports, system
from .config import get_settings
from .db import SessionLocal
from .services.auth import request_ctx

settings = get_settings()
logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("factorypulse")


def seed_if_empty(session_factory=None) -> bool:
    """Load the synthetic demo dataset into an EMPTY database on its very first start.

    Returns True when demo data was loaded. Callers must check ``settings.demo_seeding_enabled``
    first; this function itself never runs for a database that already has data or was initialised.
    """
    from .services.data_access import get_company
    from .services.demo_data import company_has_data, load_demo
    from .services.settings_store import audit, get_setting, set_setting

    with (session_factory or SessionLocal)() as db:
        company = get_company(db)
        # Seed only on the very first start; never re-seed after the user deleted data.
        if get_setting(db, company.id, "initialized") is None and not company_has_data(db, company.id):
            stats = load_demo(db, company)
            audit(db, "demo.loaded", company.id, "company", company.id, {**stats, "trigger": "startup"})
            set_setting(db, company.id, "initialized", True)
            db.commit()
            log.info("Synthetic demo dataset loaded: %s", stats)
            company_id = company.id
        else:
            return False
    from .services.alerts import run_alert_evaluation

    run_alert_evaluation(company_id, trigger="demo.seed", max_attempts=1, session_factory=session_factory)
    return True


def bootstrap_admin() -> None:
    from sqlalchemy import select

    from .api.auth import ensure_bootstrap_admin
    from .models import User

    with SessionLocal() as db:
        ensure_bootstrap_admin(db)
        from .services.demo_action_center import DEMO_DOMAIN
        if db.scalar(select(User.id).where(~User.email.like(f"%@{DEMO_DOMAIN}")).limit(1)) is None:
            log.warning("No user account exists. Create an administrator with: "
                        "python -m app.cli create-user --email you@example.com --role admin "
                        "(or set FP_INITIAL_ADMIN_EMAIL / FP_INITIAL_ADMIN_PASSWORD).")


def startup_seeding(s=None, session_factory=None) -> bool:
    """Decide about demo seeding at startup. Returns True when synthetic data was loaded."""
    s = s or settings
    if not s.demo_seeding_enabled:
        log.info("Synthetic demo seeding is disabled (FP_ENVIRONMENT=%s).", s.environment)
        return False
    if s.environment == "production":
        log.warning("FP_SEED_DEMO_ON_STARTUP=true in production: an EMPTY database will receive the "
                    "SYNTHETIC demo dataset. Remove this setting for customer installations.")
    try:
        return seed_if_empty(session_factory)
    except Exception:  # pragma: no cover - startup must not crash on seeding problems
        log.exception("Demo seeding failed")
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    request_ctx.set({"request_id": "startup"})
    startup_seeding()
    try:
        bootstrap_admin()
    except Exception:  # pragma: no cover
        log.exception("Administrator bootstrap failed")
    yield


app = FastAPI(
    title="FactoryPulse AI API",
    version=__version__,
    description=(
        "Manufacturing analytics API: data import, KPI engine, quality & downtime analysis, "
        "statistical anomaly detection and report generation. All KPI ratios are returned as decimal "
        "fractions (0.85 = 85 %); `null` means *not computable from the available data* (shown as N/A).\n\n"
        "**Authentication:** session cookie from `POST /api/auth/login`; state-changing requests must send the "
        "`X-CSRF-Token` header returned by `/api/auth/me`. Roles: viewer < analyst < admin."
    ),
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID"],
)

access_log = logging.getLogger("factorypulse.access")


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("X-Request-ID", "")[:64] or uuid.uuid4().hex[:16]
    ctx = {"request_id": rid, "ip": request.client.host if request.client else None}
    token = request_ctx.set(ctx)
    start = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        request_ctx.reset(token)
    ms = (time.perf_counter() - start) * 1000
    response.headers["X-Request-ID"] = rid
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.url.path.startswith("/api/") and request.url.path not in ("/api/docs", "/api/openapi.json"):
        response.headers.setdefault("Cache-Control", "no-store")
    if request.url.path != "/api/health":
        access_log.info("%s %s %s %.0fms user=%s rid=%s", request.method, request.url.path, response.status_code, ms,
                        ctx.get("user_email", "-"), rid)
    return response


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    """Uniform error body: {"detail": "...", "code": "...", ...}."""
    body: dict = {"detail": exc.detail}
    if isinstance(exc.detail, dict) and "message" in exc.detail:
        body = {"detail": exc.detail["message"], **{k: v for k, v in exc.detail.items() if k != "message"}}
    return JSONResponse(status_code=exc.status_code, content=body, headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    errors = [
        {"field": ".".join(str(p) for p in e.get("loc", []) if p not in ("body", "query")), "message": e.get("msg")}
        for e in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": "Invalid request", "errors": errors})


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):  # pragma: no cover
    rid = (request_ctx.get() or {}).get("request_id")
    log.exception("Unhandled error on %s %s (request %s)", request.method, request.url.path, rid)
    return JSONResponse(status_code=500, content={"detail": "Internal server error", "request_id": rid})


for r in (system.router, auth.router, auth.users_router, analytics.router, admin.router, imports.router,
          anomalies.router, reports.router, exports.router, system.backup_router, action_center.router):
    app.include_router(r)
