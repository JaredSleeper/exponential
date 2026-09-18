import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from .db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex


class UTCDateTime(TypeDecorator):
    """DateTime that always comes back timezone-aware (UTC), even from SQLite."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


# Membership states. `applicant`/`waitlisted`/`declined` live on Application;
# the Member row exists once a seat is in play (invitation redeemed or an
# admin grants status directly). `invited` = redeemed a seat, onboarding pending.
MEMBER_STATUSES = ("invited", "active", "paused", "alumni", "declined")
SEAT_STATUSES = ("invited", "active")  # consume a seat under the cap
APPLICATION_STATUSES = ("new", "waitlisted", "invited", "declined")


class User(Base):
    """Authentication identity. Having an account grants nothing by itself."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    clerk_user_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    session_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    member: Mapped["Member | None"] = relationship(
        back_populates="user", uselist=False, foreign_keys="Member.user_id")
    profile: Mapped["Profile | None"] = relationship(back_populates="user", uselist=False)


class Member(Base):
    """The authoritative membership record."""

    __tablename__ = "members"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="invited", index=True)
    invited_via_id: Mapped[str | None] = mapped_column(ForeignKey("invitations.id"), nullable=True)
    approved_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # cohost responsible for welcoming this member
    welcome_cohost: Mapped[str] = mapped_column(String(120), default="")
    # follow-up flag for introductions or other promises made in admissions
    needs_followup: Mapped[bool] = mapped_column(Boolean, default=False)
    followup_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="member",
                                      foreign_keys=[user_id])


class Profile(Base):
    """Member-facing profile data. Never contains admissions notes."""

    __tablename__ = "profiles"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    headline: Mapped[str] = mapped_column(String(200), default="")  # one-line intro
    role: Mapped[str] = mapped_column(String(120), default="")
    organization: Mapped[str] = mapped_column(String(120), default="")
    working_on: Mapped[str] = mapped_column(Text, default="")
    bio: Mapped[str] = mapped_column(Text, default="")
    interests: Mapped[list] = mapped_column(JSON, default=list)
    expertise: Mapped[list] = mapped_column(JSON, default=list)
    come_to_me_for: Mapped[str] = mapped_column(Text, default="")
    like_to_meet: Mapped[str] = mapped_column(Text, default="")
    outside_ai: Mapped[str] = mapped_column(Text, default="")
    website: Mapped[str] = mapped_column(String(300), default="")
    linkedin: Mapped[str] = mapped_column(String(300), default="")
    # explicit opt-in contact methods shown behind the Connect button
    contact_links: Mapped[list] = mapped_column(JSON, default=list)  # [{label, url}]
    show_email: Mapped[bool] = mapped_column(Boolean, default=False)  # login email stays private unless chosen
    photo_key: Mapped[str] = mapped_column(String(200), default="")
    directory_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    willing_host: Mapped[bool] = mapped_column(Boolean, default=False)
    willing_present: Mapped[bool] = mapped_column(Boolean, default=False)
    willing_organize: Mapped[bool] = mapped_column(Boolean, default=False)
    norms_accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    published: Mapped[bool] = mapped_column(Boolean, default=False)  # member approved how others see them
    onboarding_step: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="profile")


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(320), index=True)
    role_company: Mapped[str] = mapped_column(String(200), default="")
    link: Mapped[str] = mapped_column(String(300), default="")
    working_on: Mapped[str] = mapped_column(Text, default="")
    why_join: Mapped[str] = mapped_column(Text, default="")
    referrer: Mapped[str] = mapped_column(String(160), default="", index=True)
    status: Mapped[str] = mapped_column(String(16), default="new", index=True)
    # private flag: invite this person to a gathering first (not membership)
    suggest_gathering: Mapped[bool] = mapped_column(Boolean, default=False)
    decided_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Invitation(Base):
    __tablename__ = "invitations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    note: Mapped[str] = mapped_column(Text, default="")  # welcome note shown on redemption
    application_id: Mapped[str | None] = mapped_column(ForeignKey("applications.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending|redeemed|revoked
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    redeemed_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    redeemed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    @staticmethod
    def new_token() -> str:
        return secrets.token_urlsafe(32)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(200))
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime())  # stored UTC
    tz_label: Mapped[str] = mapped_column(String(60), default="America/New_York")
    description: Mapped[str] = mapped_column(Text, default="")
    host: Mapped[str] = mapped_column(String(160), default="")
    image_key: Mapped[str] = mapped_column(String(200), default="")
    external_url: Mapped[str] = mapped_column(String(500), default="")  # e.g. Luma
    published: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class EmailMessage(Base):
    """Transactional email outbox — every send is recorded and retryable."""

    __tablename__ = "email_messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(40), index=True)  # invitation|application_ack|welcome|login_code|…
    to_email: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(300))
    body_text: Mapped[str] = mapped_column(Text)
    body_html: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending|sent|failed|skipped
    error: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    invitation_id: Mapped[str | None] = mapped_column(ForeignKey("invitations.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    actor_email: Mapped[str] = mapped_column(String(320), default="")
    action: Mapped[str] = mapped_column(String(60), index=True)
    target_type: Mapped[str] = mapped_column(String(40), default="")
    target_id: Mapped[str] = mapped_column(String(40), default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class PrivateNote(Base):
    """Admissions notes. NEVER serialized into member-facing responses."""

    __tablename__ = "private_notes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    subject_type: Mapped[str] = mapped_column(String(20))  # application|member
    subject_id: Mapped[str] = mapped_column(String(40), index=True)
    author_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class RemovalRequest(Base):
    __tablename__ = "removal_requests"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    email: Mapped[str] = mapped_column(String(320))
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|done
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class LoginCode(Base):
    """Dev-adapter one-time codes (used only when AUTH_PROVIDER=dev)."""

    __tablename__ = "login_codes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
