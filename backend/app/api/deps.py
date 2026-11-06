"""Authentication / authorization dependencies used by every router.

Usage:
    router = APIRouter(dependencies=[Depends(require("data.read"))])
    @router.post(..., dependencies=[Depends(require("import.write"))])

``require`` also enforces the CSRF token on state-changing methods and blocks accounts that
must change their password first.
"""
from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User, UserSession
from ..services.auth import CSRF_HEADER, SESSION_COOKIE, ctx, has_permission, resolve_session

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass
class Principal:
    user: User
    session: UserSession

    @property
    def id(self) -> int:
        return self.user.id

    @property
    def role(self) -> str:
        return self.user.role

    @property
    def company_id(self) -> int:
        return self.user.company_id


def _authenticate(request: Request, db: Session) -> Principal:
    sess = resolve_session(db, request.cookies.get(SESSION_COOKIE))
    if sess is None:
        raise HTTPException(401, {"code": "not_authenticated", "message": "Authentication required"})
    if request.method in UNSAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not hmac.compare_digest(sent.encode(), sess.csrf_token.encode()):
            raise HTTPException(403, {"code": "csrf_failed", "message": "Missing or invalid CSRF token"})
    c = ctx()
    c["user_id"], c["user_email"], c["company_id"] = sess.user.id, sess.user.email, sess.user.company_id
    return Principal(sess.user, sess)


def current_principal_allow_pw_change(request: Request, db: Session = Depends(get_db)) -> Principal:
    """Authenticated user, even if a password change is pending (for /auth/me, change-password, logout)."""
    return _authenticate(request, db)


def require(permission: str):
    def dep(request: Request, db: Session = Depends(get_db)) -> Principal:
        p = _authenticate(request, db)
        if p.user.must_change_password:
            raise HTTPException(403, {"code": "password_change_required",
                                      "message": "You must change your password before continuing"})
        if not has_permission(p.role, permission):
            raise HTTPException(403, {"code": "forbidden", "message": f"Your role '{p.role}' is not allowed to do this ({permission})"})
        return p

    dep.__name__ = f"require_{permission.replace('.', '_')}"
    dep.permission = permission  # type: ignore[attr-defined]  (used by the route-security test)
    return dep
