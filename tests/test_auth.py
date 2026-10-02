from types import SimpleNamespace

from app.routers import auth as auth_router
from tests.conftest import sign_in


def test_clerk_sign_out_renders_clerk_logout_and_clears_session(client, monkeypatch):
    sign_in(client, "clerk-signout@example.com")
    assert client.cookies.get("exp_session")
    monkeypatch.setattr(
        auth_router,
        "get_settings",
        lambda: SimpleNamespace(
            auth_provider="clerk",
            clerk_publishable_key="pk_test_Y2xlcmsuZXhhbXBsZS5jb20k",
        ),
    )

    response = client.post(
        "/auth/sign-out",
        data={"csrf": client.cookies.get("exp_csrf")},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Clerk.signOut({redirectUrl: '/'});" in response.text
    assert not client.cookies.get("exp_session")
    members = client.get("/members", follow_redirects=False)
    assert members.status_code == 303
    assert members.headers["location"] == "/auth/sign-in"


def test_dev_sign_out_still_redirects_and_clears_session(client):
    sign_in(client, "dev-signout@example.com")

    response = client.post(
        "/auth/sign-out",
        data={"csrf": client.cookies.get("exp_csrf")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert not client.cookies.get("exp_session")


def test_homepage_cache_control_is_not_public(client):
    response = client.get("/")

    assert response.headers["cache-control"] == "private, no-cache"
