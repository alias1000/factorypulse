"""Login / logout / session endpoints and user administration."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import User, UserSession, utcnow
from ..services import auth as A
from ..services.data_access import get_company
from ..services.settings_store import audit
from .deps import Principal, current_principal_allow_pw_change, require

router = APIRouter(prefix="/api/auth", tags=["auth"])
users_router = APIRouter(prefix="/api/users", tags=["users"])

INVALID = "Invalid e-mail or password, or the account is temporarily locked."


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=1, max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., max_length=256)
    new_password: str = Field(..., max_length=256)


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def user_dict(u: User) -> dict:
    return {
        "id": u.id, "email": u.email, "display_name": u.display_name, "role": u.role, "is_active": u.is_active,
        "must_change_password": u.must_change_password,
        "locked": A.is_locked(u),
        "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "preferred_language": u.preferred_language,
    }


def _me(p: Principal) -> dict:
    return {"user": user_dict(p.user), "permissions": A.permissions_for(p.role), "csrf_token": p.session.csrf_token,
            "session_expires_at": p.session.expires_at.isoformat(),
            "idle_timeout_minutes": get_settings().session_idle_minutes}


def _set_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(A.SESSION_COOKIE, token, max_age=s.session_absolute_hours * 3600, httponly=True,
                        secure=s.secure_cookies, samesite="strict", path="/api")


@router.post("/login", summary="Log in with e-mail and password (sets an HttpOnly session cookie)")
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    s = get_settings()
    ip = _client_ip(request) or "unknown"
    if not A.login_limiter.hit(f"ip:{ip}", s.login_ip_max_attempts, s.login_ip_window_minutes * 60):
        audit(db, "auth.rate_limited", None, details={"ip": ip})
        db.commit()
        raise HTTPException(429, "Too many login attempts. Please wait a few minutes.")
    email = A.normalize_email(body.email)
    user = db.scalar(select(User).where(User.email == email))
    ok, rehash = A.verify_password(user.password_hash if user else None, body.password)
    if user is None or not user.is_active or A.is_locked(user) or not ok:
        if user is not None and user.is_active and not A.is_locked(user) and not ok:
            A.register_failure(user)
            if A.is_locked(user):
                audit(db, "auth.locked", user.company_id, "user", user.id, {"email": email})
        audit(db, "auth.login_failed", user.company_id if user else None, "user", user.id if user else None,
              {"email": email[:254]})
        db.commit()
        raise HTTPException(401, INVALID)
    if rehash:
        user.password_hash = rehash
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = utcnow()
    token, sess = A.create_session(db, user, ip, request.headers.get("user-agent"))
    A.ctx().update(user_id=user.id, user_email=user.email, company_id=user.company_id)
    audit(db, "auth.login", user.company_id, "user", user.id)
    A.purge_expired_sessions(db)
    db.commit()
    _set_cookie(response, token)
    return _me(Principal(user, sess))


@router.post("/logout", summary="End the current session")
def logout(response: Response, p: Principal = Depends(current_principal_allow_pw_change), db: Session = Depends(get_db)):
    sess = db.get(UserSession, p.session.id)
    A.revoke_session(db, sess)
    audit(db, "auth.logout", p.company_id, "user", p.id)
    db.commit()
    response.delete_cookie(A.SESSION_COOKIE, path="/api")
    return {"status": "logged_out"}


@router.post("/logout-all", summary="End all sessions of the current user")
def logout_all(response: Response, p: Principal = Depends(current_principal_allow_pw_change), db: Session = Depends(get_db)):
    n = A.revoke_all_sessions(db, p.id)
    audit(db, "auth.logout_all", p.company_id, "user", p.id, {"sessions": n})
    db.commit()
    response.delete_cookie(A.SESSION_COOKIE, path="/api")
    return {"status": "logged_out", "sessions_revoked": n}


@router.get("/me", summary="Current user, permissions and CSRF token")
def me(p: Principal = Depends(current_principal_allow_pw_change)):
    return _me(p)


@router.post("/change-password", summary="Change own password (revokes other sessions)")
def change_password(body: ChangePasswordRequest, p: Principal = Depends(current_principal_allow_pw_change),
                    db: Session = Depends(get_db)):
    user = db.get(User, p.id)
    ok, _ = A.verify_password(user.password_hash, body.current_password)
    if not ok:
        audit(db, "auth.password_change_failed", user.company_id, "user", user.id)
        db.commit()
        raise HTTPException(400, {"code": "wrong_password", "message": "The current password is incorrect."})
    if body.new_password == body.current_password:
        raise HTTPException(422, {"code": "weak_password", "message": "The new password must differ from the current one."})
    problems = A.password_problems(body.new_password, user.email)
    if problems:
        raise HTTPException(422, {"code": "weak_password", "message": " ".join(problems)})
    user.password_hash = A.hash_password(body.new_password)
    user.must_change_password = False
    user.password_changed_at = utcnow()
    n = A.revoke_all_sessions(db, user.id, except_id=p.session.id)
    audit(db, "auth.password_changed", user.company_id, "user", user.id, {"other_sessions_revoked": n})
    db.commit()
    return _me(Principal(user, db.get(UserSession, p.session.id)))


# ----------------------------------------------------------------------------- user administration

Role = Literal["viewer", "analyst", "admin"]


class UserCreate(BaseModel):
    email: str = Field(..., min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    display_name: str = Field(..., min_length=1, max_length=200)
    role: Role = "viewer"
    temporary_password: str = Field(..., max_length=256)


class UserUpdate(BaseModel):
    display_name: str | None = Field(None, min_length=1, max_length=200)
    role: Role | None = None
    is_active: bool | None = None
    unlock: bool | None = None


class PasswordReset(BaseModel):
    temporary_password: str = Field(..., max_length=256)


def _target(db: Session, admin: Principal, user_id: int) -> User:
    u = db.get(User, user_id)
    if u is None or u.company_id != admin.company_id:
        raise HTTPException(404, "User not found")
    return u


@users_router.get("", summary="List users")
def list_users(p: Principal = Depends(require("user.manage")), db: Session = Depends(get_db)):
    rows = db.scalars(select(User).where(User.company_id == p.company_id).order_by(User.email))
    return [user_dict(u) for u in rows]


@users_router.post("", summary="Create a user with a temporary password (must be changed at first login)")
def create_user(body: UserCreate, p: Principal = Depends(require("user.manage")), db: Session = Depends(get_db)):
    try:
        u = A.create_user(db, p.company_id, body.email, body.display_name, body.role, body.temporary_password, True)
    except ValueError as exc:
        raise HTTPException(422, {"code": "invalid_user", "message": str(exc)}) from exc
    audit(db, "user.created", p.company_id, "user", u.id, {"email": u.email, "role": u.role})
    db.commit()
    return user_dict(u)


@users_router.patch("/{user_id}", summary="Change role, name, active state or unlock a user")
def update_user(user_id: int, body: UserUpdate, p: Principal = Depends(require("user.manage")),
                db: Session = Depends(get_db)):
    u = _target(db, p, user_id)
    changes: dict = {}
    demoting = body.role is not None and body.role != "admin" and u.role == "admin"
    deactivating = body.is_active is False and u.is_active
    if (demoting or deactivating) and u.role == "admin" and A.active_admin_count(db, p.company_id) <= 1:
        raise HTTPException(409, {"code": "last_admin", "message": "The last active administrator cannot be demoted or deactivated."})
    if u.id == p.id and (demoting or deactivating):
        raise HTTPException(409, {"code": "self_lockout", "message": "You cannot demote or deactivate your own account."})
    if body.display_name is not None:
        u.display_name, changes["display_name"] = body.display_name.strip(), body.display_name.strip()
    if body.role is not None and body.role != u.role:
        changes["role"] = [u.role, body.role]
        u.role = body.role
    if body.is_active is not None and body.is_active != u.is_active:
        u.is_active = body.is_active
        changes["is_active"] = body.is_active
        if not body.is_active:
            A.revoke_all_sessions(db, u.id)
    if body.unlock:
        u.locked_until, u.failed_login_count = None, 0
        changes["unlocked"] = True
    if "role" in changes:
        A.revoke_all_sessions(db, u.id)  # permissions changed: force re-login
    audit(db, "user.updated", p.company_id, "user", u.id, changes)
    db.commit()
    return user_dict(u)


@users_router.post("/{user_id}/reset-password", summary="Set a temporary password and revoke the user's sessions")
def reset_password(user_id: int, body: PasswordReset, p: Principal = Depends(require("user.manage")),
                   db: Session = Depends(get_db)):
    u = _target(db, p, user_id)
    problems = A.password_problems(body.temporary_password, u.email)
    if problems:
        raise HTTPException(422, {"code": "weak_password", "message": " ".join(problems)})
    u.password_hash = A.hash_password(body.temporary_password)
    u.must_change_password = True
    u.locked_until, u.failed_login_count = None, 0
    n = A.revoke_all_sessions(db, u.id)
    audit(db, "user.password_reset", p.company_id, "user", u.id, {"sessions_revoked": n})
    db.commit()
    return user_dict(u)


def ensure_bootstrap_admin(db: Session) -> None:
    """Create the first administrator from FP_INITIAL_ADMIN_* if no (real) user exists yet.

    The synthetic demo creates placeholder accounts (``@demo.factorypulse.invalid``, unusable passwords);
    they must not prevent the bootstrap (v0.3.1 fix: a seeded demo system previously had no admin)."""
    from ..services.demo_action_center import DEMO_DOMAIN

    s = get_settings()
    if db.scalar(select(User.id).where(~User.email.like(f"%@{DEMO_DOMAIN}")).limit(1)) is not None:
        return
    if not (s.initial_admin_email and s.initial_admin_password):
        return
    company = get_company(db)
    u = A.create_user(db, company.id, s.initial_admin_email, "Administrator", "admin", s.initial_admin_password,
                      must_change_password=True)
    audit(db, "user.bootstrap_admin", company.id, "user", u.id, {"email": u.email})
    db.commit()
