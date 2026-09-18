"""Public surface: homepage, expression of interest, invitation landing."""
import re

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import get_db
from ..emailer import queue_email
from ..models import Application, Invitation
from ..security import normalize_email, rate_limit, sha256_hex, utcnow, verify_csrf
from ..web import redirect, render

router = APIRouter()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@router.get("/")
def home(request: Request):
    return render(request, "public/home.html")


@router.get("/apply")
def apply_form(request: Request):
    return render(request, "public/apply.html")


@router.post("/apply")
async def apply_submit(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(verify_csrf),
    name: str = Form(""),
    email: str = Form(""),
    role_company: str = Form(""),
    link: str = Form(""),
    working_on: str = Form(""),
    why_join: str = Form(""),
    referrer: str = Form(""),
):
    rate_limit(request, "apply", 5, 600)
    name, email = name.strip(), normalize_email(email)
    errors = []
    if not name:
        errors.append("Please tell us your name.")
    if not EMAIL_RE.match(email):
        errors.append("Please use a valid email address.")
    if not working_on.strip():
        errors.append("Tell us a little about what you're working on or exploring.")
    if not why_join.strip():
        errors.append("Tell us why you'd like to join.")
    if errors:
        return render(request, "public/apply.html", status=422, errors=errors, form={
            "name": name, "email": email, "role_company": role_company, "link": link,
            "working_on": working_on, "why_join": why_join, "referrer": referrer,
        })

    app_row = Application(
        name=name, email=email, role_company=role_company.strip(), link=link.strip(),
        working_on=working_on.strip(), why_join=why_join.strip(), referrer=referrer.strip(),
    )
    db.add(app_row)
    audit(db, "application.submitted", target_type="application", target_id=app_row.id,
          details={"email": email, "referrer": referrer.strip()})
    db.flush()
    queue_email(
        db, "application_ack", email,
        "We received your note — Exponential",
        f"Hi {name},\n\nThank you for expressing interest in Exponential. "
        "We read every application by hand, and we'll be in touch if it seems like a fit — "
        "sometimes starting with an invitation to a salon or dinner.\n\nWarmly,\nExponential",
    )
    db.commit()
    return redirect("/apply/done")


@router.get("/apply/done")
def apply_done(request: Request):
    return render(request, "public/apply_done.html")


@router.get("/invite/{token}")
def invite_landing(token: str, request: Request, db: Session = Depends(get_db)):
    inv = db.scalar(select(Invitation).where(Invitation.token_hash == sha256_hex(token)))
    if not inv:
        return render(request, "public/invite.html", status=404, state="invalid")
    if inv.status == "revoked":
        return render(request, "public/invite.html", status=410, state="revoked")
    if inv.status == "redeemed":
        return render(request, "public/invite.html", state="redeemed")
    if inv.expires_at <= utcnow():
        return render(request, "public/invite.html", status=410, state="expired", email=inv.email)
    return render(request, "public/invite.html", state="valid", email=inv.email,
                  note=inv.note, token=token)


@router.get("/gatherings")
def gatherings_teaser(request: Request):
    # public teaser only: no dates, hosts, or addresses
    return render(request, "public/gatherings.html")


@router.get("/favicon.ico", include_in_schema=False)
def favicon():
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/static/favicon.svg")
