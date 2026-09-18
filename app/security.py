"""Sessions, request context, authorization dependencies, rate limiting, CSRF."""
import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import SEAT_STATUSES, Member, User

SESSION_COOKIE = "exp_session"
_serializer = lambda: URLSafeSerializer(get_settings().secret_key, salt="exp-session")


# ---------- session cookies ----------

def create_session(response: Response, user: User) -> None:
    s = get_settings()
    payload = {"uid": user.id, "sv": user.session_version, "iat": int(time.time())}
    response.set_cookie(
        SESSION_COOKIE,
        _serializer().dumps(payload),
        max_age=s.session_ttl_days * 86400,
        httponly=True,
        samesite="lax",
        secure=s.session_cookie_secure,
        path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def _load_user(request: Request, db: Session) -> User | None:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    try:
        payload = _serializer().loads(raw)
    except BadSignature:
        return None
    if int(time.time()) - int(payload.get("iat", 0)) > get_settings().session_ttl_days * 86400:
        return None
    user = db.get(User, payload.get("uid", ""))
    if not user or user.session_version != payload.get("sv"):
        return None
    return user


def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    return _load_user(request, db)


def require_user(user: User | None = Depends(current_user)) -> User:
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/auth/sign-in"})
    return user


def _seat_status(user: User, db: Session) -> str | None:
    m = db.get(Member, user.id)
    return m.status if m else None


def require_member(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)) -> User:
    """Active (or invited/onboarding) members only. Pausing/revoking takes effect
    immediately — status is read fresh on every request, so an existing session
    loses access as soon as the member row changes."""
    status = _seat_status(user, db)
    if status not in SEAT_STATUSES:
        raise HTTPException(status_code=303, headers={"Location": "/"})
    return user


def require_active_member(user: User = Depends(require_member), db: Session = Depends(get_db)) -> User:
    if _seat_status(user, db) != "active":
        raise HTTPException(status_code=303, headers={"Location": "/onboarding"})
    return user


def require_admin(user: User = Depends(require_user), db: Session = Depends(get_db)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=404)  # do not reveal admin surface
    return user


# ---------- CSRF (double-submit cookie for form posts) ----------

CSRF_COOKIE = "exp_csrf"


def issue_csrf(response: Response) -> str:
    token = secrets.token_urlsafe(24)
    response.set_cookie(CSRF_COOKIE, token, httponly=False, samesite="lax", path="/",
                        secure=get_settings().session_cookie_secure)
    return token


async def verify_csrf(request: Request) -> None:
    """Double-submit: form field/header must match the cookie. SameSite=Lax on
    the session cookie already blocks cross-site posts; this adds defense in
    depth for state-changing form routes."""
    cookie = request.cookies.get(CSRF_COOKIE)
    if not cookie:
        raise HTTPException(403, "Missing CSRF token")
    form = await request.form()
    sent = form.get("csrf") or request.headers.get("x-csrf")
    if not sent or not hmac.compare_digest(str(sent), cookie):
        raise HTTPException(403, "Bad CSRF token")


# ---------- rate limiting (in-memory sliding window; per-instance) ----------

class RateLimiter:
    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_s: int) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_s:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True


limiter = RateLimiter()


def client_key(request: Request, scope: str) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    ip = fwd.split(",")[0].strip() or (request.client.host if request.client else "?")
    return f"{scope}:{ip}"


def rate_limit(request: Request, scope: str, limit: int, window_s: int = 60) -> None:
    if not limiter.allow(client_key(request, scope), limit, window_s):
        raise HTTPException(429, "Too many requests — please wait a moment and try again.")


# ---------- misc ----------

def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def normalize_email(email: str) -> str:
    return email.strip().lower()


def utcnow() -> datetime:
    return datetime.now(UTC)
