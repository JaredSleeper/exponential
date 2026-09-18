"""Sign-in: Clerk in production, a labelled one-time-code adapter in dev.

AUTH_PROVIDER=dev flow: enter email → we "send" a 6-digit code via the email
outbox (console adapter prints it; in dev the code is also shown on-screen,
clearly labelled INSECURE DEV). Verifying proves inbox control, same shape as
the production Clerk flow.
"""
import logging
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clerk as clerk_adp
from ..audit import audit
from ..config import get_settings
from ..db import get_db
from ..emailer import queue_email
from ..models import Invitation, LoginCode, Member, User
from ..security import (
    clear_session,
    create_session,
    current_user,
    normalize_email,
    rate_limit,
    require_user,
    sha256_hex,
    utcnow,
    verify_csrf,
)
from ..services import admissions
from ..web import redirect, render

router = APIRouter()
log = logging.getLogger("exponential.auth")


def _upsert_user(db: Session, email: str, clerk_user_id: str | None = None) -> User:
    user = db.scalar(select(User).where(User.email == email))
    if not user:
        user = User(email=email)
        db.add(user)
        db.flush()
    if clerk_user_id and not user.clerk_user_id:
        user.clerk_user_id = clerk_user_id
    # admin bootstrap — the only path to a first admin is deployment config
    if email in get_settings().admin_email_list and not user.is_admin:
        user.is_admin = True
    user.last_login_at = utcnow()
    db.flush()
    return user


def _safe_next(next_url: str) -> str:
    return next_url if next_url.startswith("/") and not next_url.startswith("//") else "/members"


# ---------- sign-in pages ----------

@router.get("/auth/sign-in")
def sign_in(request: Request, next: str = "/", user: User | None = Depends(current_user)):
    s = get_settings()
    if user:
        return redirect(_safe_next(next) if next != "/" else "/members")
    if s.auth_provider == "clerk":
        import base64
        clerk_js_url = ""
        try:
            host = base64.b64decode(
                s.clerk_publishable_key.split("_", 2)[2] + "==").decode().rstrip("$")
            clerk_js_url = f"https://{host}/npm/@clerk/clerk-js@5/dist/clerk.browser.js"
        except (ValueError, IndexError, UnicodeDecodeError):
            pass
        return render(request, "auth/signin_clerk.html", next=_safe_next(next),
                      clerk_pk=s.clerk_publishable_key, clerk_js_url=clerk_js_url)
    return render(request, "auth/signin_dev.html", next=_safe_next(next))


@router.post("/auth/sign-in")
async def sign_in_start(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(verify_csrf),
    email: str = Form(""),
    next: str = Form("/"),
):
    if not get_settings().is_dev_auth:
        return redirect("/auth/sign-in")
    rate_limit(request, "login", 10, 600)
    email = normalize_email(email)
    if "@" not in email:
        return render(request, "auth/signin_dev.html", status=422,
                      error="Enter a valid email address.", next=_safe_next(next))

    code = f"{secrets.randbelow(1_000_000):06d}"
    db.add(LoginCode(
        email=email, code_hash=sha256_hex(code),
        expires_at=utcnow() + timedelta(minutes=get_settings().login_code_ttl_minutes),
    ))
    queue_email(db, "login_code", email, "Your Exponential sign-in code",
                f"Your Exponential sign-in code is {code}. It expires in "
                f"{get_settings().login_code_ttl_minutes} minutes.\n\n"
                "If you didn't request this, you can ignore it.")
    db.commit()
    # DEV ADAPTER ONLY: when emails print to console, surface the code so local
    # development and the staging demo work without a mail provider.
    show_code = code if get_settings().email_provider == "console" else None
    return render(request, "auth/verify_dev.html", email=email, next=_safe_next(next),
                  dev_code=show_code)


@router.post("/auth/verify")
async def sign_in_verify(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    _=Depends(verify_csrf),
    email: str = Form(""),
    code: str = Form(""),
    next: str = Form("/"),
):
    if not get_settings().is_dev_auth:
        return redirect("/auth/sign-in")
    rate_limit(request, "verify", 12, 600)
    email = normalize_email(email)
    row = db.scalar(
        select(LoginCode)
        .where(LoginCode.email == email, LoginCode.used == False)
        .order_by(LoginCode.created_at.desc())
    )
    ok = bool(row) and row.expires_at > utcnow() and row.code_hash == sha256_hex(code.strip())
    if row:
        row.attempts += 1
        if not ok and row.attempts >= 6:
            row.used = True  # brute-force guard
        if ok:
            row.used = True
    if not ok:
        db.commit()
        return render(request, "auth/verify_dev.html", status=422, email=email,
                      next=_safe_next(next), error="That code didn't work — try again or request a new one.")
    user = _upsert_user(db, email)
    audit(db, "auth.sign_in", actor=user, details={"provider": "dev"})
    db.commit()
    resp = redirect(_safe_next(next) if next != "/" else "/members")
    create_session(resp, user)
    return resp


class ClerkPayload(BaseModel):
    token: str
    next: str = "/"


@router.post("/auth/clerk")
def clerk_exchange(payload: ClerkPayload, request: Request, db: Session = Depends(get_db)):
    if get_settings().auth_provider != "clerk":
        return JSONResponse({"error": "clerk not enabled"}, status_code=404)
    rate_limit(request, "clerk", 20, 600)
    try:
        claims = clerk_adp.verify_session_token(payload.token)
        email = clerk_adp.fetch_verified_email(claims["sub"])
    except Exception as e:  # noqa: BLE001 — any verification failure means 401
        log.warning("clerk exchange failed: %s", e)
        return JSONResponse({"error": "Sign-in could not be verified."}, status_code=401)
    user = _upsert_user(db, email, clerk_user_id=claims["sub"])
    audit(db, "auth.sign_in", actor=user, details={"provider": "clerk"})
    db.commit()
    resp = JSONResponse({"ok": True, "next": _safe_next(payload.next)})
    create_session(resp, user)
    return resp


@router.post("/auth/sign-out")
async def sign_out(request: Request, _=Depends(verify_csrf)):
    resp = redirect("/")
    clear_session(resp)
    return resp


# ---------- invitation claim ----------

@router.post("/invite/{token}/claim")
async def claim_invite(
    token: str,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    _=Depends(verify_csrf),
):
    # Capacity lock serializes this against approvals and other redemptions.
    admissions.lock_cap(db)
    inv = db.scalar(
        select(Invitation).where(Invitation.token_hash == sha256_hex(token)).with_for_update()
    ) if db.bind.dialect.name != "sqlite" else db.scalar(
        select(Invitation).where(Invitation.token_hash == sha256_hex(token))
    )
    if not inv:
        return redirect("/", error="That invitation link isn't valid.")
    if inv.status == "redeemed":
        return redirect("/members", notice="That invitation was already used — you're signed in.")
    if inv.status == "revoked":
        return render(request, "public/invite.html", status=410, state="revoked")
    if inv.expires_at <= utcnow():
        return render(request, "public/invite.html", status=410, state="expired", email=inv.email)
    if normalize_email(user.email) != normalize_email(inv.email):
        return render(request, "public/invite.html", status=403, state="mismatch",
                      invited_email=inv.email, signed_in_email=user.email, token=token)

    # No capacity check here: the pending invitation already holds a seat, so
    # redemption only converts it — seats_used stays constant. Capacity is
    # enforced when invitations are created and when statuses are reactivated.
    member = db.get(Member, user.id) or Member(user_id=user.id)
    if member.status not in ("invited", "active"):
        member.status = "invited"
    member.invited_via_id = inv.id
    if not db.get(Member, user.id):
        db.add(member)
    inv.status = "redeemed"
    inv.redeemed_by_id = user.id
    inv.redeemed_at = utcnow()
    audit(db, "invitation.redeemed", actor=user, target_type="invitation", target_id=inv.id,
          details={"email": inv.email})
    db.commit()
    return redirect("/onboarding")
