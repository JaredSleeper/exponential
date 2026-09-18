import os
import re
import tempfile

_tmp = tempfile.mkdtemp(prefix="exp-test-")
os.environ.update({
    "DATABASE_URL": f"sqlite:///{_tmp}/test.db",
    "SECRET_KEY": "test-secret",
    "AUTH_PROVIDER": "dev",
    "EMAIL_PROVIDER": "console",
    "EMAIL_DISABLE_SENDS": "true",
    "MEDIA_DIR": f"{_tmp}/media",
    "ADMIN_EMAILS": "admin@example.com",
    "MEMBER_CAP": "3",
})

import pytest
from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import EmailMessage


@pytest.fixture(scope="session", autouse=True)
def _db():
    Base.metadata.create_all(engine)
    yield


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client():
    return TestClient(app, base_url="http://testserver")


def csrf(client: TestClient) -> str:
    client.get("/")
    return client.cookies.get("exp_csrf")


def sign_in(client: TestClient, email: str) -> None:
    """Full dev-adapter OTP sign-in."""
    t = csrf(client)
    r = client.post("/auth/sign-in", data={"csrf": t, "email": email, "next": "/members"})
    assert r.status_code == 200, r.text[:300]
    m = re.search(r"your code is <strong>(\d{6})</strong>", r.text)
    assert m, "dev code not rendered"
    r = client.post("/auth/verify",
                    data={"csrf": client.cookies.get("exp_csrf"),
                          "email": email, "code": m.group(1), "next": "/members"},
                    follow_redirects=False)
    assert r.status_code == 303, r.text[:300]


def admin_client() -> TestClient:
    c = TestClient(app, base_url="http://testserver")
    sign_in(c, "admin@example.com")
    return c


def make_member(client: TestClient, admin: TestClient, db, email: str,
                name: str = "Test Person") -> None:
    """Admin invites `email`; the invited user claims + completes onboarding."""
    t = csrf(admin)
    r = admin.post("/admin/invitations",
                   data={"csrf": t, "email": email, "note": "", "application_id": ""},
                   follow_redirects=False)
    assert r.status_code == 303
    # pull the invite link out of the outbox
    msg = db.query(EmailMessage).filter_by(kind="invitation", to_email=email) \
        .order_by(EmailMessage.created_at.desc()).first()
    token = re.search(r"/invite/(\S+)", msg.body_text).group(1)

    sign_in(client, email)
    r = client.post(f"/invite/{token}/claim",
                    data={"csrf": client.cookies.get("exp_csrf")},
                    follow_redirects=False)
    assert r.status_code == 303, r.text
    assert r.headers["location"].startswith("/onboarding")

    # minimal onboarding: save steps, upload photo, accept norms, publish
    post = lambda step, data: client.post("/onboarding/save",
                                          data={"csrf": client.cookies.get("exp_csrf"),
                                                "step": str(step), **data},
                                          follow_redirects=False)
    post(1, {"display_name": name, "headline": "Testing the edges of AI",
             "role": "", "organization": ""})
    post(3, {"working_on": "Writing tests", "bio": "", "come_to_me_for": "",
             "like_to_meet": "", "outside_ai": "", "interests": ["Agents"]})
    post(4, {"website": "", "linkedin": "", "norms_accepted": "on"})
    # tiny valid JPEG
    import io as _io

    from PIL import Image
    buf = _io.BytesIO()
    Image.new("RGB", (900, 700), "#33493a").save(buf, "JPEG")
    r = client.post("/onboarding/photo",
                    files={"photo": ("h.jpg", buf.getvalue(), "image/jpeg")},
                    data={"csrf": client.cookies.get("exp_csrf")}, follow_redirects=False)
    assert r.status_code == 303
    r = client.post("/onboarding/publish",
                    data={"csrf": client.cookies.get("exp_csrf")}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/welcome")
