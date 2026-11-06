"""Authentication, authorization, CSRF, sessions, destructive-action guards, backups, readiness."""
import datetime as dt
import json

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import select, update

from conftest import TEST_PASSWORD, email_for, login

PUBLIC = {"/api/health", "/api/health/ready", "/api/auth/login", "/api/docs", "/api/redoc", "/api/openapi.json",
          "/api/docs/oauth2-redirect"}
# endpoints that intentionally allow a pending password change (but still require login)
SELF_SERVICE = {"/api/auth/me", "/api/auth/logout", "/api/auth/logout-all", "/api/auth/change-password"}


def _flatten(routes):
    for r in routes:
        inner = getattr(r, "original_router", None)  # FastAPI >= 0.13x wraps included routers
        if inner is not None:
            yield from _flatten(inner.routes)
        else:
            yield r


def _api_routes(app):
    for r in _flatten(app.routes):
        if isinstance(r, APIRoute) and r.path.startswith("/api"):
            for m in r.methods:
                yield m, r


def _fill(path: str) -> str:
    return (path.replace("{job_id}", "1").replace("{report_id}", "1").replace("{user_id}", "1")
            .replace("{name}", "fp-backup-20260101-000000-abcdef"))


# --------------------------------------------------------------------------- deny by default

def test_every_api_route_requires_authentication(app):
    anon = login(app, None)
    checked = 0
    for method, route in _api_routes(app):
        if route.path in PUBLIC:
            continue
        r = anon.request(method, _fill(route.path), json={})
        assert r.status_code == 401, f"{method} {route.path} returned {r.status_code} without authentication"
        checked += 1
    assert checked > 35


def test_every_protected_route_declares_a_permission(app):
    """Static check: each non-public route depends on require(<permission>) or the self-service principal."""
    def deps(dependant):
        for d in dependant.dependencies:
            yield d.call
            yield from deps(d)
    for method, route in _api_routes(app):
        if route.path in PUBLIC:
            continue
        calls = list(deps(route.dependant))
        has_perm = any(getattr(c, "permission", None) for c in calls)
        self_service = route.path in SELF_SERVICE and any(
            getattr(c, "__name__", "") == "current_principal_allow_pw_change" for c in calls)
        assert has_perm or self_service, f"{method} {route.path} has no permission check"


# --------------------------------------------------------------------------- roles

@pytest.mark.parametrize("role,method,path,body,expected", [
    ("viewer", "GET", "/api/kpis", None, 200),
    ("viewer", "GET", "/api/reports", None, 200),
    ("viewer", "POST", "/api/anomalies/detect", {}, 200),
    ("viewer", "POST", "/api/reports", {"report_type": "daily", "format": "xlsx", "start_date": "2026-09-01"}, 403),
    ("viewer", "GET", "/api/imports", None, 403),
    ("viewer", "PUT", "/api/settings", {"company_name": "X"}, 403),
    ("viewer", "GET", "/api/users", None, 403),
    ("viewer", "GET", "/api/audit-log", None, 403),
    ("analyst", "POST", "/api/reports", {"report_type": "daily", "format": "xlsx", "start_date": "2026-09-01"}, 200),
    ("analyst", "GET", "/api/imports", None, 200),
    ("analyst", "POST", "/api/imports/1/revert", {"confirm": "REVERT"}, 403),
    ("analyst", "PUT", "/api/settings", {"default_language": "de"}, 403),
    ("analyst", "GET", "/api/admin/backups", None, 403),
    ("analyst", "POST", "/api/admin/demo/load", {"password": TEST_PASSWORD, "confirm": "RELOAD"}, 403),
    ("admin", "GET", "/api/users", None, 200),
    ("admin", "GET", "/api/audit-log", None, 200),
    ("admin", "GET", "/api/admin/system", None, 200),
])
def test_role_matrix(as_role, demo, role, method, path, body, expected):
    c = as_role(role)
    r = c.request(method, path, json=body)
    assert r.status_code == expected, r.text
    if expected == 403:
        assert r.json()["code"] == "forbidden"


def test_viewer_cannot_upload(as_role):
    r = as_role("viewer").post("/api/imports/upload", files={"file": ("a.csv", b"Date\n2026-01-01\n")})
    assert r.status_code == 403


# --------------------------------------------------------------------------- CSRF & cookies

def test_csrf_required_for_state_changes(app):
    c = login(app, "analyst")
    token = c.headers.pop("X-CSRF-Token")
    body = {"report_type": "daily", "format": "xlsx", "start_date": "2026-09-01"}
    assert c.post("/api/reports", json=body).json()["code"] == "csrf_failed"
    c.headers["X-CSRF-Token"] = "wrong"
    assert c.post("/api/reports", json=body).status_code == 403
    c.headers["X-CSRF-Token"] = token
    assert c.get("/api/kpis").status_code == 200  # safe methods need no token


def test_session_cookie_flags(app):
    c = login(app, None)
    r = c.post("/api/auth/login", json={"email": email_for("viewer"), "password": TEST_PASSWORD})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/api" in cookie
    assert "csrf_token" in r.json() and "password_hash" not in json.dumps(r.json())


def test_passwords_are_argon2_hashed(app):
    from app.db import SessionLocal
    from app.models import User
    with SessionLocal() as db:
        u = db.scalar(select(User).where(User.email == email_for("admin")))
        assert u.password_hash.startswith("$argon2id$") and TEST_PASSWORD not in u.password_hash


# --------------------------------------------------------------------------- login protection

def test_login_errors_are_generic_and_case_insensitive(app):
    c = login(app, None)
    a = c.post("/api/auth/login", json={"email": "nobody@test.example", "password": "x" * 12})
    b = c.post("/api/auth/login", json={"email": email_for("viewer"), "password": "wrong-password-123"})
    assert a.status_code == b.status_code == 401 and a.json()["detail"] == b.json()["detail"]
    ok = c.post("/api/auth/login", json={"email": email_for("viewer").upper(), "password": TEST_PASSWORD})
    assert ok.status_code == 200


def test_account_lockout_and_admin_unlock(app, client):
    from app.services.auth import login_limiter
    tmp = client.post("/api/users", json={"email": "lock@test.example", "display_name": "Lock", "role": "viewer",
                                          "temporary_password": "Temporary-Pass-2026"}).json()
    login_limiter.reset()
    c = login(app, None)
    for _ in range(5):
        assert c.post("/api/auth/login", json={"email": "lock@test.example", "password": "bad-password-1234"}).status_code == 401
    # correct password is now rejected as well (locked)
    assert c.post("/api/auth/login", json={"email": "lock@test.example", "password": "Temporary-Pass-2026"}).status_code == 401
    assert client.get("/api/users").json()[[u["email"] for u in client.get("/api/users").json()].index("lock@test.example")]["locked"]
    client.patch(f"/api/users/{tmp['id']}", json={"unlock": True}).raise_for_status()
    assert c.post("/api/auth/login", json={"email": "lock@test.example", "password": "Temporary-Pass-2026"}).status_code == 200
    log = client.get("/api/audit-log", params={"event": "auth."}).json()
    assert any(e["event"] == "auth.locked" for e in log)


def test_ip_rate_limit(app):
    c = login(app, None)
    codes = [c.post("/api/auth/login", json={"email": f"x{i}@test.example", "password": "y" * 12}).status_code
             for i in range(65)]
    assert 429 in codes and codes.index(429) == 60


# --------------------------------------------------------------------------- sessions

def test_logout_revokes_session(app):
    c = login(app, "viewer")
    assert c.get("/api/kpis").status_code == 200
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/api/kpis").status_code == 401


def test_idle_timeout(app):
    from app.db import SessionLocal
    from app.models import User, UserSession, utcnow
    c = login(app, "viewer")
    with SessionLocal() as db:
        uid = db.scalar(select(User.id).where(User.email == email_for("viewer")))
        db.execute(update(UserSession).where(UserSession.user_id == uid)
                   .values(last_seen_at=utcnow() - dt.timedelta(hours=3)))
        db.commit()
    assert c.get("/api/kpis").status_code == 401


def test_new_user_must_change_password(app, client):
    u = client.post("/api/users", json={"email": "new@test.example", "display_name": "Neu", "role": "analyst",
                                        "temporary_password": "Erstes-Passwort-2026"}).json()
    assert u["must_change_password"] is True
    c = login(app, None)
    me = c.post("/api/auth/login", json={"email": "new@test.example", "password": "Erstes-Passwort-2026"}).json()
    c.headers["X-CSRF-Token"] = me["csrf_token"]
    blocked = c.get("/api/kpis")
    assert blocked.status_code == 403 and blocked.json()["code"] == "password_change_required"
    weak = c.post("/api/auth/change-password", json={"current_password": "Erstes-Passwort-2026", "new_password": "short"})
    assert weak.status_code == 422
    ok = c.post("/api/auth/change-password", json={"current_password": "Erstes-Passwort-2026",
                                                   "new_password": "Neues-Sicheres-Passwort-77"})
    assert ok.status_code == 200 and ok.json()["user"]["must_change_password"] is False
    assert c.get("/api/kpis").status_code == 200


def test_role_change_and_deactivation_revoke_sessions(app, client):
    u = client.post("/api/users", json={"email": "temp@test.example", "display_name": "T", "role": "viewer",
                                        "temporary_password": "Zeitweise-Kennwort-2026"}).json()
    from app.db import SessionLocal
    from app.models import User
    with SessionLocal() as db:
        db.get(User, u["id"]).must_change_password = False
        db.commit()
    c = login(app, None)
    c.post("/api/auth/login", json={"email": "temp@test.example", "password": "Zeitweise-Kennwort-2026"}).raise_for_status()
    assert c.get("/api/kpis").status_code == 200
    client.patch(f"/api/users/{u['id']}", json={"role": "analyst"}).raise_for_status()
    assert c.get("/api/kpis").status_code == 401
    client.patch(f"/api/users/{u['id']}", json={"is_active": False}).raise_for_status()
    assert c.post("/api/auth/login", json={"email": "temp@test.example", "password": "Zeitweise-Kennwort-2026"}).status_code == 401


def test_last_admin_and_self_protection(client):
    users = client.get("/api/users").json()
    me = next(u for u in users if u["email"] == email_for("admin"))
    r = client.patch(f"/api/users/{me['id']}", json={"role": "viewer"})
    assert r.status_code == 409 and r.json()["code"] in ("last_admin", "self_lockout")


def test_weak_or_duplicate_user_rejected(client):
    assert client.post("/api/users", json={"email": "w@test.example", "display_name": "W", "role": "viewer",
                                           "temporary_password": "123"}).status_code == 422
    assert client.post("/api/users", json={"email": email_for("viewer"), "display_name": "Dup", "role": "viewer",
                                           "temporary_password": "Another-Pass-2026"}).status_code == 422


# --------------------------------------------------------------------------- destructive actions

def test_destructive_actions_need_reauth_and_confirmation(client, demo):
    wrong_pw = client.post("/api/admin/data/delete", json={"password": "nope-nope-nope", "confirm": "DELETE"})
    assert wrong_pw.status_code == 403 and wrong_pw.json()["code"] == "reauth_failed"
    wrong_word = client.post("/api/admin/data/delete", json={"password": TEST_PASSWORD, "confirm": "delete"})
    assert wrong_word.status_code == 400
    assert client.get("/api/meta").json()["data_range"]["record_count"] > 0  # nothing deleted
    log = client.get("/api/audit-log", params={"event": "data.delete"}).json()
    assert log and log[0]["user_email"] == email_for("admin")


def test_demo_tools_can_be_disabled(client, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "enable_demo_tools", False)
    r = client.post("/api/admin/demo/load", json={"password": TEST_PASSWORD, "confirm": "RELOAD"})
    assert r.status_code == 403 and r.json()["code"] == "demo_tools_disabled"


def test_demo_reload_removes_orphaned_files(client, demo):
    from app.config import get_settings
    rep = client.post("/api/reports", json={"report_type": "daily", "format": "xlsx", "start_date": "2026-09-01"}).json()
    before = set(p.name for p in get_settings().report_dir.iterdir())
    assert any(n.endswith(".xlsx") for n in before)
    r = client.post("/api/admin/demo/load", json={"password": TEST_PASSWORD, "confirm": "RELOAD"}).json()
    assert r["files_removed"] >= 1
    assert not list(get_settings().report_dir.iterdir())
    assert rep["id"]


def test_audit_entries_carry_the_acting_user(client, demo):
    client.put("/api/settings", json={"default_language": "de"}).raise_for_status()
    e = client.get("/api/audit-log", params={"event": "settings."}).json()[0]
    assert e["user_email"] == email_for("admin") and e["client_ip"]


# --------------------------------------------------------------------------- readiness

def test_readiness_public_without_details(app):
    anon = login(app, None)
    r = anon.get("/api/health/ready")
    assert r.status_code == 200 and r.json()["checks"]["migrations"] == "ok"
    assert "detail" not in json.dumps(r.json())
    assert r.headers["x-request-id"] and r.headers["cache-control"] == "no-store"


def test_system_status_details_for_admin(client):
    s = client.get("/api/admin/system").json()
    names = {c["name"] for c in s["checks"]}
    assert {"database", "migrations", "pdf_engine", "storage:uploads"} <= names


# --------------------------------------------------------------------------- backups

@pytest.fixture()
def postgres_only():
    from app.db import engine
    if engine.dialect.name != "postgresql":
        pytest.skip("backups require PostgreSQL")


def test_backup_and_restore_round_trip(app, client, demo, postgres_only):
    created = client.post("/api/admin/backups", json={"note": "test"}).json()
    name = created["name"]
    assert created["database"]["sha256"] and created["alembic_revision"]
    # change data after the backup
    client.put("/api/settings", json={"company_name": "Changed after backup"}).raise_for_status()
    up = client.post("/api/imports/upload", files={"file": ("x.csv", b"Date,Line,Machine,Shift,Product,Actual Quantity\n2026-12-01,L1,M-01,F,P-100,5\n")}).json()
    client.post(f"/api/imports/{up['job']['id']}/commit", json={"options": {"mapping": up["suggested_mapping"]}}).raise_for_status()
    n_after = client.get("/api/meta").json()["data_range"]["record_count"]

    assert client.post(f"/api/admin/backups/{name}/restore", json={"password": "wrong-pass-123", "confirm": "RESTORE"}).status_code == 403
    res = client.post(f"/api/admin/backups/{name}/restore", json={"password": TEST_PASSWORD, "confirm": "RESTORE"})
    assert res.status_code == 200, res.text
    assert res.json()["safety_backup"]
    # all sessions were revoked by the restore
    assert client.get("/api/meta").status_code == 401
    me = client.post("/api/auth/login", json={"email": email_for("admin"), "password": TEST_PASSWORD}).json()
    client.headers["X-CSRF-Token"] = me["csrf_token"]
    meta = client.get("/api/meta").json()
    assert meta["company"]["name"] != "Changed after backup"
    assert meta["data_range"]["record_count"] == n_after - 1
    names = [b["name"] for b in client.get("/api/admin/backups").json()]
    assert res.json()["safety_backup"] in names and name in names
    assert client.get("/api/audit-log", params={"event": "backup.restored"}).json()


def test_tampered_backup_is_refused(client, postgres_only):
    from app.config import get_settings
    name = client.post("/api/admin/backups", json={}).json()["name"]
    dump = get_settings().backup_dir / name / "database.dump"
    dump.write_bytes(dump.read_bytes() + b"tampered")
    r = client.post(f"/api/admin/backups/{name}/restore", json={"password": TEST_PASSWORD, "confirm": "RESTORE"})
    assert r.status_code == 400 and "Checksum" in r.json()["detail"]
    assert client.get("/api/meta").status_code == 200  # still logged in, nothing restored


def test_backup_download_and_name_validation(client, postgres_only):
    name = client.post("/api/admin/backups", json={}).json()["name"]
    d = client.get(f"/api/admin/backups/{name}/download")
    assert d.status_code == 200 and d.headers["content-type"] == "application/x-tar"
    assert client.get("/api/admin/backups/..%2F..%2Fetc/download").status_code == 404
    assert client.post(f"/api/admin/backups/{name}/delete", json={"password": TEST_PASSWORD, "confirm": "DELETE"}).status_code == 200
