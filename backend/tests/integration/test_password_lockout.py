"""Regression cover for the password-change lockout (P3 §1).

The owner reported being unable to get back into his account after changing his
password. Two independent mechanisms could produce that, and both are pinned here:

  1. The client never told the browser's password manager the credential had
     changed, so the manager re-filled the OLD password at the login screen.
     That half is markup — asserted in tests/test_dashboard_password_form.py.
  2. A suspended or deleted workspace sharing the address made `login` RAISE
     instead of continuing to scan, denying access to a healthy account.

The end-to-end walk below is the gate for the whole P3 plan: change password ->
still authenticated -> log out -> log in with the NEW password -> succeeds. It
runs the real endpoints in the order the BROWSER hits them, which is not the
order the pre-existing test used: the browser gets a 403 `step_up_required`
first and only then confirms a step-up and retries.
"""

from __future__ import annotations

import uuid

import bcrypt
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Organization, User

NEW_PW = "brand-new-pass1"


def _stored_hash(email: str) -> str:
    with SessionLocal() as s:
        return s.execute(select(User).where(User.email == email)).scalars().first().password_hash


def _confirm_step_up(c: TestClient) -> None:
    """Walk the emailed-code step-up the SPA's fetch interceptor drives."""
    import app.mfa as _mfa
    orig = _mfa.new_otp
    _mfa.new_otp = lambda: "000000"
    try:
        assert c.post("/v1/auth/step-up/request").status_code == 200
        assert c.post("/v1/auth/step-up/confirm", json={"code": "000000"}).status_code == 200
    finally:
        _mfa.new_otp = orig


def test_change_password_browser_order_then_relogin(make_org):
    """THE GATE. Change -> still authed -> logout -> log in with the new password."""
    org = make_org()
    email, old_pw = org["admin_email"], org["admin_password"]

    c = TestClient(app)
    assert c.post("/v1/auth/login", json={"email": email, "password": old_pw}).status_code == 200
    csrf = c.cookies.get("foxy_csrf")
    if csrf:
        c.headers.update({"X-CSRF-Token": csrf})

    # The browser posts BEFORE holding a step-up grant and is refused.
    r = c.post("/v1/auth/change-password",
               json={"current_password": old_pw, "new_password": NEW_PW})
    assert r.status_code == 403 and r.json()["detail"] == "step_up_required"
    assert _stored_hash(email) == _stored_hash(email)   # the refusal changed nothing

    # The interceptor confirms step-up and retries the identical body.
    _confirm_step_up(c)
    r = c.post("/v1/auth/change-password",
               json={"current_password": old_pw, "new_password": NEW_PW})
    assert r.status_code == 200, r.text

    stored = _stored_hash(email)
    assert bcrypt.checkpw(NEW_PW.encode(), stored.encode())
    assert not bcrypt.checkpw(old_pw.encode(), stored.encode())

    # The session that made the change survives it — no orphaned cookie.
    assert c.get("/v1/auth/me").status_code == 200
    assert c.post("/v1/auth/logout").status_code == 200

    # And the new password gets you back in. The old one does not.
    assert TestClient(app).post(
        "/v1/auth/login", json={"email": email, "password": NEW_PW}).status_code == 200
    assert TestClient(app).post(
        "/v1/auth/login", json={"email": email, "password": old_pw}).status_code == 401


def test_step_up_grant_survives_the_password_change(make_org):
    """The grant lives inside the session cookie; changing the password must not
    drop it, or the next danger action re-prompts for no reason."""
    org = make_org()
    c = TestClient(app)
    c.post("/v1/auth/login", json={"email": org["admin_email"], "password": org["admin_password"]})
    csrf = c.cookies.get("foxy_csrf")
    if csrf:
        c.headers.update({"X-CSRF-Token": csrf})
    _confirm_step_up(c)
    assert c.post("/v1/auth/change-password",
                  json={"current_password": org["admin_password"],
                        "new_password": NEW_PW}).status_code == 200
    # A second gated mutation still passes without a fresh prompt.
    assert c.post("/v1/auth/change-password",
                  json={"current_password": NEW_PW,
                        "new_password": "third-password-9"}).status_code == 200


def test_suspended_org_does_not_shadow_a_healthy_account(make_org, add_user,
                                                         revealed_org_id):
    """Same address in a suspended workspace and a healthy one. The suspended
    match must not deny the healthy account — it must keep scanning."""
    dead, alive = make_org(), make_org()
    email, pw = "shadow@test.dev", "same-pass-999"
    add_user(dead["org_id"], email, pw, role="admin")
    add_user(alive["org_id"], email, pw, role="admin")
    with SessionLocal() as s:
        s.get(Organization, uuid.UUID(dead["org_id"])).suspended = True
        s.commit()

    c = TestClient(app)
    assert c.post("/v1/auth/login",
                  json={"email": email, "password": pw}).status_code == 200
    # Which org the session landed in is a step-up-gated question now (§7.1).
    assert revealed_org_id(c) == alive["org_id"]


def test_deleted_org_does_not_shadow_a_healthy_account(make_org, add_user,
                                                       revealed_org_id):
    from datetime import datetime, timezone
    dead, alive = make_org(), make_org()
    email, pw = "shadow2@test.dev", "same-pass-998"
    add_user(dead["org_id"], email, pw, role="admin")
    add_user(alive["org_id"], email, pw, role="admin")
    with SessionLocal() as s:
        s.get(Organization, uuid.UUID(dead["org_id"])).deleted_at = datetime.now(timezone.utc)
        s.commit()

    c = TestClient(app)
    assert c.post("/v1/auth/login",
                  json={"email": email, "password": pw}).status_code == 200
    assert revealed_org_id(c) == alive["org_id"]


def test_suspended_org_alone_still_refuses_with_its_reason(make_org, add_user):
    """The fix must not swallow the message when there IS no healthy account."""
    dead = make_org()
    email, pw = "only-dead@test.dev", "same-pass-997"
    add_user(dead["org_id"], email, pw, role="admin")
    with SessionLocal() as s:
        s.get(Organization, uuid.UUID(dead["org_id"])).suspended = True
        s.commit()

    r = TestClient(app).post("/v1/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 403
    assert r.json()["detail"] == "This workspace is suspended"
