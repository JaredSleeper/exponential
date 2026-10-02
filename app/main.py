import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .routers import admin, auth, media, member, onboarding, public
from .security import CSRF_COOKIE, CSRF_MAX_AGE
from .web import render

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app):
    # idempotent — brings a fresh database to head; safe on every boot
    from alembic.config import Config

    from alembic import command
    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
    command.upgrade(cfg, "head")
    if get_settings().seed_demo_data:
        from . import seed
        seed.run()
    yield


app = FastAPI(title="Exponential", docs_url=None, redoc_url=None, lifespan=lifespan)

PRIVATE_PREFIXES = ("/members", "/directory", "/onboarding", "/admin", "/media", "/auth")


@app.middleware("http")
async def security_and_csrf(request: Request, call_next):
    s = get_settings()
    token = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(24)
    request.state.csrf = token
    response = await call_next(request)

    # double-submit CSRF cookie for form posts
    if not request.cookies.get(CSRF_COOKIE):
        response.set_cookie(
            CSRF_COOKIE, token, max_age=CSRF_MAX_AGE,
            httponly=False, samesite="lax", path="/", secure=s.session_cookie_secure,
        )

    path = request.url.path
    private = path.startswith(PRIVATE_PREFIXES)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    if private:
        # member/admin/auth responses are never publicly cached or indexed
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        clerk_hosts = " https://*.clerk.accounts.dev https://clerk.exponential.nyc https://challenges.cloudflare.com"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            f"script-src 'self' 'unsafe-inline'{clerk_hosts if s.auth_provider == 'clerk' else ''}; "
            f"connect-src 'self'{clerk_hosts if s.auth_provider == 'clerk' else ''}; "
            "img-src 'self' data: blob: https://img.clerk.com; "
            "worker-src 'self' blob:; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; "
            "frame-src https://challenges.cloudflare.com; "
            "base-uri 'self'; form-action 'self'"
        )
    else:
        is_static = path == "/static" or path.startswith("/static/")
        is_html = response.headers.get("content-type", "").startswith("text/html")
        response.headers["Cache-Control"] = (
            "private, no-cache" if is_html and not is_static else "public, max-age=60"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; base-uri 'self'; form-action 'self'"
        )
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    csrf_failure = exc.status_code == 403 and exc.detail in (
        "Missing CSRF token",
        "Bad CSRF token",
    )
    if not csrf_failure or "text/html" not in request.headers.get("accept", "").lower():
        return JSONResponse(
            {"detail": exc.detail},
            status_code=exc.status_code,
            headers=exc.headers,
        )

    back_url = "/"
    referer = request.headers.get("referer", "")
    try:
        parsed_referer = urlsplit(referer)
    except ValueError:
        parsed_referer = None
    if (
        parsed_referer
        and parsed_referer.scheme == request.url.scheme
        and parsed_referer.netloc.casefold() == request.url.netloc.casefold()
    ):
        path = parsed_referer.path or "/"
        if path.startswith("/") and not path.startswith("//") and "\\" not in path:
            back_url = path
    return render(request, "error.html", status=403, back_url=back_url)


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}


@app.get("/robots.txt", include_in_schema=False)
def robots():
    return PlainTextResponse("User-agent: *\nAllow: /$\nAllow: /apply\nDisallow: /\n")


app.include_router(public.router)
app.include_router(auth.router)
app.include_router(onboarding.router)
app.include_router(member.router)
app.include_router(admin.router)
app.include_router(media.router)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
