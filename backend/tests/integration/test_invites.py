"""Set-password-link invites — customer teammates + platform staff (Phase 5 · 5D.2).

Inviting a user (no explicit password) creates the account with an unusable
random password and emails a "set your password" link (reusing the reset-token
infra), instead of returning a shareable plaintext temp password. Supplying an
explicit password keeps the old direct-set behaviour.
"""

from __future__ import annotations

import re


def _mock(monkeypatch) -> dict:
    from app import email as emailmod
    sent: dict = {}
    monkeypatch.setattr(emailmod, "send_email",
                        lambda **kw: (sent.update(kw), True)[1])
    return sent


def _token(sent: dict) -> str:
    body = (sent.get("html") or "") + (sent.get("text") or "")
    m = re.search(r"reset_token=([A-Za-z0-9_\-]+)", body)
    assert m, f"no set-password link in email: {sent}"
    return m.group(1)


# ── customer teammate invite ──────────────────────────────────────────────────

def test_customer_invite_emails_set_password_link(make_org, login, client, monkeypatch):
    sent = _mock(monkeypatch)
    org = make_org()
    admin = login(org["admin_email"], org["admin_password"])

    r = admin.post("/v1/auth/users", json={"email": "teammate@test.dev", "role": "member"})
    assert r.status_code == 200
    body = r.json()
    assert body["invited"] is True
    assert not body.get("temp_password")            # no plaintext password handed out
    assert "teammate@test.dev" in str(sent)         # invite email went to the invitee

    token = _token(sent)
    assert client.post("/v1/auth/reset-password", json={
        "token": token, "new_password": "teammate1234"}).status_code == 200
    assert client.post("/v1/auth/login", json={
        "email": "teammate@test.dev", "password": "teammate1234"}).status_code == 200


def test_customer_create_with_explicit_password_still_returns_temp(make_org, login, client):
    org = make_org()
    admin = login(org["admin_email"], org["admin_password"])
    r = admin.post("/v1/auth/users", json={
        "email": "direct@test.dev", "role": "member", "password": "adminpass123"})
    assert r.status_code == 200
    body = r.json()
    assert body["invited"] is False
    assert body["temp_password"] == "adminpass123"
    assert client.post("/v1/auth/login", json={
        "email": "direct@test.dev", "password": "adminpass123"}).status_code == 200


# ── staff invite ──────────────────────────────────────────────────────────────

def test_staff_invite_emails_set_password_link(make_staff, staff_login, client, monkeypatch):
    sent = _mock(monkeypatch)
    su = make_staff(role="superadmin")
    cs = staff_login(su["email"], su["password"])

    r = cs.post("/admin/v1/staff", json={"email": "newstaff@foxy.audit", "platform_role": "operator"})
    assert r.status_code == 200
    body = r.json()
    assert body["invited"] is True
    assert not body.get("temp_password")

    token = _token(sent)
    assert client.post("/admin/v1/auth/reset-password", json={
        "token": token, "new_password": "newstaff1234"}).status_code == 200
    staff_login("newstaff@foxy.audit", "newstaff1234")   # asserts 200 on success
