import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .routers import admin, auth, media, member, onboarding, public

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
    response = await call_next(request)

    # double-submit CSRF cookie for form posts
    if "exp_csrf" not in request.cookies:
        response.set_cookie(
            "exp_csrf", secrets.token_urlsafe(24),
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
            "img-src 'self' data: blob:; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; "
            "frame-src https://challenges.cloudflare.com; "
            "base-uri 'self'; form-action 'self'"
        )
    else:
        response.headers["Cache-Control"] = "public, max-age=60"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; base-uri 'self'; form-action 'self'"
        )
    return response


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
