"""Shared template rendering with common context."""
import hashlib
import json
from datetime import datetime
from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .config import get_settings
from .db import SessionLocal
from .models import Member, User
from .security import _load_user
from .services.admissions import get_setting

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


@cache
def static_url(path: str) -> str:
    """URL for a file under app/static with a content hash so browsers and
    CDNs never serve a stale copy after the file changes."""
    file = APP_DIR / "static" / path
    digest = hashlib.sha256(file.read_bytes()).hexdigest()[:10]
    return f"/static/{path}?v={digest}"


NY = ZoneInfo("America/New_York")


def ny_time(dt: datetime | None, fmt: str = "%A, %B %-d · %-I:%M %p", tz: bool = True) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=__import__("datetime").timezone.utc)
    out = dt.astimezone(NY).strftime(fmt)
    return out + " ET" if tz else out


def interest_tags(db: Session) -> list[str]:
    raw = get_setting(db, "interest_tags", "")
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    return DEFAULT_INTEREST_TAGS


DEFAULT_INTEREST_TAGS = [
    "AI research", "Applied AI", "AI policy", "Agents", "Interpretability",
    "AI safety", "Robotics", "Biotech", "Creative tools", "Startups",
    "Venture", "Education", "Climate", "Science", "Writing",
]


templates.env.filters["ny_time"] = ny_time
templates.env.globals["static_url"] = static_url


def render(
    request: Request,
    template: str,
    status: int = 200,
    db: Session | None = None,
    **ctx,
) -> HTMLResponse:
    own_db = None
    if db is None:
        own_db = SessionLocal()
        db = own_db
    try:
        user: User | None = ctx.pop("user", None) or _load_user(request, db)
        member = db.get(Member, user.id) if user else None
        ctx.setdefault("user", user)
        ctx.setdefault("member", member)
        ctx.setdefault("is_member", bool(member and member.status in ("invited", "active")))
        ctx.setdefault("is_active_member", bool(member and member.status == "active"))
        ctx.setdefault("is_admin", bool(user and user.is_admin))
        ctx.setdefault("csrf", request.cookies.get("exp_csrf", ""))
        ctx.setdefault("s", get_settings())
        ctx.setdefault("notice", request.query_params.get("notice", ""))
        ctx.setdefault("error", request.query_params.get("error", ""))
        ctx.setdefault("request", request)
        resp = templates.TemplateResponse(request, template, ctx, status_code=status)
        return resp
    finally:
        if own_db:
            own_db.close()


def redirect(url: str, notice: str = "", error: str = "") -> HTMLResponse:
    from urllib.parse import urlencode

    from fastapi.responses import RedirectResponse

    params = {}
    if notice:
        params["notice"] = notice
    if error:
        params["error"] = error
    if params:
        sep = "&" if "?" in url else "?"
        url = url + sep + urlencode(params)
    return RedirectResponse(url, status_code=303)
