"""P1 · §D — member management (change-role / enable / resend-invite) and MFA
self-enrollment. Guards: admin-only, tenant isolation, last-admin lockout,
step-up on MFA disable."""
from __future__ import annotations

import app.mfa as mfamod
from app.db import SessionLocal
from app.models import User


def _uid(org_id: str, email: str) -> str:
    db = SessionLocal()
    try:
        return str(db.query(User).filter(User.org_id == org_id, User.email == email).one().id)
    finally:
        db.close()


def _mfa_flag(org_id: str, email: str) -> bool:
    db = SessionLocal()
    try:
        return bool(db.query(User).filter(User.org_id == org_id, User.email == email).one().mfa_enabled)
    finally:
        db.close()


# ── member management ───────────────────────────────────────────────────────

def test_change_role_promotes_member(make_org, add_user, login):
    org = make_org()
    add_user(org["org_id"], "m@test.dev", "memberpass1", role="member")
    mid = _uid(org["org_id"], "m@test.dev")
    c = login(org["admin_email"], org["admin_password"])
    r = c.post(f"/v1/auth/users/{mid}/role", json={"role": "admin"})
    assert r.status_code == 200 and r.json()["role"] == "admin"
    users = {u["email"]: u for u in c.get("/v1/auth/users").json()}
    assert users["m@test.dev"]["role"] == "admin"


def test_cannot_demote_last_admin(make_org, login):
    org = make_org()
    aid = _uid(org["org_id"], org["admin_email"])
    c = login(org["admin_email"], org["admin_password"])
    r = c.post(f"/v1/auth/users/{aid}/role", json={"role": "member"})
    assert r.status_code == 400 and "last admin" in r.json()["detail"]


def test_change_role_requires_admin(make_org, add_user, login):
    org = make_org()
    add_user(org["org_id"], "member@test.dev", "memberpass1", role="member")
    aid = _uid(org["org_id"], org["admin_email"])
    c = login("member@test.dev", "memberpass1")
    assert c.post(f"/v1/auth/users/{aid}/role", json={"role": "member"}).status_code == 403


def test_enable_reenables_disabled_user(make_org, add_user, login):
    org = make_org()
    add_user(org["org_id"], "off@test.dev", "pw12345678", role="member", disabled=True)
    uid = _uid(org["org_id"], "off@test.dev")
    c = login(org["admin_email"], org["admin_password"])
    assert c.post(f"/v1/auth/users/{uid}/enable").status_code == 200
    # the re-enabled user can now log in
    assert login("off@test.dev", "pw12345678").get("/v1/auth/me").status_code == 200


def test_resend_invite(make_org, add_user, login, monkeypatch):
    import app.routers.auth_human as ah
    sent = {}
    monkeypatch.setattr(ah.password_reset, "issue_reset",
                        lambda *a, **k: sent.update({"to": a[2], "invite": k.get("invite")}) or None)
    org = make_org()
    add_user(org["org_id"], "invitee@test.dev", "pw12345678", role="member")
    uid = _uid(org["org_id"], "invitee@test.dev")
    c = login(org["admin_email"], org["admin_password"])
    assert c.post(f"/v1/auth/users/{uid}/resend-invite").status_code == 200
    assert sent["to"] == "invitee@test.dev" and sent["invite"] is True


def test_member_actions_are_tenant_isolated(make_org, add_user, login):
    org_a = make_org()
    org_b = make_org()
    add_user(org_b["org_id"], "b-user@test.dev", "pw12345678", role="member")
    victim = _uid(org_b["org_id"], "b-user@test.dev")
    c = login(org_a["admin_email"], org_a["admin_password"])   # admin of A
    assert c.post(f"/v1/auth/users/{victim}/role", json={"role": "admin"}).status_code == 404
    assert c.post(f"/v1/auth/users/{victim}/enable").status_code == 404


# ── MFA self-enrollment ─────────────────────────────────────────────────────

def test_mfa_enroll_enable_then_disable(make_org, login, monkeypatch):
    monkeypatch.setattr(mfamod, "new_otp", lambda: "246810")
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/auth/me").json()["mfa_enabled"] is False
    assert c.post("/v1/auth/mfa/enroll").status_code == 200
    assert c.post("/v1/auth/mfa/enable", json={"code": "246810"}).status_code == 200
    assert c.get("/v1/auth/me").json()["mfa_enabled"] is True
    assert _mfa_flag(org["org_id"], org["admin_email"]) is True
    # disabling needs the account password (step-up)
    assert c.post("/v1/auth/mfa/disable", json={"password": org["admin_password"]}).status_code == 200
    assert c.get("/v1/auth/me").json()["mfa_enabled"] is False


def test_mfa_enable_rejects_wrong_code(make_org, login, monkeypatch):
    monkeypatch.setattr(mfamod, "new_otp", lambda: "111111")
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    c.post("/v1/auth/mfa/enroll")
    assert c.post("/v1/auth/mfa/enable", json={"code": "999999"}).status_code == 403
    assert _mfa_flag(org["org_id"], org["admin_email"]) is False


def test_mfa_disable_requires_correct_password(make_org, login, monkeypatch):
    monkeypatch.setattr(mfamod, "new_otp", lambda: "222222")
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    c.post("/v1/auth/mfa/enroll")
    c.post("/v1/auth/mfa/enable", json={"code": "222222"})
    assert c.post("/v1/auth/mfa/disable", json={"password": "wrong-pass"}).status_code == 403
    assert _mfa_flag(org["org_id"], org["admin_email"]) is True


def test_mfa_endpoints_require_login(client):
    assert client.post("/v1/auth/mfa/enroll").status_code == 401
    assert client.post("/v1/auth/mfa/enable", json={"code": "123456"}).status_code == 401
    assert client.post("/v1/auth/mfa/disable", json={"password": "x"}).status_code == 401
