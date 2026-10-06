from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.models import Member, Profile, User
from app.routers import api as api_router


def _settings(monkeypatch, token: str):
    monkeypatch.setattr(
        api_router, "get_settings",
        lambda: SimpleNamespace(arronax_sync_token=token),
    )


def _add_member(db, *, profile: dict | None = None) -> tuple[str, Member]:
    user = User(email=f"{uuid4().hex}@EXAMPLE.COM")
    db.add(user)
    db.flush()
    member = Member(
        user_id=user.id,
        status="active",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        approved_at=datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC),
        updated_at=datetime(2025, 1, 3, 4, 5, 6, tzinfo=UTC),
    )
    db.add(member)
    if profile is not None:
        db.add(Profile(user_id=user.id, **profile))
    db.commit()
    return user.id, member


def test_members_endpoint_is_hidden_when_sync_token_is_unset(client, monkeypatch):
    _settings(monkeypatch, "")

    response = client.get("/api/members")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_members_endpoint_requires_a_matching_bearer_token(client, monkeypatch):
    _settings(monkeypatch, "expected-token")

    missing = client.get("/api/members")
    wrong = client.get("/api/members", headers={"Authorization": "Bearer wrong-token"})

    assert missing.status_code == 401
    assert wrong.status_code == 401


def test_members_endpoint_exports_profile_fields_without_admissions_data(
    client, db, monkeypatch,
):
    _settings(monkeypatch, "expected-token")
    profiled_id, member = _add_member(db, profile={
        "display_name": "Alex Member",
        "role": "Founder",
        "organization": "Example Co",
        "headline": "Building helpful tools",
        "linkedin": "https://linkedin.com/in/alex",
        "website": "https://example.com",
        "working_on": "A new product",
        "interests": ["AI", "Design"],
        "expertise": ["Product"],
    })
    bare_id, _ = _add_member(db)

    response = client.get(
        "/api/members", headers={"Authorization": "Bearer expected-token"},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    members = {row["id"]: row for row in response.json()["members"]}
    profiled = members[profiled_id]
    assert profiled == {
        "id": profiled_id,
        "email": profiled["email"].lower(),
        "status": "active",
        "name": "Alex Member",
        "role": "Founder",
        "organization": "Example Co",
        "headline": "Building helpful tools",
        "linkedin": "https://linkedin.com/in/alex",
        "website": "https://example.com",
        "working_on": "A new product",
        "interests": ["AI", "Design"],
        "expertise": ["Product"],
        "joined_at": member.approved_at.isoformat(),
        "updated_at": member.updated_at.isoformat(),
    }
    assert profiled["email"].endswith("@example.com")
    bare = members[bare_id]
    assert {key: bare[key] for key in (
        "name", "role", "organization", "headline", "linkedin", "website",
        "working_on", "interests", "expertise",
    )} == {
        "name": "",
        "role": "",
        "organization": "",
        "headline": "",
        "linkedin": "",
        "website": "",
        "working_on": "",
        "interests": [],
        "expertise": [],
    }
    admissions_keys = {
        "score", "score_reason", "score_source", "scored_at",
        "needs_followup", "followup_note", "welcome_cohost", "private_notes",
    }
    assert all(not (admissions_keys & row.keys()) for row in members.values())
