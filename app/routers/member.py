"""Member area: welcome, home, directory, profiles, settings, removal."""
import json

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import get_db
from ..images import ImageRejected, process_headshot
from ..models import Event, Member, Profile, RemovalRequest, User
from ..security import require_active_member, utcnow, verify_csrf
from ..services.admissions import get_setting
from ..storage import delete, put
from ..web import interest_tags, redirect, render

router = APIRouter()


def _next_events(db: Session) -> list[Event]:
    return list(db.scalars(
        select(Event)
        .where(Event.published == True, Event.starts_at >= utcnow())
        .order_by(Event.starts_at)
        .limit(5)
    ))


@router.get("/welcome")
def welcome(request: Request, user: User = Depends(require_active_member),
            db: Session = Depends(get_db)):
    return render(request, "member/welcome.html", db=db, user=user,
                  p=db.get(Profile, user.id), events=_next_events(db),
                  signal_url=get_setting(db, "signal_invite_url"))


@router.get("/members")
def member_home(request: Request, user: User = Depends(require_active_member),
                db: Session = Depends(get_db)):
    next_event = (_next_events(db) or [None])[0]
    recent_members = list(db.scalars(
        select(Profile)
        .join(Member, Member.user_id == Profile.user_id)
        .where(Member.status == "active", Profile.published == True,
               Profile.directory_visible == True, Profile.user_id != user.id)
        .order_by(Profile.created_at.desc())
        .limit(6)
    ))
    return render(request, "member/home.html", db=db, user=user,
                  p=db.get(Profile, user.id), next_event=next_event,
                  events=_next_events(db), recent=recent_members,
                  signal_url=get_setting(db, "signal_invite_url"))


@router.get("/events")
def events(request: Request, user: User = Depends(require_active_member),
           db: Session = Depends(get_db)):
    upcoming = _next_events(db)
    past = list(db.scalars(
        select(Event)
        .where(Event.published == True, Event.starts_at < utcnow())
        .order_by(Event.starts_at.desc())
        .limit(20)
    ))
    return render(request, "member/events.html", db=db, user=user,
                  upcoming=upcoming, past=past)


# ---------- directory ----------

def _directory_query(db: Session, q: str, interest: str, expertise: str):
    stmt = (
        select(Profile)
        .join(Member, Member.user_id == Profile.user_id)
        .where(Member.status == "active", Profile.published == True,
               Profile.directory_visible == True)
    )
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(
            Profile.display_name.ilike(like),
            Profile.organization.ilike(like),
            Profile.headline.ilike(like),
            Profile.bio.ilike(like),
            Profile.working_on.ilike(like),
            cast(Profile.interests, String).ilike(like),
            cast(Profile.expertise, String).ilike(like),
        ))
    if interest:
        stmt = stmt.where(cast(Profile.interests, String).ilike(f'%"{interest}"%'))
    if expertise:
        stmt = stmt.where(cast(Profile.expertise, String).ilike(f'%"{expertise}"%'))
    return stmt.order_by(Profile.display_name)


@router.get("/directory")
def directory(request: Request, q: str = "", interest: str = "", expertise: str = "",
              user: User = Depends(require_active_member), db: Session = Depends(get_db)):
    profiles = list(db.scalars(_directory_query(db, q.strip(), interest, expertise)))
    total = db.scalar(
        select(func.count()).select_from(Profile)
        .join(Member, Member.user_id == Profile.user_id)
        .where(Member.status == "active", Profile.published == True,
               Profile.directory_visible == True))
    return render(request, "member/directory.html", db=db, user=user, profiles=profiles,
                  q=q, interest=interest, expertise=expertise,
                  suggested_tags=interest_tags(db), total=total)


@router.get("/directory/{user_id}")
def directory_profile(user_id: str, request: Request,
                      user: User = Depends(require_active_member), db: Session = Depends(get_db)):
    p = db.get(Profile, user_id)
    m = db.get(Member, user_id)
    if not p or not m or m.status != "active" or not p.published:
        return render(request, "member/profile.html", status=404, db=db, user=user, missing=True)
    if not p.directory_visible and user_id != user.id:
        return render(request, "member/profile.html", status=404, db=db, user=user, missing=True)
    return render(request, "member/profile.html", db=db, user=user, p=p,
                  owner=db.get(User, user_id), self_view=(user_id == user.id))


# ---------- own profile ----------

@router.get("/members/profile")
def edit_profile(request: Request, user: User = Depends(require_active_member),
                 db: Session = Depends(get_db)):
    return render(request, "member/edit_profile.html", db=db, user=user,
                  p=db.get(Profile, user.id), suggested_tags=interest_tags(db))


@router.post("/members/profile")
async def save_profile(
    request: Request,
    user: User = Depends(require_active_member),
    db: Session = Depends(get_db),
    _=Depends(verify_csrf),
):
    form = await request.form()
    p = db.get(Profile, user.id) or Profile(user_id=user.id)
    if not db.get(Profile, user.id):
        db.add(p)
    for f in ("display_name", "headline", "role", "organization", "working_on", "bio",
              "come_to_me_for", "like_to_meet", "outside_ai", "website", "linkedin"):
        setattr(p, f, str(form.get(f, "")).strip())
    p.interests = [t.strip() for t in form.getlist("interests") if t.strip()][:14]
    p.expertise = [t.strip() for t in str(form.get("expertise", "")).split(",") if t.strip()][:10]
    labels, urls = form.getlist("contact_label"), form.getlist("contact_url")
    p.contact_links = [
        {"label": l.strip()[:40], "url": u.strip()[:300]}
        for l, u in zip(labels, urls) if l.strip() and u.strip()
    ][:6]
    for b in ("show_email", "directory_visible", "willing_host", "willing_present", "willing_organize"):
        setattr(p, b, b in form)
    db.commit()
    return redirect("/members/profile", notice="Profile saved.")


@router.post("/members/photo")
async def change_photo(
    request: Request,
    photo: UploadFile,
    user: User = Depends(require_active_member),
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
        return redirect("/members/profile", error=str(e))
    put(key, blob, "image/jpeg")
    p = db.get(Profile, user.id)
    old, p.photo_key = p.photo_key, key
    db.commit()
    if old:
        delete(old)
    return redirect("/members/profile", notice="Photo updated.")


# ---------- removal ----------

@router.post("/members/removal")
async def request_removal(
    request: Request,
    user: User = Depends(require_active_member),
    db: Session = Depends(get_db),
    _=Depends(verify_csrf),
    reason: str = Form(""),
):
    existing = db.scalar(select(RemovalRequest).where(
        RemovalRequest.user_id == user.id, RemovalRequest.status == "open"))
    if not existing:
        db.add(RemovalRequest(user_id=user.id, email=user.email, reason=reason.strip()[:2000]))
        audit(db, "removal.requested", actor=user, target_type="user", target_id=user.id)
        db.commit()
    return redirect("/members/profile",
                    notice="Your removal request is in — an organizer will confirm shortly.")
