"""Manual admin grants and applicant-to-member promotion."""
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.clerk import ensure_user
from app.main import app
from app.models import (
    Application,
    AuditEvent,
    EmailMessage,
    Invitation,
    Member,
    User,
)
from app.security import utcnow
from tests.conftest import admin_client, csrf, make_member, sign_in


def redirect_param(response, key: str) -> str:
    return parse_qs(urlsplit(response.headers["location"]).query)[key][0]


@pytest.fixture(autouse=True)
def fresh():
    from app.db import engine
    from app.models import Base
    from app.security import limiter

    limiter._hits.clear()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def test_admin_manually_adds_member_and_sends_welcome(fresh, db):
    admin = admin_client()
    response = admin.post(
        "/admin/members/new",
        data={
            "csrf": csrf(admin),
            "email": " Ada@Example.com ",
            "name": "Ada Lovelace",
            "linkedin": "linkedin.com/in/ada-lovelace",
            "headline": "Building analytical engines",
            "note": "Glad you are joining us.",
            "send_welcome": "on",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    user = db.query(User).filter_by(email="ada@example.com").one()
    assert response.headers["location"].startswith(f"/admin/members/{user.id}")
    assert user.member.status == "invited"
    assert user.profile.display_name == "Ada Lovelace"
    assert user.profile.linkedin == "https://linkedin.com/in/ada-lovelace"
    assert user.profile.headline == "Building analytical engines"

    welcome = db.query(EmailMessage).filter_by(
        kind="member_welcome", to_email="ada@example.com"
    ).one()
    assert "/auth/sign-in" in welcome.body_text
    assert "A note from your host:\nGlad you are joining us." in welcome.body_text
    event = db.query(AuditEvent).filter_by(action="member.created_manually").one()
    assert event.target_id == user.id
    assert event.details == {"email": "ada@example.com", "welcome_sent": True}


def test_manual_member_without_welcome_does_not_queue_email(fresh, db):
    admin = admin_client()
    response = admin.post(
        "/admin/members/new",
        data={"csrf": csrf(admin), "email": "quiet@example.com"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert db.query(Member).count() == 1
    assert db.query(EmailMessage).filter_by(kind="member_welcome").count() == 0


@pytest.mark.parametrize(
    ("email", "linkedin", "error"),
    [
        ("bad-email", "", "Enter a valid email address."),
        ("valid@example.com", "https://example.com/person", "Use a LinkedIn profile URL"),
    ],
)
def test_manual_member_rejects_invalid_fields(fresh, db, email, linkedin, error):
    admin = admin_client()
    response = admin.post(
        "/admin/members/new",
        data={"csrf": csrf(admin), "email": email, "linkedin": linkedin},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/members?")
    assert error in redirect_param(response, "error")
    assert db.query(User).filter_by(email=email.lower()).count() == 0


def test_non_admin_and_anonymous_cannot_create_or_promote_members(fresh, db):
    admin = admin_client()
    applicant = Application(
        name="Applicant", email="applicant@example.com", working_on="x", why_join="y"
    )
    db.add(applicant)
    db.commit()

    member_client = TestClient(app, base_url="http://testserver")
    make_member(member_client, admin, db, "member@example.com", "Current Member")
    member_token = csrf(member_client)
    anonymous = TestClient(app, base_url="http://testserver", follow_redirects=False)
    anonymous_token = csrf(anonymous)

    for client, token, expected in (
        (member_client, member_token, 404),
        (anonymous, anonymous_token, 303),
    ):
        add_response = client.post(
            "/admin/members/new",
            data={"csrf": token, "email": "blocked@example.com"},
            follow_redirects=False,
        )
        promote_response = client.post(
            f"/admin/applications/{applicant.id}/make-member",
            data={"csrf": token},
            follow_redirects=False,
        )
        assert add_response.status_code == expected
        assert promote_response.status_code == expected


def test_admin_makes_applicant_member_and_copies_score(fresh, db):
    admin = admin_client()
    applicant = Application(
        name="Grace Hopper",
        email="grace@example.com",
        role_company="Compiler design",
        linkedin="https://www.linkedin.com/in/grace-hopper",
        working_on="Compilers",
        why_join="Meet other builders",
        score=7,
        score_source="ai",
        score_reason="Strong experience.",
        scored_at=utcnow(),
    )
    db.add(applicant)
    db.commit()

    response = admin.post(
        f"/admin/applications/{applicant.id}/make-member",
        data={
            "csrf": csrf(admin),
            "note": "Welcome to the community.",
            "send_welcome": "on",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    user = db.query(User).filter_by(email="grace@example.com").one()
    member = db.get(Member, user.id)
    db.refresh(applicant)
    assert applicant.status == "member"
    assert member.status == "invited"
    assert (member.score, member.score_source, member.score_reason) == (
        7, "ai", "Strong experience."
    )
    assert member.scored_at == applicant.scored_at
    assert user.profile.display_name == "Grace Hopper"
    assert user.profile.linkedin == "https://www.linkedin.com/in/grace-hopper"
    assert user.profile.headline == "Compiler design"
    welcome = db.query(EmailMessage).filter_by(
        kind="member_welcome", to_email="grace@example.com"
    ).one()
    assert "/auth/sign-in" in welcome.body_text
    event = db.query(AuditEvent).filter_by(action="application.made_member").one()
    assert event.target_id == applicant.id
    assert event.details == {
        "email": "grace@example.com",
        "user_id": user.id,
        "welcome_sent": True,
    }


def test_pending_invite_is_revoked_before_capacity_check(fresh, db):
    admin = admin_client()
    for email in ("applicant@example.com", "second@example.com", "third@example.com"):
        response = admin.post(
            "/admin/invitations",
            data={"csrf": csrf(admin), "email": email},
            follow_redirects=False,
        )
        assert response.status_code == 303

    applicant = Application(
        name="Applicant", email="applicant@example.com", working_on="x", why_join="y"
    )
    db.add(applicant)
    db.commit()
    invite = db.query(Invitation).filter_by(email="applicant@example.com").one()

    response = admin.post(
        f"/admin/applications/{applicant.id}/make-member",
        data={"csrf": csrf(admin)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db.refresh(invite)
    assert invite.status == "revoked"
    assert db.query(Member).count() == 1

    response = admin.post(
        "/admin/members/new",
        data={"csrf": csrf(admin), "email": "fourth@example.com"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "Member cap reached" in redirect_param(response, "error")
    assert db.query(User).filter_by(email="fourth@example.com").count() == 0
    assert db.query(Member).count() == 1


def test_existing_member_receives_notice_without_duplicate_or_welcome(fresh, db):
    admin = admin_client()
    member_client = TestClient(app, base_url="http://testserver")
    make_member(member_client, admin, db, "existing@example.com", "Existing Member")
    user = db.query(User).filter_by(email="existing@example.com").one()

    response = admin.post(
        "/admin/members/new",
        data={
            "csrf": csrf(admin),
            "email": "existing@example.com",
            "send_welcome": "on",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/admin/members/{user.id}?notice=")
    assert "already a member" in redirect_param(response, "notice").lower()
    assert db.query(Member).filter_by(user_id=user.id).count() == 1
    assert db.query(EmailMessage).filter_by(kind="member_welcome").count() == 0


def test_inviting_member_with_application_does_not_create_invite(fresh, db):
    from app.services.admissions import seats_used

    admin = admin_client()
    applicant = Application(
        name="Already Member", email="already@example.com", working_on="x", why_join="y"
    )
    db.add(applicant)
    db.commit()
    admin.post(
        f"/admin/applications/{applicant.id}/make-member",
        data={"csrf": csrf(admin)},
        follow_redirects=False,
    )
    user = db.query(User).filter_by(email="already@example.com").one()
    seats_before = seats_used(db)

    response = admin.post(
        "/admin/invitations",
        data={
            "csrf": csrf(admin),
            "email": "already@example.com",
            "application_id": applicant.id,
        },
        follow_redirects=False,
    )

    db.refresh(applicant)
    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/admin/members/{user.id}?notice=")
    assert redirect_param(response, "notice") == "already@example.com is already a member."
    assert db.query(Invitation).filter_by(email="already@example.com").count() == 0
    assert applicant.status == "member"
    assert seats_used(db) == seats_before


def test_member_application_detail_hides_invitation_card(fresh, db):
    admin = admin_client()
    applicant = Application(
        name="Already Member", email="already@example.com", working_on="x", why_join="y"
    )
    db.add(applicant)
    db.commit()
    admin.post(
        f"/admin/applications/{applicant.id}/make-member",
        data={"csrf": csrf(admin)},
        follow_redirects=False,
    )

    response = admin.get(f"/admin/applications/{applicant.id}")

    assert response.status_code == 200
    assert "Already a member." in response.text
    assert "Send membership invitation" not in response.text


def test_redeemed_application_invite_stays_member_on_make_member_retry(fresh, db):
    admin = admin_client()
    applicant = Application(
        name="Invited Applicant",
        email="invited-applicant@example.com",
        working_on="x",
        why_join="y",
    )
    db.add(applicant)
    db.commit()
    response = admin.post(
        "/admin/invitations",
        data={
            "csrf": csrf(admin),
            "email": applicant.email,
            "application_id": applicant.id,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    invitation_email = db.query(EmailMessage).filter_by(
        kind="invitation", to_email=applicant.email
    ).one()
    token = re.search(r"/invite/(\S+)", invitation_email.body_text).group(1)
    member_client = TestClient(app, base_url="http://testserver")
    sign_in(member_client, applicant.email)
    response = member_client.post(
        f"/invite/{token}/claim",
        data={"csrf": csrf(member_client)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db.refresh(applicant)
    assert applicant.status == "member"

    response = admin.post(
        f"/admin/applications/{applicant.id}/make-member",
        data={"csrf": csrf(admin), "send_welcome": "on"},
        follow_redirects=False,
    )

    db.refresh(applicant)
    assert response.status_code == 303
    assert redirect_param(response, "notice") == (
        "invited-applicant@example.com is already a member."
    )
    assert applicant.status == "member"
    assert db.query(EmailMessage).filter_by(kind="member_welcome").count() == 0


def test_manually_added_member_can_complete_onboarding(fresh, db):
    admin = admin_client()
    admin.post(
        "/admin/members/new",
        data={"csrf": csrf(admin), "email": "new-member@example.com"},
        follow_redirects=False,
    )
    member_client = TestClient(app, base_url="http://testserver")
    sign_in(member_client, "new-member@example.com")

    response = member_client.get("/members", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/onboarding")

    token = member_client.cookies.get("exp_csrf")

    def save(step, data):
        return member_client.post(
            "/onboarding/save",
            data={"csrf": token, "step": str(step), **data},
            follow_redirects=False,
        )

    save(1, {
        "display_name": "New Member",
        "headline": "Building useful tools",
        "role": "",
        "organization": "",
    })
    save(3, {
        "working_on": "Useful tools",
        "bio": "",
        "come_to_me_for": "",
        "like_to_meet": "",
        "outside_ai": "",
    })
    save(4, {
        "website": "",
        "linkedin": "https://linkedin.com/in/new-member",
        "norms_accepted": "on",
    })
    response = member_client.post(
        "/onboarding/publish",
        data={"csrf": token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/welcome")
    user = db.query(User).filter_by(email="new-member@example.com").one()
    db.refresh(user.member)
    assert user.member.status == "active"
    assert user.profile.published


def test_clerk_account_is_ensured_for_manual_member(fresh, db, monkeypatch):
    from app.config import get_settings
    from app.routers import admin as admin_routes

    admin = admin_client()
    monkeypatch.setattr(get_settings(), "auth_provider", "clerk")
    calls = []
    monkeypatch.setattr(admin_routes.clerk, "ensure_user",
                        lambda email, name="": calls.append((email, name)))

    response = admin.post(
        "/admin/members/new",
        data={
            "csrf": csrf(admin),
            "email": "clerk@example.com",
            "name": "Clerk Person",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert calls == [("clerk@example.com", "Clerk Person")]


def test_clerk_failure_rolls_back_manual_member(fresh, db, monkeypatch):
    from app.config import get_settings
    from app.routers import admin as admin_routes

    admin = admin_client()
    monkeypatch.setattr(get_settings(), "auth_provider", "clerk")

    def fail(*_args, **_kwargs):
        raise RuntimeError("Clerk unavailable")

    monkeypatch.setattr(admin_routes.clerk, "ensure_user", fail)
    response = admin.post(
        "/admin/members/new",
        data={
            "csrf": csrf(admin),
            "email": "clerk-fail@example.com",
            "name": "No Account",
            "send_welcome": "on",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "Couldn't create their sign-in account: Clerk unavailable" in redirect_param(
        response, "error"
    )
    assert db.query(User).filter_by(email="clerk-fail@example.com").count() == 0
    assert db.query(Member).count() == 0
    assert db.query(EmailMessage).filter_by(kind="member_welcome").count() == 0


def test_clerk_ensure_user_returns_when_account_exists(monkeypatch):
    from app import clerk
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "clerk_secret_key", "test-secret")
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"id": "user_123"}]

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr(clerk.httpx, "get", get)
    monkeypatch.setattr(clerk.httpx, "post",
                        lambda *_args, **_kwargs: pytest.fail("unexpected Clerk POST"))

    assert ensure_user("ada@example.com", "Ada Lovelace") is None
    assert calls == [(
        "https://api.clerk.com/v1/users",
        {
            "headers": {"Authorization": "Bearer test-secret"},
            "params": {"email_address": "ada@example.com"},
            "timeout": 15,
        },
    )]


def test_clerk_ensure_user_creates_account_with_name(monkeypatch):
    from app import clerk
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "clerk_secret_key", "test-secret")
    post_calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return []

    class PostResponse:
        def raise_for_status(self):
            pass

    monkeypatch.setattr(clerk.httpx, "get", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(
        clerk.httpx,
        "post",
        lambda url, **kwargs: post_calls.append((url, kwargs)) or PostResponse(),
    )

    ensure_user("ada@example.com", "Ada Lovelace")

    assert post_calls == [(
        "https://api.clerk.com/v1/users",
        {
            "headers": {"Authorization": "Bearer test-secret"},
            "json": {
                "email_address": ["ada@example.com"],
                "skip_password_requirement": True,
                "first_name": "Ada",
                "last_name": "Lovelace",
            },
            "timeout": 15,
        },
    )]
