"""Password reset via emailed link — customer + staff (Phase 5 · 5D).

Forgot-password is enumeration-safe (always 200, never reveals whether an email
exists). It emails a link carrying a single-use, hash-stored token with a 1-hour
TTL; reset-password consumes the token and sets the new password. The token is
captured from the mocked email so each test drives the real end-to-end flow.
"""

from __future__ import annotations

import re


def _mock(monkeypatch) -> dict:
    """Patch the shared email sender (module attribute, so password_reset's
    `email.send_email` picks it up) and capture the last message."""
    from app import email as emailmod
    sent: dict = {}
    monkeypatch.setattr(emailmod, "send_email",
                        lambda **kw: (sent.update(kw), True)[1])
    return sent


def _token(sent: dict) -> str:
    body = (sent.get("html") or "") + (sent.get("text") or "")
    m = re.search(r"reset_token=([A-Za-z0-9_\-]+)", body)
    assert m, f"no reset_token link in email: {sent}"
    return m.group(1)


# ── customer dashboard ────────────────────────────────────────────────────────

def test_customer_reset_flow(make_org, client, monkeypatch):
    sent = _mock(monkeypatch)
    org = make_org()

    assert client.post("/v1/auth/forgot-password",
                       json={"email": org["admin_email"]}).status_code == 200
    token = _token(sent)

    assert client.post("/v1/auth/reset-password",
                       json={"token": token, "new_password": "newpass1234"}).status_code == 200

    # old password no longer works; the new one does
    assert client.post("/v1/auth/login", json={
        "email": org["admin_email"], "password": org["admin_password"]}).status_code == 401
    assert client.post("/v1/auth/login", json={
        "email": org["admin_email"], "password": "newpass1234"}).status_code == 200


def test_forgot_password_unknown_email_is_200_and_silent(client, monkeypatch):
    sent = _mock(monkeypatch)
    r = client.post("/v1/auth/forgot-password", json={"email": "nobody@nowhere.test"})
    assert r.status_code == 200          # never reveal non-existence
    assert sent == {}                    # ...and no email goes out


def test_reset_with_bad_token_rejected(make_org, client, monkeypatch):
    _mock(monkeypatch)
    make_org()
    assert client.post("/v1/auth/reset-password", json={
        "token": "not-a-real-token", "new_password": "whatever1234"}).status_code == 401


def test_reset_token_is_single_use(make_org, client, monkeypatch):
    sent = _mock(monkeypatch)
    org = make_org()
    client.post("/v1/auth/forgot-password", json={"email": org["admin_email"]})
    token = _token(sent)
    assert client.post("/v1/auth/reset-password", json={
        "token": token, "new_password": "newpass1234"}).status_code == 200
    # second use of the same token is rejected
    assert client.post("/v1/auth/reset-password", json={
        "token": token, "new_password": "another1234"}).status_code == 401


def test_reset_rejects_short_password(make_org, client, monkeypatch):
    sent = _mock(monkeypatch)
    org = make_org()
    client.post("/v1/auth/forgot-password", json={"email": org["admin_email"]})
    token = _token(sent)
    assert client.post("/v1/auth/reset-password", json={
        "token": token, "new_password": "short"}).status_code == 422


# ── staff / admin console ─────────────────────────────────────────────────────

def test_staff_reset_flow(make_staff, staff_login, client, monkeypatch):
    sent = _mock(monkeypatch)
    staff = make_staff()

    assert client.post("/admin/v1/auth/forgot-password",
                       json={"email": staff["email"]}).status_code == 200
    token = _token(sent)
    assert client.post("/admin/v1/auth/reset-password",
                       json={"token": token, "new_password": "newstaff1234"}).status_code == 200

    # old rejected, new accepted (staff_login asserts 200 on success)
    assert client.post("/admin/v1/auth/login", json={
        "email": staff["email"], "password": staff["password"]}).status_code == 401
    staff_login(staff["email"], "newstaff1234")
