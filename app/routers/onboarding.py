"""Member onboarding: draft profile, photo, norms, preview, publish."""
import json

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..db import get_db
from ..emailer import queue_email
from ..images import ImageRejected, monogram_avatar, process_headshot
from ..models import Member, Profile, User
from ..security import clean_text, normalize_url, require_member, utcnow, verify_csrf
from ..services import admissions
from ..storage import put
from ..web import interest_tags, redirect, render

router = APIRouter()

REQUIRED = ("display_name", "headline", "working_on", "linkedin")
REQUIRED_LABELS = {"display_name": "your name", "headline": "a one-line intro",
                   "working_on": "what you're working on", "linkedin": "your LinkedIn URL"}

STEP_FIELDS = {
    1: ("display_name", "headline", "role", "organization"),
    3: ("working_on", "bio", "come_to_me_for", "like_to_meet", "outside_ai"),
    4: ("website", "linkedin", "show_email"),
}

PROFILE_LIMITS = {
    "display_name": 120,
    "headline": 200,
    "role": 120,
    "organization": 120,
    "website": 300,
    "linkedin": 300,
}

PROFILE_LABELS = {
    "display_name": "Your name",
    "headline": "Your one-line introduction",
    "role": "Your role",
    "organization": "Your organization",
    "website": "Your website",
    "linkedin": "Your LinkedIn URL",
}


def _profile(db: Session, user: User) -> Profile:
    p = db.get(Profile, user.id) or Profile(user_id=user.id)
    if not db.get(Profile, user.id):
        db.add(p)
    return p


def _progress(p: Profile) -> int:
    done = sum(bool(getattr(p, f, "")) for f in REQUIRED) + bool(p.norms_accepted_at)
    return round(done / (len(REQUIRED) + 1) * 100)


@router.get("/onboarding")
def onboarding(step: int = 1, request: Request = None,
               user: User = Depends(require_member), db: Session = Depends(get_db)):
    p = _profile(db, user)
    step = max(1, min(4, step))
    return render(request, "onboarding/step.html", db=db, user=user, p=p, step=step,
                  progress=_progress(p), suggested_tags=interest_tags(db))


@router.post("/onboarding/save")
async def onboarding_save(
    request: Request,
    user: User = Depends(require_member),
    db: Session = Depends(get_db),
    _=Depends(verify_csrf),
    step: int = Form(1),
):
    form = await request.form()
    p = _profile(db, user)
    for field in STEP_FIELDS.get(step, ()):
        if field == "show_email":
            p.show_email = "show_email" in form
        else:
            value = clean_text(str(form.get(field, ""))).strip()
            if field == "linkedin":
                value = normalize_url(value)
            limit = PROFILE_LIMITS.get(field)
            if limit is not None and len(value) > limit:
                return redirect(
                    f"/onboarding?step={step}",
                    error=f"{PROFILE_LABELS[field]} must be {limit} characters or fewer.",
                )
            setattr(p, field, value)
    if step == 3:
        p.interests = [
            clean_text(t).strip() for t in form.getlist("interests") if clean_text(t).strip()
        ][:12]
        extra = [
            clean_text(t).strip()
            for t in str(form.get("extra_interests", "")).split(",")
            if clean_text(t).strip()
        ]
        p.interests = list(dict.fromkeys(p.interests + extra))[:14]
        p.expertise = [
            clean_text(t).strip()
            for t in str(form.get("expertise", "")).split(",")
            if clean_text(t).strip()
        ][:10]
        p.willing_host = "willing_host" in form
        p.willing_present = "willing_present" in form
        p.willing_organize = "willing_organize" in form
    if step == 4:
        labels, urls = form.getlist("contact_label"), form.getlist("contact_url")
        p.contact_links = []
        for label, url in zip(labels, urls):
            label, url = clean_text(label).strip(), clean_text(url).strip()
            if label and url:
                p.contact_links.append({"label": label[:40], "url": url[:300]})
            if len(p.contact_links) == 6:
                break
        if "norms_accepted" in form and not p.norms_accepted_at:
            p.norms_accepted_at = utcnow()
        if "norms_accepted" not in form:
            p.norms_accepted_at = None
    p.onboarding_step = max(p.onboarding_step or 0, step)
    db.commit()
    return redirect(f"/onboarding?step={min(step + 1, 4)}" if step < 4 else "/onboarding/preview")


@router.post("/onboarding/photo")
async def onboarding_photo(
    request: Request,
    photo: UploadFile,
    user: User = Depends(require_member),
    db: Session = Depends(get_db),
    _=Depends(verify_csrf),
):
    form = await request.form()
    data = await photo.read()
    crop = None
    if form.get("crop"):
        try:
            c = json.loads(str(form["crop"]))
            crop = (int(c["x"]), int(c["y"]), int(c["w"]), int(c["h"]))
        except (ValueError, KeyError, TypeError):
            crop = None
    try:
        key, blob = process_headshot(data, crop)
    except ImageRejected as e:
        return redirect("/onboarding?step=2", error=str(e))
    put(key, blob, "image/jpeg")
    p = _profile(db, user)
    old, p.photo_key = p.photo_key, key
    db.commit()
    if old:
        from ..storage import delete
        delete(old)
    return redirect("/onboarding?step=2", notice="Photo updated.")


@router.get("/onboarding/preview")
def onboarding_preview(request: Request, user: User = Depends(require_member),
                       db: Session = Depends(get_db)):
    p = _profile(db, user)
    missing = [REQUIRED_LABELS.get(f, f) for f in REQUIRED if not getattr(p, f, "")]
    if not p.norms_accepted_at:
        missing.append("accepting the community norms")
    return render(request, "onboarding/preview.html", db=db, user=user, p=p,
                  missing=missing, progress=_progress(p))


@router.post("/onboarding/publish")
async def onboarding_publish(
    request: Request,
    user: User = Depends(require_member),
    db: Session = Depends(get_db),
    _=Depends(verify_csrf),
):
    p = _profile(db, user)
    missing = [f for f in REQUIRED if not getattr(p, f, "")] \
        + ([] if p.norms_accepted_at else ["norms"])
    if missing:
        return redirect("/onboarding/preview", error="A few required pieces are still missing.")
    if not p.photo_key:
        key, blob = monogram_avatar(p.display_name or user.email)
        put(key, blob, "image/jpeg")
        p.photo_key = key
    p.published = True
    member = db.get(Member, user.id)
    first_publish = member and member.status != "active"
    if member:
        admissions.set_member_status(db, member, "active")
    audit(db, "member.activated", actor=user, target_type="member", target_id=user.id)
    queue_email(
        db, "welcome", user.email, "Welcome to Exponential",
        f"Hi {p.display_name or 'there'},\n\nYour profile is live and you're a member of "
        f"Exponential. A few places to start:\n\n"
        f"· Member home: {get_settings().app_base_url}/members\n"
        f"· Directory: {get_settings().app_base_url}/directory\n\nSee you at a gathering soon.\n\n— Exponential",
    )
    db.commit()
    return redirect("/welcome" if first_publish else "/members",
                    notice="Welcome — your profile is live.")
