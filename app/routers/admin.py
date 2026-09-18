"""Admin dashboard: admissions, invitations, members, events, settings, email,
audit log, CSV export, removal requests."""
import csv
import io
import json
import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, or_, select
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..db import get_db
from ..emailer import queue_email, retry
from ..images import ImageRejected, process_event_image
from ..models import (
    APPLICATION_STATUSES,
    Application,
    AuditEvent,
    EmailMessage,
    Event,
    Invitation,
    Member,
    PrivateNote,
    Profile,
    RemovalRequest,
    User,
)
from ..security import normalize_email, require_admin, sha256_hex, utcnow, verify_csrf
from ..services import admissions
from ..services.admissions import get_setting, set_setting
from ..storage import put
from ..web import redirect, render

router = APIRouter()
NY = ZoneInfo("America/New_York")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

Admin = Depends(require_admin)


# ---------- dashboard ----------

@router.get("/admin")
def dashboard(request: Request, user: User = Admin, db: Session = Depends(get_db)):
    stats = {
        "cap": admissions.get_cap(db),
        "seats": admissions.seats_used(db),
        "active": len(list(db.scalars(select(Member).where(Member.status == "active")))),
        "outstanding_invites": admissions.outstanding_invitations(db),
        "new_applications": len(list(db.scalars(
            select(Application).where(Application.status == "new")))),
        "failed_emails": len(list(db.scalars(
            select(EmailMessage).where(EmailMessage.status == "failed")))),
        "open_removals": len(list(db.scalars(
            select(RemovalRequest).where(RemovalRequest.status == "open")))),
    }
    return render(request, "admin/dashboard.html", db=db, user=user, stats=stats)


# ---------- applications ----------

@router.get("/admin/applications")
def applications(request: Request, status: str = "", referrer: str = "",
                 user: User = Admin, db: Session = Depends(get_db)):
    stmt = select(Application).order_by(desc(Application.created_at))
    if status in APPLICATION_STATUSES:
        stmt = stmt.where(Application.status == status)
    if referrer:
        stmt = stmt.where(Application.referrer.ilike(f"%{referrer}%"))
    rows = list(db.scalars(stmt))
    return render(request, "admin/applications.html", db=db, user=user, rows=rows,
                  status_filter=status, referrer=referrer, statuses=APPLICATION_STATUSES)


@router.get("/admin/applications/{app_id}")
def application_detail(app_id: str, request: Request, user: User = Admin,
                       db: Session = Depends(get_db)):
    a = db.get(Application, app_id)
    if not a:
        return redirect("/admin/applications", error="Application not found.")
    notes = list(db.scalars(select(PrivateNote).where(
        PrivateNote.subject_type == "application", PrivateNote.subject_id == app_id)
        .order_by(PrivateNote.created_at)))
    invites = list(db.scalars(select(Invitation).where(Invitation.application_id == app_id)
                              .order_by(desc(Invitation.created_at))))
    return render(request, "admin/application_detail.html", db=db, user=user, a=a,
                  notes=notes, invites=invites)


@router.post("/admin/applications/{app_id}/status")
async def application_status(app_id: str, request: Request, user: User = Admin,
                             db: Session = Depends(get_db), _=Depends(verify_csrf)):

    a = db.get(Application, app_id)
    if not a:
        return redirect("/admin/applications", error="Application not found.")
    form = await request.form()
    new_status = str(form.get("status", ""))
    if new_status not in ("new", "waitlisted", "declined"):
        return redirect(f"/admin/applications/{app_id}", error="Unsupported status.")
    a.status = new_status
    a.decided_by_id, a.decided_at = user.id, utcnow()
    audit(db, f"application.{new_status}", actor=user, target_type="application",
          target_id=app_id, details={"email": a.email})
    db.commit()
    return redirect(f"/admin/applications/{app_id}", notice=f"Marked {new_status}.")


@router.post("/admin/applications/{app_id}/gathering")
async def application_gathering(app_id: str, request: Request, user: User = Admin,
                                db: Session = Depends(get_db), _=Depends(verify_csrf)):

    a = db.get(Application, app_id)
    if a:
        a.suggest_gathering = not a.suggest_gathering
        audit(db, "application.gathering_flag", actor=user, target_type="application",
              target_id=app_id, details={"suggest_gathering": a.suggest_gathering})
        db.commit()
    return redirect(f"/admin/applications/{app_id}")


@router.post("/admin/applications/{app_id}/notes")
async def application_note(app_id: str, request: Request, user: User = Admin,
                           db: Session = Depends(get_db), _=Depends(verify_csrf),
                           body: str = Form("")):
    if body.strip():
        db.add(PrivateNote(subject_type="application", subject_id=app_id,
                           author_id=user.id, body=body.strip()[:4000]))
        audit(db, "note.added", actor=user, target_type="application", target_id=app_id)
        db.commit()
    return redirect(f"/admin/applications/{app_id}")


# ---------- invitations ----------

def _send_invite(db: Session, inv: Invitation, token: str) -> None:
    """Queue the invitation email containing the single-use link. The stored
    hash is rotated to the new token, so resending invalidates older links."""
    inv.token_hash = sha256_hex(token)
    inv.sent_at = utcnow()
    link = f"{get_settings().app_base_url}/invite/{token}"
    queue_email(
        db, "invitation", inv.email, "You're invited to Exponential",
        "Hello,\n\nYou've been invited to join Exponential — a New York community of "
        "people building, researching, and thoughtfully applying AI.\n\n"
        + (f"\nA note from your host:\n{inv.note}\n" if inv.note else "")
        + f"\nThis link is personal, single-use, and expires "
        f"{inv.expires_at.strftime('%B %-d, %Y')}:\n\n{link}\n\nWarmly,\nExponential",
        invitation_id=inv.id,
    )


@router.get("/admin/invitations")
def invitations(request: Request, user: User = Admin, db: Session = Depends(get_db)):
    rows = list(db.scalars(select(Invitation).order_by(desc(Invitation.created_at))))
    return render(request, "admin/invitations.html", db=db, user=user, rows=rows,
                  now=utcnow(), cap=admissions.get_cap(db),
                  seats=admissions.seats_used(db))


@router.post("/admin/invitations")
async def invitation_create(
    request: Request,
    user: User = Admin,
    db: Session = Depends(get_db),
    _=Depends(verify_csrf),
    email: str = Form(""),
    note: str = Form(""),
    application_id: str = Form(""),
):
    email = normalize_email(email)
    if not EMAIL_RE.match(email):
        return redirect("/admin/invitations", error="Enter a valid email address.")

    admissions.lock_cap(db)
    try:
        admissions.require_capacity(db)
    except admissions.CapacityError as e:
        db.rollback()
        return redirect("/admin/invitations", error=str(e))

    # if there's an open invitation for this email, resend rather than duplicate
    existing = db.scalar(select(Invitation).where(
        Invitation.email == email, Invitation.status == "pending"))
    if existing and existing.expires_at > utcnow():
        _send_invite(db, existing, Invitation.new_token())
        audit(db, "invitation.resent", actor=user, target_type="invitation",
              target_id=existing.id, details={"email": email})
        db.commit()
        return redirect("/admin/invitations", notice="Resent the existing invitation.")

    inv = Invitation(
        email=email, note=note.strip()[:2000], expires_at=admissions.new_invitation_expiry(),
        created_by_id=user.id, application_id=application_id or None,
        token_hash="pending",
    )
    db.add(inv)
    db.flush()
    _send_invite(db, inv, Invitation.new_token())
    if application_id:
        app_row = db.get(Application, application_id)
        if app_row:
            app_row.status = "invited"
            app_row.decided_by_id, app_row.decided_at = user.id, utcnow()
    audit(db, "invitation.created", actor=user, target_type="invitation",
          target_id=inv.id, details={"email": email})
    db.commit()
    return redirect("/admin/invitations", notice=f"Invitation sent to {email}.")


@router.post("/admin/invitations/{inv_id}/revoke")
async def invitation_revoke(inv_id: str, request: Request, user: User = Admin,
                            db: Session = Depends(get_db), _=Depends(verify_csrf)):
    inv = db.get(Invitation, inv_id)
    if inv and inv.status == "pending":
        inv.status = "revoked"
        audit(db, "invitation.revoked", actor=user, target_type="invitation",
              target_id=inv_id, details={"email": inv.email})
        db.commit()
    return redirect("/admin/invitations", notice="Invitation revoked.")


@router.post("/admin/invitations/{inv_id}/resend")
async def invitation_resend(inv_id: str, request: Request, user: User = Admin,
                            db: Session = Depends(get_db), _=Depends(verify_csrf)):
    inv = db.get(Invitation, inv_id)
    if not inv or inv.status != "pending":
        return redirect("/admin/invitations", error="Only pending invitations can be resent.")
    if inv.expires_at <= utcnow():
        inv.expires_at = admissions.new_invitation_expiry()  # resend extends expiry
    _send_invite(db, inv, Invitation.new_token())
    audit(db, "invitation.resent", actor=user, target_type="invitation",
          target_id=inv_id, details={"email": inv.email})
    db.commit()
    return redirect("/admin/invitations", notice="Invitation resent.")


# ---------- members ----------

@router.get("/admin/members")
def members(request: Request, status: str = "", user: User = Admin,
            db: Session = Depends(get_db)):
    stmt = (select(Member, User, Profile)
            .join(User, User.id == Member.user_id)
            .outerjoin(Profile, Profile.user_id == Member.user_id)
            .order_by(desc(Member.created_at)))
    if status:
        stmt = stmt.where(Member.status == status)
    rows = list(db.execute(stmt))
    return render(request, "admin/members.html", db=db, user=user, rows=rows,
                  status_filter=status, cap=admissions.get_cap(db),
                  seats=admissions.seats_used(db),
                  outstanding=admissions.outstanding_invitations(db))


@router.get("/admin/members/{uid}")
def member_detail(uid: str, request: Request, user: User = Admin,
                  db: Session = Depends(get_db)):
    m, u, p = db.get(Member, uid), db.get(User, uid), db.get(Profile, uid)
    if not u:
        return redirect("/admin/members", error="Member not found.")
    notes = list(db.scalars(select(PrivateNote).where(
        PrivateNote.subject_type == "member", PrivateNote.subject_id == uid)
        .order_by(PrivateNote.created_at)))
    invites = list(db.scalars(select(Invitation).where(Invitation.email == u.email)
                              .order_by(desc(Invitation.created_at))))
    return render(request, "admin/member_detail.html", db=db, user=user,
                  m=m, u=u, p=p, notes=notes, invites=invites)


@router.post("/admin/members/{uid}/status")
async def member_status(uid: str, request: Request, user: User = Admin,
                        db: Session = Depends(get_db), _=Depends(verify_csrf),
                        status: str = Form("")):
    if status not in ("invited", "active", "paused", "alumni", "declined"):
        return redirect(f"/admin/members/{uid}", error="Unsupported status.")
    m = db.get(Member, uid)
    if not m:
        # direct grant to an account holder with no member row yet
        if not db.get(User, uid):
            return redirect("/admin/members", error="Member not found.")
        m = Member(user_id=uid, status="invited")
        db.add(m)
        db.flush()
    admissions.lock_cap(db)
    if status in ("invited", "active") and m.status not in ("invited", "active"):
        try:
            admissions.require_capacity(db)
        except admissions.CapacityError as e:
            db.rollback()
            return redirect(f"/admin/members/{uid}", error=str(e))
    old = m.status
    admissions.set_member_status(db, m, status, actor=user)
    audit(db, "member.status_changed", actor=user, target_type="member", target_id=uid,
          details={"from": old, "to": status})
    if status in ("paused", "alumni", "declined"):
        # end existing sessions' member access immediately
        u = db.get(User, uid)
        if u:
            u.session_version += 1
    db.commit()
    return redirect(f"/admin/members/{uid}", notice=f"Status set to {status}.")


@router.post("/admin/members/{uid}/fields")
async def member_fields(uid: str, request: Request, user: User = Admin,
                        db: Session = Depends(get_db), _=Depends(verify_csrf),
                        welcome_cohost: str = Form(""), needs_followup: str = Form(""),
                        followup_note: str = Form("")):
    m = db.get(Member, uid)
    if m:
        m.welcome_cohost = welcome_cohost.strip()[:120]
        m.needs_followup = needs_followup == "on"
        m.followup_note = followup_note.strip()[:2000]
        audit(db, "member.fields_updated", actor=user, target_type="member", target_id=uid)
        db.commit()
    return redirect(f"/admin/members/{uid}", notice="Saved.")


@router.post("/admin/members/{uid}/notes")
async def member_note(uid: str, request: Request, user: User = Admin,
                      db: Session = Depends(get_db), _=Depends(verify_csrf),
                      body: str = Form("")):
    if body.strip():
        db.add(PrivateNote(subject_type="member", subject_id=uid,
                           author_id=user.id, body=body.strip()[:4000]))
        audit(db, "note.added", actor=user, target_type="member", target_id=uid)
        db.commit()
    return redirect(f"/admin/members/{uid}")


@router.post("/admin/members/{uid}/toggle-directory")
async def member_toggle_directory(uid: str, request: Request, user: User = Admin,
                                  db: Session = Depends(get_db), _=Depends(verify_csrf)):
    p = db.get(Profile, uid)
    if p:
        p.directory_visible = not p.directory_visible
        audit(db, "profile.moderated", actor=user, target_type="member", target_id=uid,
              details={"directory_visible": p.directory_visible})
        db.commit()
    return redirect(f"/admin/members/{uid}")


@router.post("/admin/members/{uid}/make-admin")
async def member_make_admin(uid: str, request: Request, user: User = Admin,
                            db: Session = Depends(get_db), _=Depends(verify_csrf)):
    """Only an existing admin can grant admin. Users can never grant it to
    themselves via any member-facing route."""
    u = db.get(User, uid)
    if u:
        u.is_admin = not u.is_admin
        audit(db, "admin.toggled", actor=user, target_type="user", target_id=uid,
              details={"is_admin": u.is_admin})
        db.commit()
    return redirect(f"/admin/members/{uid}")


@router.post("/admin/members/{uid}/delete")
async def member_delete(uid: str, request: Request, user: User = Admin,
                        db: Session = Depends(get_db), _=Depends(verify_csrf)):
    """Hard-delete account + profile + photo. Audit and email records are
    retained (documented in README)."""
    u = db.get(User, uid)
    if not u:
        return redirect("/admin/members", error="Member not found.")
    p = db.get(Profile, uid)
    if p and p.photo_key:
        from ..storage import delete
        delete(p.photo_key)
    # admissions notes ABOUT them go with the profile; notes/authorship and
    # invitation/audit history keep their rows with actor refs nulled — the
    # audit log retains actor_email for the record.
    for n in db.scalars(select(PrivateNote).where(
            PrivateNote.subject_type == "member", PrivateNote.subject_id == uid)):
        db.delete(n)
    for n in db.scalars(select(PrivateNote).where(PrivateNote.author_id == uid)):
        n.author_id = None
    for inv in db.scalars(select(Invitation).where(
            or_(Invitation.created_by_id == uid, Invitation.redeemed_by_id == uid))):
        if inv.created_by_id == uid:
            inv.created_by_id = None
        if inv.redeemed_by_id == uid:
            inv.redeemed_by_id = None
    for rr in db.scalars(select(RemovalRequest).where(RemovalRequest.user_id == uid)):
        db.delete(rr)
    for ev in db.scalars(select(AuditEvent).where(AuditEvent.actor_user_id == uid)):
        ev.actor_user_id = None
    if p:
        db.delete(p)
    if m := db.get(Member, uid):
        db.delete(m)
    audit(db, "member.deleted", actor=user, target_type="member", target_id=uid,
          details={"email": u.email})
    db.delete(u)
    db.commit()
    return redirect("/admin/members", notice="Account and profile deleted.")


# ---------- events ----------

@router.get("/admin/events")
def events(request: Request, user: User = Admin, db: Session = Depends(get_db)):
    rows = list(db.scalars(select(Event).order_by(desc(Event.starts_at))))
    return render(request, "admin/events.html", db=db, user=user, rows=rows)


@router.post("/admin/events")
async def event_create(
    request: Request, user: User = Admin, db: Session = Depends(get_db),
    _=Depends(verify_csrf),
    title: str = Form(""), date: str = Form(""), time_: str = Form("", alias="time"),
    tz: str = Form("America/New_York"), description: str = Form(""),
    host: str = Form(""), external_url: str = Form(""),
    image: UploadFile | None = None,
):
    if not title.strip() or not date or not time_:
        return redirect("/admin/events", error="Title, date, and time are required.")
    try:
        local_dt = datetime.strptime(f"{date} {time_}", "%Y-%m-%d %H:%M").replace(
            tzinfo=ZoneInfo(tz or "America/New_York"))
    except ValueError:
        return redirect("/admin/events", error="Couldn't parse that date/time.")
    ev = Event(title=title.strip(), starts_at=local_dt.astimezone(UTC),
               tz_label=tz or "America/New_York", description=description.strip(),
               host=host.strip(), external_url=external_url.strip())
    db.add(ev)
    db.flush()
    if image and image.filename:
        try:
            key, blob = process_event_image(await image.read())
            put(key, blob, "image/jpeg")
            ev.image_key = key
        except ImageRejected as e:
            return redirect("/admin/events", error=str(e))
    audit(db, "event.created", actor=user, target_type="event", target_id=ev.id,
          details={"title": ev.title})
    db.commit()
    return redirect("/admin/events", notice="Event created.")


@router.post("/admin/events/{event_id}/edit")
async def event_edit(
    event_id: str, request: Request, user: User = Admin,
    db: Session = Depends(get_db), _=Depends(verify_csrf),
    title: str = Form(""), date: str = Form(""), time_: str = Form("", alias="time"),
    tz: str = Form("America/New_York"), description: str = Form(""),
    host: str = Form(""), external_url: str = Form(""), published: str = Form(""),
    image: UploadFile | None = None,
):
    ev = db.get(Event, event_id)
    if not ev:
        return redirect("/admin/events", error="Event not found.")
    try:
        local_dt = datetime.strptime(f"{date} {time_}", "%Y-%m-%d %H:%M").replace(
            tzinfo=ZoneInfo(tz or "America/New_York"))
        ev.starts_at = local_dt.astimezone(UTC)
    except ValueError:
        pass
    ev.title, ev.description = title.strip(), description.strip()
    ev.host, ev.external_url = host.strip(), external_url.strip()
    ev.tz_label = tz or "America/New_York"
    ev.published = published == "on"
    if image and image.filename:
        try:
            key, blob = process_event_image(await image.read())
            put(key, blob, "image/jpeg")
            if ev.image_key:
                from ..storage import delete
                delete(ev.image_key)
            ev.image_key = key
        except ImageRejected as e:
            return redirect("/admin/events", error=str(e))
    audit(db, "event.updated", actor=user, target_type="event", target_id=event_id)
    db.commit()
    return redirect("/admin/events", notice="Event saved.")


# ---------- settings ----------

@router.get("/admin/settings")
def settings_page(request: Request, user: User = Admin, db: Session = Depends(get_db)):
    return render(request, "admin/settings.html", db=db, user=user,
                  cap=admissions.get_cap(db), seats=admissions.seats_used(db),
                  signal_url=get_setting(db, "signal_invite_url"),
                  interest_tags=get_setting(db, "interest_tags"))


@router.post("/admin/settings")
async def settings_save(request: Request, user: User = Admin,
                        db: Session = Depends(get_db), _=Depends(verify_csrf),
                        member_cap: str = Form(""), signal_invite_url: str = Form(""),
                        interest_tags: str = Form("")):
    if member_cap.strip():
        try:
            cap = int(member_cap)
            if cap < 1:
                raise ValueError
        except ValueError:
            return redirect("/admin/settings", error="Cap must be a positive number.")
        admissions.lock_cap(db)
        # allow lowering but never below current seats in use
        if cap < admissions.seats_used(db):
            db.rollback()
            return redirect("/admin/settings",
                            error=f"{admissions.seats_used(db)} seats are already in use.")
        set_setting(db, "member_cap", str(cap))
        audit(db, "settings.cap_changed", actor=user, details={"cap": cap})
    set_setting(db, "signal_invite_url", signal_invite_url.strip()[:500])
    if interest_tags.strip():
        try:
            tags = json.loads(interest_tags)
        except json.JSONDecodeError:
            tags = None
        if not isinstance(tags, list):
            tags = [t.strip() for t in interest_tags.split(",") if t.strip()]
        set_setting(db, "interest_tags", json.dumps(tags[:60]))
        audit(db, "settings.tags_changed", actor=user)
    db.commit()
    return redirect("/admin/settings", notice="Settings saved.")


# ---------- email outbox ----------

@router.get("/admin/email")
def email_outbox(request: Request, user: User = Admin, db: Session = Depends(get_db)):
    rows = list(db.scalars(select(EmailMessage).order_by(desc(EmailMessage.created_at)).limit(200)))
    return render(request, "admin/email.html", db=db, user=user, rows=rows)


@router.post("/admin/email/{msg_id}/retry")
async def email_retry(msg_id: str, request: Request, user: User = Admin,
                      db: Session = Depends(get_db), _=Depends(verify_csrf)):
    msg = db.get(EmailMessage, msg_id)
    if msg:
        retry(db, msg)
        audit(db, "email.retried", actor=user, target_type="email", target_id=msg_id,
              details={"status": msg.status})
        db.commit()
    return redirect("/admin/email")


# ---------- audit log ----------

@router.get("/admin/audit")
def audit_log(request: Request, user: User = Admin, db: Session = Depends(get_db)):
    rows = list(db.scalars(select(AuditEvent).order_by(desc(AuditEvent.created_at)).limit(500)))
    return render(request, "admin/audit.html", db=db, user=user, rows=rows)


# ---------- removal requests ----------

@router.post("/admin/removals/{req_id}/done")
async def removal_done(req_id: str, request: Request, user: User = Admin,
                       db: Session = Depends(get_db), _=Depends(verify_csrf)):
    r = db.get(RemovalRequest, req_id)
    if r:
        r.status = "done"
        audit(db, "removal.completed", actor=user, target_type="removal", target_id=req_id)
        db.commit()
    return redirect("/admin")


# ---------- CSV exports ----------

def _csv(filename: str, rows: list[list]) -> StreamingResponse:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerows(rows)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"',
                 "Cache-Control": "private, no-store"})


@router.get("/admin/export/members.csv")
def export_members(user: User = Admin, db: Session = Depends(get_db)):
    rows = [["email", "name", "status", "welcome_cohost", "needs_followup",
             "approved_at", "created_at"]]
    for m, u, p in db.execute(
            select(Member, User, Profile)
            .join(User, User.id == Member.user_id)
            .outerjoin(Profile, Profile.user_id == Member.user_id)):
        rows.append([u.email, p.display_name if p else "", m.status, m.welcome_cohost,
                     m.needs_followup, m.approved_at or "", m.created_at])
    audit(db, "export.members", actor=user)
    db.commit()
    return _csv("members.csv", rows)


@router.get("/admin/export/applications.csv")
def export_applications(user: User = Admin, db: Session = Depends(get_db)):

    rows = [["name", "email", "role_company", "link", "referrer", "status",
             "suggest_gathering", "created_at"]]
    for a in db.scalars(select(Application)):
        rows.append([a.name, a.email, a.role_company, a.link, a.referrer,
                     a.status, a.suggest_gathering, a.created_at])
    audit(db, "export.applications", actor=user)
    db.commit()
    return _csv("applications.csv", rows)
