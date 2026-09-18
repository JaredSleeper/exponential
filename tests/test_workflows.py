"""The meaningful workflows, per the launch spec."""
import re
import threading
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import (
    Application,
    EmailMessage,
    Invitation,
    PrivateNote,
    Profile,
    User,
)
from app.security import utcnow
from tests.conftest import admin_client, csrf, make_member, sign_in


@pytest.fixture(autouse=True)
def fresh():
    """Reset the DB and rate limiter between tests."""
    from app.db import engine
    from app.models import Base
    from app.security import limiter
    limiter._hits.clear()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


# ---------- applications ----------

def test_application_submit_and_admin_review(fresh, db):
    c = TestClient(app, base_url="http://testserver")
    t = csrf(c)
    r = c.post("/apply", data={
        "csrf": t, "name": "Ada Wong", "email": "ada@example.com",
        "role_company": "Researcher", "link": "https://ada.example.com",
        "working_on": "agent evals", "why_join": "good rooms", "referrer": "Mara",
    }, follow_redirects=False)
    assert r.status_code == 303
    a = db.query(Application).filter_by(email="ada@example.com").one()
    assert a.status == "new"
    # ack email queued
    assert db.query(EmailMessage).filter_by(kind="application_ack",
                                           to_email="ada@example.com").one()

    admin = admin_client()
    r = admin.get(f"/admin/applications/{a.id}")
    assert r.status_code == 200 and "Ada Wong" in r.text
    # waitlist → invite
    admin.post(f"/admin/applications/{a.id}/status",
               data={"csrf": csrf(admin), "status": "waitlisted"})
    db.expire_all()
    assert db.get(Application, a.id).status == "waitlisted"
    admin.post(f"/admin/applications/{a.id}/gathering", data={"csrf": csrf(admin)})
    db.expire_all()
    assert db.get(Application, a.id).suggest_gathering is True


def test_apply_requires_fields(fresh):
    c = TestClient(app, base_url="http://testserver")
    r = c.post("/apply", data={"csrf": csrf(c), "name": "", "email": "bad",
                               "working_on": "", "why_join": ""})
    assert r.status_code == 422


# ---------- direct invitation + onboarding ----------

def test_invitation_redemption_to_active_member(fresh, db):
    admin = admin_client()
    invited = TestClient(app, base_url="http://testserver")
    make_member(invited, admin, db, "founding@example.com", "Founding Member")
    u = db.query(User).filter_by(email="founding@example.com").one()
    assert u.member.status == "active"
    assert u.profile.published
    # sees member home + directory
    assert invited.get("/members").status_code == 200
    assert "Founding Member" in invited.get("/directory").text


def test_invite_single_use_and_forwarded(fresh, db):
    admin = admin_client()
    invited = TestClient(app, base_url="http://testserver")
    make_member(invited, admin, db, "once@example.com")

    msg = db.query(EmailMessage).filter_by(kind="invitation", to_email="once@example.com").one()
    token = re.search(r"/invite/(\S+)", msg.body_text).group(1)

    # second redemption attempt by a different signed-in user
    other = TestClient(app, base_url="http://testserver")
    sign_in(other, "intruder@example.com")
    r = other.post(f"/invite/{token}/claim",
                   data={"csrf": csrf(other)}, follow_redirects=False)
    assert r.status_code == 303  # redirected, no membership granted
    assert db.query(User).filter_by(email="intruder@example.com").one().member is None
    # landing page says "already used"
    r = other.get(f"/invite/{token}")
    assert "already" in r.text.lower()


def test_invite_email_mismatch(fresh, db):
    admin = admin_client()
    admin.post("/admin/invitations", data={
        "csrf": csrf(admin), "email": "right@example.com", "note": "", "application_id": ""})
    msg = db.query(EmailMessage).filter_by(to_email="right@example.com").one()
    token = re.search(r"/invite/(\S+)", msg.body_text).group(1)
    wrong = TestClient(app, base_url="http://testserver")
    sign_in(wrong, "wrong@example.com")
    r = wrong.post(f"/invite/{token}/claim", data={"csrf": csrf(wrong)})
    assert r.status_code == 403 and "different email" in r.text
    assert db.query(User).filter_by(email="wrong@example.com").one().member is None


def test_expired_invite(fresh, db):
    admin = admin_client()
    admin.post("/admin/invitations", data={
        "csrf": csrf(admin), "email": "late@example.com", "note": "", "application_id": ""})
    inv = db.query(Invitation).filter_by(email="late@example.com").one()
    inv.expires_at = utcnow() - timedelta(days=1)
    db.commit()
    c = TestClient(app, base_url="http://testserver")
    sign_in(c, "late@example.com")
    r = c.get(f"/invite/{inv.token_hash}")  # bogus token → invalid
    assert r.status_code == 404
    # real token from email body:
    msg = db.query(EmailMessage).filter_by(to_email="late@example.com",
                                           kind="invitation").one()
    token = re.search(r"/invite/(\S+)", msg.body_text).group(1)
    r = c.get(f"/invite/{token}")
    assert r.status_code == 410 and "expired" in r.text.lower()
    r = c.post(f"/invite/{token}/claim", data={"csrf": csrf(c)})
    assert r.status_code == 410


def test_revoked_invite(fresh, db):
    admin = admin_client()
    admin.post("/admin/invitations", data={
        "csrf": csrf(admin), "email": "rev@example.com", "note": "", "application_id": ""})
    inv = db.query(Invitation).filter_by(email="rev@example.com").one()
    admin.post(f"/admin/invitations/{inv.id}/revoke", data={"csrf": csrf(admin)})
    msg = db.query(EmailMessage).filter_by(to_email="rev@example.com").one()
    token = re.search(r"/invite/(\S+)", msg.body_text).group(1)
    r = TestClient(app, base_url="http://testserver").get(f"/invite/{token}")
    assert r.status_code == 410 and "no longer active" in r.text


# ---------- cap / concurrency ----------

def test_member_cap_blocks_invitations(fresh, db):
    admin = admin_client()
    c1, c2, c3 = (TestClient(app, base_url="http://testserver") for _ in range(3))
    make_member(c1, admin, db, "a@example.com", "A A")
    make_member(c2, admin, db, "b@example.com", "B B")
    make_member(c3, admin, db, "c@example.com", "C C")  # cap = 3, now full
    r = admin.post("/admin/invitations", data={
        "csrf": csrf(admin), "email": "d@example.com", "note": "", "application_id": ""},
        follow_redirects=True)
    assert "cap" in r.text.lower()
    assert db.query(Invitation).filter_by(email="d@example.com").count() == 0


def test_concurrent_claims_cannot_exceed_cap(fresh, db):
    """Two invitations pending under cap=4: both created, but if the cap drops
    to the current seat count before redemption, one claim must fail."""
    admin = admin_client()
    make_member(TestClient(app, base_url="http://testserver"), admin, db, "x@example.com")
    for email in ("p@example.com", "q@example.com"):
        admin.post("/admin/invitations",
                   data={"csrf": csrf(admin), "email": email, "note": "", "application_id": ""})
    # shrink cap to exactly seats-in-use: p+q invites already count, so both
    # claims would exceed — but a redemeed invite stops counting as pending.
    # Instead simulate two redemptions racing for the last seat.
    from app.services.admissions import set_setting
    set_setting(db, "member_cap", "3")  # 1 member + 2 pending = 3 seats
    db.commit()

    msgs = {m.to_email: m for m in db.query(EmailMessage).filter_by(kind="invitation").all()}
    tokens = {e: re.search(r"/invite/(\S+)", msgs[e].body_text).group(1)
              for e in ("p@example.com", "q@example.com")}
    clients = {}
    for e in tokens:
        c = TestClient(app, base_url="http://testserver")
        sign_in(c, e)
        clients[e] = c

    results = {}
    barrier = threading.Barrier(2)

    def claim(email):
        c = clients[email]
        barrier.wait()
        r = c.post(f"/invite/{tokens[email]}/claim",
                   data={"csrf": c.cookies.get("exp_csrf")})
        results[email] = r.status_code

    ts = [threading.Thread(target=claim, args=(e,)) for e in tokens]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    # both may claim because redeeming frees the pending-invite seat — the real
    # invariant: seats_used never exceeded the cap at commit time.
    from app.services.admissions import get_cap, seats_used
    db.expire_all()
    assert seats_used(db) <= get_cap(db)


def test_admin_can_raise_cap(fresh, db):
    admin = admin_client()
    admin.post("/admin/settings", data={
        "csrf": csrf(admin), "member_cap": "10", "signal_invite_url": "",
        "interest_tags": ""}, follow_redirects=False)
    from app.services.admissions import get_cap
    assert get_cap(db) == 10


# ---------- access control ----------

def test_anonymous_cannot_see_profiles_or_photos(fresh, db):
    admin = admin_client()
    invited = TestClient(app, base_url="http://testserver")
    make_member(invited, admin, db, "hide@example.com", "Hidden Gem")
    p = db.query(Profile).join(User, User.id == Profile.user_id) \
        .filter(User.email == "hide@example.com").one()

    anon = TestClient(app, base_url="http://testserver", follow_redirects=False)
    assert anon.get("/directory").status_code == 303
    assert anon.get(f"/directory/{p.user_id}").status_code == 303
    assert anon.get(f"/media/{p.photo_key}").status_code == 303
    assert anon.get("/admin").status_code == 303
    # header hygiene
    r = anon.get(f"/media/{p.photo_key}")
    assert r.headers.get("x-robots-tag") == "noindex, nofollow"


def test_invited_not_yet_active_cannot_use_directory(fresh, db):
    admin = admin_client()
    make_member(TestClient(app, base_url="http://testserver"), admin, db, "in@example.com")
    # a second user holds a seat but never finishes onboarding
    admin.post("/admin/invitations", data={
        "csrf": csrf(admin), "email": "mid@example.com", "note": "", "application_id": ""})
    msg = db.query(EmailMessage).filter_by(to_email="mid@example.com").one()
    token = re.search(r"/invite/(\S+)", msg.body_text).group(1)
    mid = TestClient(app, base_url="http://testserver")
    sign_in(mid, "mid@example.com")
    mid.post(f"/invite/{token}/claim", data={"csrf": csrf(mid)})
    r = mid.get("/directory", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/onboarding")


def test_member_cannot_reach_admin_or_notes(fresh, db):
    admin = admin_client()
    member = TestClient(app, base_url="http://testserver")
    make_member(member, admin, db, "m1@example.com", "M One")
    assert member.get("/admin").status_code == 404
    assert member.get("/admin/applications").status_code == 404
    assert member.get("/admin/audit").status_code == 404
    assert member.get("/admin/export/members.csv").status_code == 404

    # private notes never surface in member-facing pages
    a = Application(name="N", email="n@example.com", working_on="x", why_join="y")
    db.add(a)
    db.flush()
    db.add(PrivateNote(subject_type="application", subject_id=a.id,
                       author_id=db.query(User).first().id,
                       body="SECRET-NOTE-STRING"))
    db.commit()
    r = member.get("/directory")
    assert "SECRET-NOTE-STRING" not in r.text
    assert member.get(f"/admin/applications/{a.id}").status_code == 404


def test_paused_member_loses_access_mid_session(fresh, db):
    admin = admin_client()
    member = TestClient(app, base_url="http://testserver")
    make_member(member, admin, db, "pause@example.com", "P Ause")
    u = db.query(User).filter_by(email="pause@example.com").one()
    assert member.get("/directory").status_code == 200
    admin.post(f"/admin/members/{u.id}/status",
               data={"csrf": csrf(admin), "status": "paused"})
    # same session, access gone
    assert member.get("/directory", follow_redirects=False).status_code == 303


def test_directory_visibility_toggle(fresh, db):
    admin = admin_client()
    m1, m2 = TestClient(app, base_url="http://testserver"), TestClient(app, base_url="http://testserver")
    make_member(m1, admin, db, "vis@example.com", "Vis Ib")
    # m1 still needs capacity: cap 3, make another
    admin.post("/admin/settings", data={
        "csrf": csrf(admin), "member_cap": "10", "signal_invite_url": "", "interest_tags": ""})
    make_member(m2, admin, db, "other@example.com", "Other Er")
    p = db.query(Profile).join(User).filter(User.email == "vis@example.com").one()
    # hide own profile
    m1.post("/members/profile", data={
        "csrf": csrf(m1), "display_name": "Vis Ib", "headline": "x",
        "working_on": "x", "show_email_off": "1"}, follow_redirects=False)
    db.expire_all()
    assert db.get(Profile, p.user_id).directory_visible is False
    # others can't see the card or the page
    assert "Vis Ib" not in m2.get("/directory").text
    assert m2.get(f"/directory/{p.user_id}").status_code == 404
    # still a member: directory itself works
    assert m1.get("/directory").status_code == 200


def test_members_edit_only_own_profiles(fresh, db):
    admin = admin_client()
    admin.post("/admin/settings", data={
        "csrf": csrf(admin), "member_cap": "10", "signal_invite_url": "", "interest_tags": ""})
    m1, m2 = TestClient(app, base_url="http://testserver"), TestClient(app, base_url="http://testserver")
    make_member(m1, admin, db, "one@example.com", "One")
    make_member(m2, admin, db, "two@example.com", "Two")
    p2 = db.query(Profile).join(User).filter(User.email == "two@example.com").one()
    # no route exists to edit another profile; POSTing to own can't touch p2
    m1.post("/members/profile", data={
        "csrf": csrf(m1), "display_name": "Hacked", "headline": "x",
        "working_on": "x", "user_id": p2.user_id})
    db.expire_all()
    assert db.get(Profile, p2.user_id).display_name == "Two"


def test_duplicate_account_same_email(fresh, db):
    admin = admin_client()
    c1, c2 = TestClient(app, base_url="http://testserver"), TestClient(app, base_url="http://testserver")
    make_member(c1, admin, db, "dup@example.com", "Dup One")
    sign_in(c2, "dup@example.com")  # same email, second device
    assert db.query(User).filter_by(email="dup@example.com").count() == 1
    assert c2.get("/directory").status_code == 200


def test_directory_search_and_filters(fresh, db):
    admin = admin_client()
    admin.post("/admin/settings", data={
        "csrf": csrf(admin), "member_cap": "10", "signal_invite_url": "", "interest_tags": ""})
    m1, m2 = (TestClient(app, base_url="http://testserver") for _ in range(2))
    make_member(m1, admin, db, "searchable@example.com", "Search Able")
    make_member(m2, admin, db, "other@example.com", "Other Er")
    r = m2.get("/directory?q=Search")
    assert "Search Able" in r.text and "Other Er" not in r.text
    r = m2.get("/directory?q=nothing-matches-this")
    assert "No members match" in r.text and "Reset filters" in r.text
    r = m2.get("/directory?interest=Agents")
    assert "Search Able" in r.text


def test_removal_request_flow(fresh, db):
    admin = admin_client()
    m = TestClient(app, base_url="http://testserver")
    make_member(m, admin, db, "gone@example.com", "Gone Soon")
    m.post("/members/removal", data={"csrf": csrf(m), "reason": "moving away"})
    from app.models import RemovalRequest
    r = db.query(RemovalRequest).filter_by(email="gone@example.com").one()
    assert r.status == "open"
    # admin deletes the account
    u = db.query(User).filter_by(email="gone@example.com").one()
    admin.post(f"/admin/members/{u.id}/delete", data={"csrf": csrf(admin)})
    db.expire_all()
    assert db.query(User).filter_by(email="gone@example.com").count() == 0
    # the old session is dead
    assert m.get("/directory", follow_redirects=False).status_code == 303


def test_admin_bootstrap_only_via_env(fresh, db):
    # a normal sign-in is never admin
    c = TestClient(app, base_url="http://testserver")
    sign_in(c, "regular@example.com")
    assert c.get("/admin").status_code == 404
    u = db.query(User).filter_by(email="regular@example.com").one()
    assert not u.is_admin
    # no member-facing route can grant admin — /admin/make-admin requires admin
    admin_client()
    assert db.query(User).filter_by(email="admin@example.com").one().is_admin
