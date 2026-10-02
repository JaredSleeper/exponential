from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.db import Base, engine
from app.main import app
from app.models import Application, LoginCode, Profile, User
from app.security import limiter
from app.site_copy import invalidate
from tests.conftest import admin_client, csrf, make_member


@pytest.fixture(autouse=True)
def fresh_apply_errors():
    limiter._hits.clear()
    invalidate()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    invalidate()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def valid_application(csrf_token: str, **overrides):
    form = {
        "csrf": csrf_token,
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "role_company": "Researcher at Analytical Engines",
        "linkedin": "https://linkedin.com/in/ada",
        "working_on": "Computing",
        "why_join": "I enjoy meeting thoughtful builders.",
        "referrer": "A friend",
    }
    form.update(overrides)
    return form


def test_first_apply_page_has_persistent_token_and_accepts_submission(db):
    client = TestClient(app, base_url="http://testserver")

    page = client.get("/apply")
    hidden_token = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    assert hidden_token == client.cookies.get("exp_csrf")
    assert "max-age=31536000" in page.headers["set-cookie"].lower()

    response = client.post(
        "/apply",
        data=valid_application(hidden_token),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/apply/done"
    assert db.query(Application).one().email == "ada@example.com"


def test_apply_csrf_failure_preserves_answers_without_creating_application(db):
    client = TestClient(app, base_url="http://testserver")
    client.get("/apply")

    response = client.post(
        "/apply",
        data=valid_application(
            "",
            name="Saved Applicant",
            why_join="Please keep this answer visible.",
        ),
        follow_redirects=False,
    )

    assert response.status_code == 422
    assert "Saved Applicant" in response.text
    assert "Please keep this answer visible." in response.text
    assert "press Submit again" in response.text
    assert db.query(Application).count() == 0


def test_application_accepts_long_referrer_and_role_company(db):
    client = TestClient(app, base_url="http://testserver")
    token = csrf(client)
    referrer = "r" * 500
    role_company = "c" * 400

    response = client.post(
        "/apply",
        data=valid_application(token, referrer=referrer, role_company=role_company),
        follow_redirects=False,
    )

    assert response.status_code == 303
    application = db.query(Application).one()
    assert application.referrer == referrer
    assert application.role_company == role_company


def test_apply_rejects_overlong_name_and_removes_nuls(db):
    client = TestClient(app, base_url="http://testserver")
    token = csrf(client)

    too_long = client.post(
        "/apply",
        data=valid_application(token, name="N" * 121),
        follow_redirects=False,
    )
    assert too_long.status_code == 422
    assert "120 characters or fewer" in too_long.text
    assert db.query(Application).count() == 0

    clean_submission = client.post(
        "/apply",
        data=valid_application(token, working_on="Build\x00ing a safer tool."),
        follow_redirects=False,
    )
    assert clean_submission.status_code == 303
    assert db.query(Application).one().working_on == "Building a safer tool."


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("email", f'{"e" * 321}@example.com', "320 characters or fewer"),
        ("linkedin", f"https://linkedin.com/in/{'l' * 300}", "300 characters or fewer"),
    ],
)
def test_apply_rejects_other_overlong_bounded_fields(field, value, message, db):
    client = TestClient(app, base_url="http://testserver")
    response = client.post(
        "/apply",
        data=valid_application(csrf(client), **{field: value}),
        follow_redirects=False,
    )

    assert response.status_code == 422
    assert message in response.text
    assert db.query(Application).count() == 0


@pytest.mark.parametrize(
    ("referer", "expected_href"),
    [
        ("http://testserver/apply?source=expired", 'href="/apply"'),
        ("http://testserver//outside.example", 'href="/"'),
        ("https://outside.example/apply", 'href="/"'),
    ],
)
def test_other_csrf_failures_use_html_for_browsers(referer, expected_href):
    client = TestClient(app, base_url="http://testserver")
    response = client.post(
        "/auth/sign-out",
        headers={"accept": "text/html", "referer": referer},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.headers["content-type"].startswith("text/html")
    assert "This page has expired." in response.text
    assert expected_href in response.text

    json_response = client.post(
        "/auth/sign-out",
        headers={"accept": "application/json"},
        follow_redirects=False,
    )
    assert json_response.status_code == 403
    assert json_response.headers["content-type"].startswith("application/json")
    assert json_response.json()["detail"] in ("Missing CSRF token", "Bad CSRF token")


def test_profile_limit_returns_existing_friendly_error(db):
    email = "profile-limit@example.com"
    client = TestClient(app, base_url="http://testserver")
    make_member(client, admin_client(), db, email, "Profile Member")
    token = csrf(client)

    response = client.post(
        "/members/profile",
        data={
            "csrf": token,
            "display_name": "D" * 121,
            "linkedin": "https://linkedin.com/in/profile-member",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "Your name must be 120 characters or fewer." in parse_qs(
        urlsplit(response.headers["location"]).query
    )["error"][0]
    db.expire_all()
    user = db.query(User).filter_by(email=email).one()
    assert db.get(Profile, user.id).display_name == "Profile Member"


def test_onboarding_limit_returns_existing_friendly_error(db):
    email = "onboarding-limit@example.com"
    client = TestClient(app, base_url="http://testserver")
    make_member(client, admin_client(), db, email, "Onboarding Member")
    response = client.post(
        "/onboarding/save",
        data={
            "csrf": csrf(client),
            "step": "1",
            "display_name": "Onboarding Member",
            "headline": "H" * 201,
            "role": "",
            "organization": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "Your one-line introduction must be 200 characters or fewer." in parse_qs(
        urlsplit(response.headers["location"]).query
    )["error"][0]


def test_sign_in_email_is_nul_cleaned_and_length_checked(db):
    client = TestClient(app, base_url="http://testserver")
    token = csrf(client)
    response = client.post(
        "/auth/sign-in",
        data={"csrf": token, "email": "ada\x00@example.com", "next": "/"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert db.query(LoginCode).one().email == "ada@example.com"

    too_long = client.post(
        "/auth/sign-in",
        data={"csrf": token, "email": f'{"a" * 321}@example.com', "next": "/"},
        follow_redirects=False,
    )
    assert too_long.status_code == 422
    assert "320 characters or fewer" in too_long.text
    assert db.query(LoginCode).count() == 1
