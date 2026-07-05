"""Email-OTP MFA for the customer dashboard login (Phase 5 · 5B.5).

Mirrors the staff flow — opt-in per user, two-step login (password → emailed code
→ session). The code also resolves the tenant across same-email accounts. Brevo
send is mocked; the OTP is pinned for determinism.
"""

from __future__ import annotations

from sqlalchemy import select


def _enable_mfa(email: str) -> None:
    from app.db import SessionLocal
    from app.models import User
    db = SessionLocal()
    try:
        u = db.execute(select(User).where(User.email == email)).scalars().first()
        u.mfa_enabled = True
        db.commit()
    finally:
        db.close()


def _mock(monkeypatch, code: str = "123456") -> dict:
    from app import mfa as mfamod
    sent: dict = {}
    monkeypatch.setattr(mfamod.email, "send_email", lambda **kw: (sent.update(kw), True)[1])
    monkeypatch.setattr(mfamod, "new_otp", lambda: code)
    return sent


def test_dashboard_mfa_two_step(make_org, client, monkeypatch):
    sent = _mock(monkeypatch)
    org = make_org()
    _enable_mfa(org["admin_email"])

    r1 = client.post("/v1/auth/login",
                     json={"email": org["admin_email"], "password": org["admin_password"]})
    assert r1.status_code == 200
    assert r1.json().get("mfa_required") is True
    assert client.get("/v1/auth/me").status_code == 401
    assert sent and org["admin_email"] in str(sent)

    r2 = client.post("/v1/auth/mfa",
                     json={"email": org["admin_email"], "code": "123456"})
    assert r2.status_code == 200
    me = client.get("/v1/auth/me")
    assert me.status_code == 200 and me.json()["email"] == org["admin_email"]


def test_dashboard_mfa_wrong_code_rejected(make_org, client, monkeypatch):
    _mock(monkeypatch)
    org = make_org()
    _enable_mfa(org["admin_email"])
    client.post("/v1/auth/login",
                json={"email": org["admin_email"], "password": org["admin_password"]})
    r = client.post("/v1/auth/mfa",
                    json={"email": org["admin_email"], "code": "000000"})
    assert r.status_code == 401
    assert client.get("/v1/auth/me").status_code == 401


def test_non_mfa_dashboard_login_unchanged(make_org, client):
    org = make_org()
    r = client.post("/v1/auth/login",
                    json={"email": org["admin_email"], "password": org["admin_password"]})
    assert r.status_code == 200
    assert r.json().get("mfa_required") is not True
    assert client.get("/v1/auth/me").json()["email"] == org["admin_email"]
