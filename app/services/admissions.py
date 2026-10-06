"""Capacity + membership transitions.

The member cap is a settings row. Every path that consumes a seat (admin
approval, invitation creation, invitation redemption) locks the cap row first
and re-counts inside the same transaction, so concurrent grants cannot
overshoot. On SQLite we take the write lock with a no-op UPDATE; on Postgres
we SELECT ... FOR UPDATE.
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    SEAT_STATUSES,
    Application,
    Invitation,
    Member,
    Profile,
    Setting,
    User,
    utcnow,
)


class CapacityError(RuntimeError):
    pass


class AlreadyMemberError(RuntimeError):
    pass


def grant_membership(
    db: Session,
    *,
    email: str,
    actor: User,
    name: str = "",
    linkedin: str = "",
    headline: str = "",
    application: Application | None = None,
) -> tuple[User, Member]:
    lock_cap(db)

    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email)
        db.add(user)
        db.flush()

    member = db.get(Member, user.id)
    if member and member.status in SEAT_STATUSES:
        raise AlreadyMemberError(f"{email} is already a member.")

    db.execute(
        update(Invitation)
        .where(Invitation.email == email, Invitation.status == "pending")
        .values(status="revoked")
    )
    db.flush()
    require_capacity(db)

    if member is None:
        member = Member(user_id=user.id, status="invited")
        db.add(member)
        db.flush()
    set_member_status(db, member, "invited", actor=actor)
    member.approved_by_id = member.approved_by_id or actor.id
    member.approved_at = member.approved_at or utcnow()

    profile = db.get(Profile, user.id)
    if profile is None:
        profile = Profile(user_id=user.id)
        db.add(profile)
    if not profile.display_name and name:
        profile.display_name = name[:120]
    if not profile.linkedin and linkedin:
        profile.linkedin = linkedin[:300]
    if not profile.headline and headline:
        profile.headline = headline[:200]

    if application is not None:
        application.status = "member"
        application.decided_by_id = actor.id
        application.decided_at = utcnow()
        if application.score is not None and member.score is None:
            member.score = application.score
            member.score_source = application.score_source
            member.score_reason = application.score_reason
            member.scored_at = application.scored_at

    db.flush()
    return user, member


def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.get(Setting, key)
    return row.value if row else default


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))
    db.flush()


def get_cap(db: Session) -> int:
    raw = get_setting(db, "member_cap", "")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return get_settings().member_cap


def lock_cap(db: Session) -> None:
    """Serialize capacity decisions inside the current transaction."""
    if db.bind.dialect.name == "sqlite":
        db.execute(update(Setting).where(Setting.key == "member_cap").values(value=Setting.value))
        if db.get(Setting, "member_cap") is None:
            db.add(Setting(key="member_cap", value=str(get_settings().member_cap)))
            db.flush()
            db.execute(update(Setting).where(Setting.key == "member_cap").values(value=Setting.value))
    else:
        db.execute(select(Setting).where(Setting.key == "member_cap").with_for_update())


def seats_used(db: Session) -> int:
    """Members holding a seat + outstanding (pending, unexpired) invitations."""
    members = db.scalar(
        select(func.count()).select_from(Member).where(Member.status.in_(SEAT_STATUSES))
    ) or 0
    pending_invites = db.scalar(
        select(func.count())
        .select_from(Invitation)
        .where(Invitation.status == "pending", Invitation.expires_at > datetime.now(UTC))
    ) or 0
    return members + pending_invites


def outstanding_invitations(db: Session) -> int:
    return db.scalar(
        select(func.count())
        .select_from(Invitation)
        .where(Invitation.status == "pending", Invitation.expires_at > datetime.now(UTC))
    ) or 0


def require_capacity(db: Session, extra: int = 1) -> None:
    """Call AFTER lock_cap(). Raises if granting `extra` seats would exceed the cap."""
    if seats_used(db) + extra > get_cap(db):
        raise CapacityError(
            f"Member cap reached ({get_cap(db)}). Raise the cap in Admin → Settings first."
        )


def set_member_status(db: Session, member: Member, status: str, actor: User | None = None) -> Member:
    member.status = status
    member.updated_at = datetime.now(UTC)
    if status == "active":
        member.approved_at = member.approved_at or datetime.now(UTC)
        if actor:
            member.approved_by_id = actor.id
    db.flush()
    return member


def new_invitation_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(days=get_settings().invite_ttl_days)
