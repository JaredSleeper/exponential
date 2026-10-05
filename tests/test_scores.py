from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app import scoring
from app.db import Base, engine
from app.main import app
from app.models import (
    Application,
    AuditEvent,
    Member,
    Profile,
    User,
)
from app.routers import public
from app.security import limiter
from app.site_copy import invalidate
from tests.conftest import admin_client, csrf, make_member


@pytest.fixture(autouse=True)
def fresh_scores():
    limiter._hits.clear()
    invalidate()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    invalidate()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def add_application(db, name: str, score=None, source: str = "", reason: str = "") -> str:
    row = Application(
        name=name,
        email=f"{name.lower().replace(' ', '.')}@example.com",
        role_company="Engineer at Example",
        linkedin="https://linkedin.com/in/example",
        referrer="Friend",
        score=score,
        score_source=source,
        score_reason=reason,
    )
    db.add(row)
    db.commit()
    return row.id


def add_member(db, email: str, name: str, score=None, source: str = "", reason: str = "") -> str:
    user = User(email=email)
    db.add(user)
    db.flush()
    member = Member(
        user_id=user.id,
        status="active",
        score=score,
        score_source=source,
        score_reason=reason,
    )
    profile = Profile(
        user_id=user.id,
        display_name=name,
        role="Engineer",
        organization="Example",
        linkedin="https://linkedin.com/in/example",
    )
    db.add_all([member, profile])
    db.commit()
    return user.id


def post_score(client: TestClient, path: str, score: str, next_url: str):
    return client.post(
        path,
        data={
            "csrf": client.cookies.get("exp_csrf"),
            "score": score,
            "next": next_url,
        },
        follow_redirects=False,
    )


def test_admin_can_set_clear_and_reject_invalid_application_scores(db):
    admin = admin_client()
    app_id = add_application(db, "Applicant One", score=6, source="ai", reason="Strong work.")
    path = f"/admin/applications/{app_id}/score"
    next_url = "/admin/applications?status=new&referrer=Friend"

    response = post_score(admin, path, "8", next_url)
    assert response.status_code == 303
    assert urlsplit(response.headers["location"]).path == "/admin/applications"
    assert parse_qs(urlsplit(response.headers["location"]).query) == {
        "status": ["new"],
        "referrer": ["Friend"],
        "notice": ["Score saved."],
    }
    db.expire_all()
    row = db.get(Application, app_id)
    assert row.score == 8
    assert row.score_source == "admin"
    assert row.score_reason == "Strong work."
    assert row.scored_at is not None

    event = db.query(AuditEvent).filter_by(action="application.score", target_id=app_id).one()
    assert event.details == {"score": 8}

    for value in ("0", "11", "abc", "7.5"):
        response = post_score(admin, path, value, next_url)
        assert response.status_code == 303
        assert parse_qs(urlsplit(response.headers["location"]).query)["error"] == [
            "Score must be a whole number from 1 to 10."
        ]
        db.expire_all()
        row = db.get(Application, app_id)
        assert row.score == 8
        assert row.score_source == "admin"
        assert row.score_reason == "Strong work."

    response = post_score(admin, path, "", next_url)
    assert response.status_code == 303
    db.expire_all()
    row = db.get(Application, app_id)
    assert row.score is None
    assert row.score_source == ""
    assert row.score_reason == ""
    assert row.scored_at is None
    assert db.query(AuditEvent).filter_by(action="application.score", target_id=app_id).count() == 2


def test_admin_can_set_clear_and_reject_invalid_member_scores(db):
    admin = admin_client()
    client = TestClient(app, base_url="http://testserver")
    make_member(client, admin, db, "score-member@example.com", "Score Member")
    member = db.query(Member).one()
    member.score = 5
    member.score_source = "ai"
    member.score_reason = "Prior AI reason."
    db.commit()

    path = f"/admin/members/{member.user_id}/score"
    response = post_score(admin, path, "9", "/admin/members?status=active")
    assert response.status_code == 303
    assert parse_qs(urlsplit(response.headers["location"]).query) == {
        "status": ["active"],
        "notice": ["Score saved."],
    }
    db.expire_all()
    member = db.get(Member, member.user_id)
    assert member.score == 9
    assert member.score_source == "admin"
    assert member.score_reason == "Prior AI reason."
    assert member.scored_at is not None

    event = db.query(AuditEvent).filter_by(action="member.score", target_id=member.user_id).one()
    assert event.details == {"score": 9}

    response = post_score(admin, path, "7.5", "/admin/members")
    assert parse_qs(urlsplit(response.headers["location"]).query)["error"] == [
        "Score must be a whole number from 1 to 10."
    ]
    db.expire_all()
    member = db.get(Member, member.user_id)
    assert member.score == 9
    assert member.score_source == "admin"

    response = post_score(admin, path, "", "/admin/members")
    assert response.status_code == 303
    db.expire_all()
    member = db.get(Member, member.user_id)
    assert member.score is None
    assert member.score_source == ""
    assert member.score_reason == ""
    assert member.scored_at is None
    assert db.query(AuditEvent).filter_by(action="member.score", target_id=member.user_id).count() == 2


def test_non_admin_cannot_post_score_routes(db):
    admin = admin_client()
    client = TestClient(app, base_url="http://testserver")
    make_member(client, admin, db, "not-admin@example.com", "Not Admin")
    member = db.query(Member).one()
    app_id = add_application(db, "Protected Applicant")

    app_response = post_score(
        client,
        f"/admin/applications/{app_id}/score",
        "8",
        "/admin/applications",
    )
    member_response = post_score(
        client,
        f"/admin/members/{member.user_id}/score",
        "8",
        "/admin/members",
    )
    assert app_response.status_code == 404
    assert member_response.status_code == 404


def test_off_site_next_redirects_to_applications_list(db):
    admin = admin_client()
    app_id = add_application(db, "Redirect Applicant")

    response = post_score(
        admin,
        f"/admin/applications/{app_id}/score",
        "5",
        "https://evil.com",
    )

    location = urlsplit(response.headers["location"])
    assert location.path == "/admin/applications"
    assert location.netloc == ""


def test_off_site_next_redirects_to_members_list(db):
    admin = admin_client()
    member_id = add_member(db, "redirect-member@example.com", "Redirect Member")

    response = post_score(
        admin,
        f"/admin/members/{member_id}/score",
        "5",
        "https://evil.com",
    )

    location = urlsplit(response.headers["location"])
    assert location.path == "/admin/members"
    assert location.netloc == ""


def test_disabled_backfill_redirect_preserves_member_filter():
    admin = admin_client()

    response = admin.post(
        "/admin/scores/run",
        data={
            "csrf": admin.cookies.get("exp_csrf"),
            "next": "/admin/members?status=active",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert urlsplit(response.headers["location"]).path == "/admin/members"
    assert parse_qs(urlsplit(response.headers["location"]).query) == {
        "status": ["active"],
        "error": ["AI scoring isn't configured."],
    }


def test_backfill_scores_applications_and_members_without_overwriting_admin(
    db, monkeypatch
):
    app_id = add_application(db, "AI Applicant")
    admin_app_id = add_application(db, "Admin Applicant", score=8, source="admin")
    no_result_id = add_application(db, "No Result Applicant")
    member_id = add_member(db, "ai-member@example.com", "AI Member")
    admin_member_id = add_member(
        db, "admin-member@example.com", "Admin Member", source="admin"
    )

    monkeypatch.setattr(scoring, "fetch_linkedin", lambda url: "LinkedIn profile evidence")

    def fake_ask_model(content: str):
        if "Name: No Result Applicant" in content:
            return None
        return 9, "Strong evidence at a respected organization."

    monkeypatch.setattr(scoring, "ask_model", fake_ask_model)

    assert scoring.run_backfill() == 2
    db.expire_all()

    ai_application = db.get(Application, app_id)
    assert ai_application.score == 9
    assert ai_application.score_source == "ai"
    assert ai_application.score_reason == "Strong evidence at a respected organization."
    assert ai_application.scored_at is not None

    ai_member = db.get(Member, member_id)
    assert ai_member.score == 9
    assert ai_member.score_source == "ai"
    assert ai_member.score_reason == "Strong evidence at a respected organization."

    assert db.get(Application, admin_app_id).score == 8
    admin_member = db.get(Member, admin_member_id)
    assert admin_member.score is None
    assert admin_member.score_source == "admin"

    no_result = db.get(Application, no_result_id)
    assert no_result.score is None
    assert no_result.score_source == ""
    assert no_result.score_reason == ""

    event = db.query(AuditEvent).filter_by(action="scores.backfill").one()
    assert event.details == {"count": 2}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"score": 8, "reason": "Strong evidence."}', (8, "Strong evidence.")),
        ('```json\n{"score": 9, "reason": "Founder."}\n```', (9, "Founder.")),
        ('The result is {"score": 6, "reason": "Shipped work."} overall.', (6, "Shipped work.")),
        ('{"score": "7", "reason": "Research."}', (7, "Research.")),
        ('{"score": 7.0, "reason": "Research."}', (7, "Research.")),
    ],
)
def test_parse_score_accepts_valid_json(text, expected):
    assert scoring.parse_score(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        '{"score": 0, "reason": "Invalid."}',
        '{"score": 11, "reason": "Invalid."}',
        '{"score": 7.5, "reason": "Invalid."}',
        '{"score": "7.5", "reason": "Invalid."}',
        '{"score": true, "reason": "Invalid."}',
        '{"reason": "Missing score."}',
        '{"score": 7}',
        "not JSON",
    ],
)
def test_parse_score_rejects_invalid_json(text):
    assert scoring.parse_score(text) is None


def test_parse_score_truncates_reason_to_300_characters():
    reason = "x" * 400
    assert scoring.parse_score(f'{{"score": 5, "reason": "{reason}"}}') == (5, "x" * 300)


def test_apply_does_not_schedule_scoring_when_disabled(client, monkeypatch):
    calls = []
    monkeypatch.setattr(public, "get_settings", lambda: SimpleNamespace(scoring_enabled=False))
    monkeypatch.setattr(scoring, "score_new_application", lambda app_id: calls.append(app_id))

    response = client.post(
        "/apply",
        data={
            "csrf": csrf(client),
            "name": "Ada Lovelace",
            "email": "ada@example.com",
            "role_company": "Researcher",
            "linkedin": "https://linkedin.com/in/ada",
            "working_on": "Computing",
            "why_join": "Thoughtful community.",
            "referrer": "Friend",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert calls == []


def test_apply_schedules_scoring_once_when_enabled(client, db, monkeypatch):
    calls = []
    monkeypatch.setattr(public, "get_settings", lambda: SimpleNamespace(scoring_enabled=True))
    monkeypatch.setattr(scoring, "score_new_application", lambda app_id: calls.append(app_id))

    response = client.post(
        "/apply",
        data={
            "csrf": csrf(client),
            "name": "Grace Hopper",
            "email": "grace@example.com",
            "role_company": "Engineer",
            "linkedin": "https://linkedin.com/in/grace",
            "working_on": "Compilers",
            "why_join": "Meet other builders.",
            "referrer": "Friend",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    application = db.query(Application).one()
    assert calls == [application.id]


def test_applications_list_renders_and_sorts_scores(db):
    admin = admin_client()
    highest_id = add_application(db, "Highest Scored", score=10, source="ai", reason="Top reason.")
    middle_id = add_application(db, "Middle Scored", score=5, source="admin")
    unscored_id = add_application(db, "Unscored Applicant")

    response = admin.get("/admin/applications?sort=score")

    assert response.status_code == 200
    assert ">Score</a>" in response.text
    assert 'value="10"' in response.text
    assert ">AI</span>" in response.text
    positions = [
        response.text.index("Highest Scored"),
        response.text.index("Middle Scored"),
        response.text.index("Unscored Applicant"),
    ]
    assert positions == sorted(positions)
    assert highest_id in response.text
    assert middle_id in response.text
    assert unscored_id in response.text
    assert "AI scoring needs ANTHROPIC_API_KEY and EXA_API_KEY." in response.text

    filtered = admin.get("/admin/applications?status=new&referrer=Friend")
    assert (
        'href="/admin/applications?status=new&amp;referrer=Friend&amp;sort=score"'
        in filtered.text
    )
    sorted_filtered = admin.get(
        "/admin/applications?status=new&referrer=Friend&sort=score"
    )
    assert 'href="/admin/applications?status=new&amp;referrer=Friend"' in sorted_filtered.text


def test_members_list_renders_and_sorts_scores(db):
    admin = admin_client()
    add_member(db, "highest@example.com", "Highest Member", score=10, source="ai")
    add_member(db, "middle@example.com", "Middle Member", score=4, source="admin")
    add_member(db, "unscored@example.com", "Unscored Member")

    response = admin.get("/admin/members?status=active&sort=score")

    assert response.status_code == 200
    assert ">Score</a>" in response.text
    assert 'value="10"' in response.text
    assert ">AI</span>" in response.text
    positions = [
        response.text.index("Highest Member"),
        response.text.index("Middle Member"),
        response.text.index("Unscored Member"),
    ]
    assert positions == sorted(positions)
    assert 'href="/admin/members?status=active"' in response.text
