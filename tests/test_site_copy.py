import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import AuditEvent
from tests.conftest import admin_client, csrf, sign_in


@pytest.fixture
def fresh_site_copy():
    from app.db import Base, engine
    from app.site_copy import invalidate

    invalidate()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    try:
        yield
    finally:
        invalidate()
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)


def test_anonymous_cannot_enter_copy_edit_mode(fresh_site_copy):
    client = TestClient(app, base_url="http://testserver")

    response = client.get("/gatherings")
    assert "<h1>Salons, breakfasts, and dinners.</h1>" in response.text
    assert "data-copy-key" not in response.text

    edit_response = client.get("/gatherings?edit=1")
    assert "data-copy-key" not in edit_response.text
    assert "js/edit.js" not in edit_response.text


def test_non_admin_cannot_enter_copy_edit_mode_or_save(fresh_site_copy):
    client = TestClient(app, base_url="http://testserver")
    sign_in(client, "someone@example.com")

    response = client.get("/gatherings?edit=1")
    assert "data-copy-key" not in response.text
    assert "js/edit.js" not in response.text

    response = client.post(
        "/admin/copy",
        data={"csrf": csrf(client), "changes": json.dumps({"gatherings.title": "Nope"})},
    )
    assert response.status_code == 404


def test_admin_can_edit_public_copy_only(fresh_site_copy):
    client = admin_client()

    response = client.get("/gatherings")
    assert 'href="/gatherings?edit=1"' in response.text
    assert response.text.index("Edit page") < response.text.index(">Admin</a>")

    edit_response = client.get("/gatherings?edit=1")
    assert 'data-copy-key="gatherings.title"' in edit_response.text
    assert "js/edit.js" in edit_response.text

    admin_response = client.get("/admin?edit=1")
    assert "data-copy-key" not in admin_response.text


def test_admin_saves_escaped_copy_and_audit_event(fresh_site_copy, db):
    client = admin_client()
    changes = {
        "gatherings.title": "<script>alert(1)</script>",
        "gatherings.intro": "Members meet\nin small groups.",
    }
    response = client.post(
        "/admin/copy",
        data={"csrf": csrf(client), "changes": json.dumps(changes)},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    page = client.get("/gatherings")
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page.text
    assert "<script>alert(1)</script>" not in page.text
    assert "Members meet<br>in small groups." in page.text

    db.expire_all()
    assert db.query(AuditEvent).filter_by(action="copy.update").one()
    audit_page = client.get("/admin/audit")
    assert audit_page.status_code == 200
    assert "copy.update" in audit_page.text


def test_copy_save_rejects_invalid_csrf_key_length_and_json(fresh_site_copy):
    client = admin_client()
    token = csrf(client)

    bad_csrf = client.post(
        "/admin/copy",
        data={"csrf": "invalid", "changes": "{}"},
    )
    assert bad_csrf.status_code == 403

    bad_key = client.post(
        "/admin/copy",
        data={"csrf": token, "changes": json.dumps({"Bad Key!": "value"})},
    )
    assert bad_key.status_code == 422

    long_value = client.post(
        "/admin/copy",
        data={"csrf": token, "changes": json.dumps({"gatherings.title": "x" * 2001})},
    )
    assert long_value.status_code == 422

    invalid_json = client.post(
        "/admin/copy",
        data={"csrf": token, "changes": "not-json"},
    )
    assert invalid_json.status_code == 422


def test_admin_resets_copy_and_audit_page_renders_action(fresh_site_copy, db):
    client = admin_client()
    response = client.post(
        "/admin/copy",
        data={
            "csrf": csrf(client),
            "changes": json.dumps({"gatherings.title": "A temporary title"}),
        },
    )
    assert response.status_code == 200

    response = client.post(
        "/admin/copy/reset",
        data={"csrf": csrf(client), "key": "gatherings.title"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert "<h1>Salons, breakfasts, and dinners.</h1>" in client.get("/gatherings").text

    db.expire_all()
    assert db.query(AuditEvent).filter_by(action="copy.reset").one()
    audit_page = client.get("/admin/audit")
    assert audit_page.status_code == 200
    assert "copy.update" in audit_page.text
    assert "copy.reset" in audit_page.text


def test_copy_reset_audits_keys_up_to_80_characters(fresh_site_copy, db):
    client = admin_client()
    key = "a" * 80
    response = client.post(
        "/admin/copy",
        data={"csrf": csrf(client), "changes": json.dumps({key: "temporary"})},
    )
    assert response.status_code == 200

    response = client.post(
        "/admin/copy/reset",
        data={"csrf": csrf(client), "key": key},
    )
    assert response.status_code == 200

    db.expire_all()
    event = db.query(AuditEvent).filter_by(action="copy.reset", target_id=key).one()
    assert event.target_id == key
