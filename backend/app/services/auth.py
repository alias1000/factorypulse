"""Authentication, sessions and role-based permissions.

* Passwords: Argon2id (argon2-cffi defaults, OWASP-recommended), with transparent re-hashing.
* Sessions: random 256-bit token in an HttpOnly cookie; only its SHA-256 is stored server-side.
  Idle timeout and absolute lifetime are enforced on every request; sessions can be revoked.
* CSRF: per-session token that must be echoed in the ``X-CSRF-Token`` header on every
  state-changing request (in addition to SameSite=Strict cookies).
* Brute force: per-account lockout (persistent) + per-IP sliding-window limit (in-process).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque
from contextvars import ContextVar
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import User, UserSession, utcnow

_hasher = PasswordHasher()
# Hash of a random password: used to spend the same time when an e-mail does not exist.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))

SESSION_COOKIE = "fp_session"
CSRF_HEADER = "X-CSRF-Token"

# ----------------------------------------------------------------------------- roles & permissions

ROLE_ORDER = {"viewer": 0, "analyst": 1, "admin": 2}
PERMISSIONS: dict[str, str] = {
    # permission: minimum role
    "data.read": "viewer",
    "anomaly.run": "viewer",
    "report.read": "viewer",
    "settings.read": "viewer",
    "action.read": "viewer",
    "action.write": "analyst",
    "action.manage": "admin",
    "report.generate": "analyst",
    "report.delete": "analyst",
    "import.read": "analyst",
    "import.write": "analyst",
    "import.revert": "admin",
    "settings.write": "admin",
    "audit.read": "admin",
    "user.manage": "admin",
    "backup.manage": "admin",
    "system.read": "admin",
    "data.destroy": "admin",
}


def has_permission(role: str, permission: str) -> bool:
    need = PERMISSIONS[permission]  # KeyError on typos is intentional
    return ROLE_ORDER.get(role, -1) >= ROLE_ORDER[need]


def permissions_for(role: str) -> list[str]:
    return sorted(p for p in PERMISSIONS if has_permission(role, p))


# ----------------------------------------------------------------------------- request context

# A mutable dict per request (set by middleware). Dependencies running in the thread pool mutate it,
# so the audit log can attribute actions without threading the user through every call.
request_ctx: ContextVar[dict[str, Any] | None] = ContextVar("request_ctx", default=None)


def ctx() -> dict[str, Any]:
    c = request_ctx.get()
    return c if c is not None else {}


# ----------------------------------------------------------------------------- passwords

COMMON_PASSWORDS = {
    "passwort1234", "password1234", "123456789012", "qwertz123456", "qwerty123456", "factorypulse",
    "factorypulse1", "administrator", "willkommen123", "welcome12345", "letmein12345", "changeme1234",
}


def hash_password(pw: str) -> str:
    return _hasher.hash(pw)


def verify_password(stored_hash: str | None, pw: str) -> tuple[bool, str | None]:
    """Returns (ok, new_hash_if_rehash_needed)."""
    try:
        _hasher.verify(stored_hash or _DUMMY_HASH, pw)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False, None
    if stored_hash is None:
        return False, None
    if _hasher.check_needs_rehash(stored_hash):
        return True, _hasher.hash(pw)
    return True, None


def password_problems(pw: str, email: str | None = None) -> list[str]:
    """NIST 800-63B style: length + blocklist, no arbitrary composition rules."""
    s = get_settings()
    problems = []
    if len(pw) < s.password_min_length:
        problems.append(f"Password must be at least {s.password_min_length} characters long.")
    if len(pw) > 256:
        problems.append("Password must be at most 256 characters long.")
    low = pw.lower()
    if low in COMMON_PASSWORDS or len(set(pw)) < 4:
        problems.append("Password is too common or too simple.")
    if email and email.split("@")[0].lower() in low and len(email.split("@")[0]) >= 4:
        problems.append("Password must not contain the e-mail name.")
    return problems


def normalize_email(email: str) -> str:
    return email.strip().lower()


# ----------------------------------------------------------------------------- sessions

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user: User, ip: str | None, user_agent: str | None) -> tuple[str, UserSession]:
    s = get_settings()
    token = secrets.token_urlsafe(32)
    now = utcnow()
    sess = UserSession(
        user_id=user.id, token_hash=_hash_token(token), csrf_token=secrets.token_urlsafe(32),
        created_at=now, last_seen_at=now, expires_at=now + dt.timedelta(hours=s.session_absolute_hours),
        ip=ip, user_agent=(user_agent or "")[:300],
    )
    db.add(sess)
    db.flush()
    return token, sess


def _aware(d: dt.datetime) -> dt.datetime:
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)  # SQLite returns naive datetimes


def resolve_session(db: Session, token: str | None) -> UserSession | None:
    if not token or len(token) > 200:
        return None
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == _hash_token(token)))
    if sess is None or sess.revoked_at is not None:
        return None
    now = utcnow()
    idle = dt.timedelta(minutes=get_settings().session_idle_minutes)
    if _aware(sess.expires_at) <= now or _aware(sess.last_seen_at) + idle <= now:
        return None
    if not sess.user.is_active:
        return None
    if (now - _aware(sess.last_seen_at)).total_seconds() > 60:  # throttle writes
        sess.last_seen_at = now
        db.commit()
    return sess


def revoke_session(db: Session, sess: UserSession) -> None:
    sess.revoked_at = utcnow()


def revoke_all_sessions(db: Session, user_id: int, except_id: int | None = None) -> int:
    q = update(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
    if except_id is not None:
        q = q.where(UserSession.id != except_id)
    return db.execute(q.values(revoked_at=utcnow())).rowcount or 0


def purge_expired_sessions(db: Session) -> int:
    cutoff = utcnow() - dt.timedelta(days=7)
    from sqlalchemy import delete, or_
    return db.execute(delete(UserSession).where(or_(UserSession.expires_at < cutoff,
                                                    UserSession.revoked_at < cutoff))).rowcount or 0


def active_admin_count(db: Session, company_id: int) -> int:
    return db.scalar(select(func.count(User.id)).where(
        User.company_id == company_id, User.role == "admin", User.is_active.is_(True))) or 0


# ----------------------------------------------------------------------------- brute-force protection

class SlidingWindowLimiter:
    """In-process limiter (per worker). Sufficient for a single-instance deployment; use the reverse
    proxy for global rate limits when running several replicas."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_s: float) -> bool:
        """Register an attempt; returns False when the limit is exceeded."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_s:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


login_limiter = SlidingWindowLimiter()


def is_locked(user: User) -> bool:
    return user.locked_until is not None and _aware(user.locked_until) > utcnow()


def register_failure(user: User) -> None:
    s = get_settings()
    user.failed_login_count = (user.failed_login_count or 0) + 1
    if user.failed_login_count >= s.login_max_failures:
        user.locked_until = utcnow() + dt.timedelta(minutes=s.login_lockout_minutes)
        user.failed_login_count = 0


def create_user(db: Session, company_id: int, email: str, display_name: str, role: str, password: str,
                must_change_password: bool = True) -> User:
    if role not in ROLE_ORDER:
        raise ValueError("invalid role")
    problems = password_problems(password, email)
    if problems:
        raise ValueError(" ".join(problems))
    email = normalize_email(email)
    if db.scalar(select(User.id).where(User.email == email)):
        raise ValueError("A user with this e-mail already exists.")
    u = User(company_id=company_id, email=email, display_name=display_name.strip() or email, role=role,
             password_hash=hash_password(password), must_change_password=must_change_password,
             password_changed_at=utcnow())
    db.add(u)
    db.flush()
    return u
