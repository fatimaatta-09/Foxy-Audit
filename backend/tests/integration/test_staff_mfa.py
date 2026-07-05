"""Email-OTP MFA for staff login via Brevo (Phase 5 · 5B.5).

Opt-in per staff (`mfa_enabled`). When enabled, login is two-step: password →
emailed 6-digit code → session. The Brevo send is mocked here (a real send needs
BREVO_API_KEY and is verified live); the OTP is pinned to a known value so step 2
is deterministic.
"""

from __future__ import annotations

import uuid


def _enable_mfa(staff_id: str) -> None:
    from app.db import SessionLocal
    from app.models import StaffUser
    db = SessionLocal()
    try:
        s = db.get(StaffUser, uuid.UUID(staff_id))
        s.mfa_enabled = True
        db.commit()
    finally:
        db.close()


def _mock(monkeypatch, code: str = "123456") -> dict:
    from app import email as emailmod
    from app.routers import auth_staff as staffmod
    sent: dict = {}
    monkeypatch.setattr(emailmod, "send_email", lambda **kw: (sent.update(kw), True)[1])
    monkeypatch.setattr(staffmod, "_new_otp", lambda: code)
    return sent


def test_mfa_login_two_step(make_staff, client, monkeypatch):
    sent = _mock(monkeypatch)
    staff = make_staff()
    _enable_mfa(staff["id"])

    # step 1: correct password → mfa_required, NO session yet, code emailed.
    r1 = client.post("/admin/v1/auth/login",
                     json={"email": staff["email"], "password": staff["password"]})
    assert r1.status_code == 200
    assert r1.json().get("mfa_required") is True
    assert client.get("/admin/v1/auth/me").status_code == 401
    assert sent and staff["email"] in str(sent)

    # step 2: submit the emailed code → authenticated.
    r2 = client.post("/admin/v1/auth/mfa",
                     json={"email": staff["email"], "code": "123456"})
    assert r2.status_code == 200
    me = client.get("/admin/v1/auth/me")
    assert me.status_code == 200 and me.json()["email"] == staff["email"]


def test_mfa_wrong_code_rejected(make_staff, client, monkeypatch):
    _mock(monkeypatch)
    staff = make_staff()
    _enable_mfa(staff["id"])
    client.post("/admin/v1/auth/login",
                json={"email": staff["email"], "password": staff["password"]})
    r = client.post("/admin/v1/auth/mfa",
                    json={"email": staff["email"], "code": "000000"})
    assert r.status_code == 401
    assert client.get("/admin/v1/auth/me").status_code == 401


def test_non_mfa_staff_login_unchanged(make_staff, client):
    """Opt-in: a staff WITHOUT MFA logs in one-step, exactly as before."""
    staff = make_staff()
    r = client.post("/admin/v1/auth/login",
                    json={"email": staff["email"], "password": staff["password"]})
    assert r.status_code == 200
    assert r.json().get("mfa_required") is not True
    assert client.get("/admin/v1/auth/me").json()["email"] == staff["email"]
