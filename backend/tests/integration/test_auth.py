"""Human session auth: login, RBAC, disabled users, change-password, and the
cross-tenant login fix (password — not index order — selects the org)."""

from __future__ import annotations

from sqlalchemy import select

from app.db import SessionLocal
from app.models import User


def test_login_and_me(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    me = c.get("/v1/auth/me").json()
    assert me["email"] == org["admin_email"]
    assert me["role"] == "admin"
    assert me["org_id"] == org["org_id"]


def test_login_wrong_password_401(make_org, client):
    org = make_org()
    r = client.post("/v1/auth/login",
                    json={"email": org["admin_email"], "password": "wrong-password"})
    assert r.status_code == 401


def test_login_nonexistent_401(client):
    r = client.post("/v1/auth/login",
                    json={"email": "ghost@nope.dev", "password": "whatever"})
    assert r.status_code == 401


def test_me_requires_auth(client):
    assert client.get("/v1/auth/me").status_code == 401


def test_disabled_user_cannot_login(make_org, add_user, client):
    org = make_org()
    add_user(org["org_id"], "dis@test.dev", "disabledpass1", role="member", disabled=True)
    r = client.post("/v1/auth/login",
                    json={"email": "dis@test.dev", "password": "disabledpass1"})
    assert r.status_code == 403


def test_change_password(make_org, login, client):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    r = c.post("/v1/auth/change-password",
               json={"current_password": org["admin_password"], "new_password": "brand-new-pass1"})
    assert r.status_code == 200
    # Old password rejected, new one works.
    assert client.post("/v1/auth/login",
                       json={"email": org["admin_email"], "password": org["admin_password"]}).status_code == 401
    assert client.post("/v1/auth/login",
                       json={"email": org["admin_email"], "password": "brand-new-pass1"}).status_code == 200


def test_change_password_wrong_current_403(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    r = c.post("/v1/auth/change-password",
               json={"current_password": "not-it", "new_password": "brand-new-pass1"})
    assert r.status_code == 403


def test_change_password_too_short_422(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    r = c.post("/v1/auth/change-password",
               json={"current_password": org["admin_password"], "new_password": "short"})
    assert r.status_code == 422


def test_disabled_user_live_session_rejected(make_org, add_user, login):
    """Disabling a user must reject their ALREADY-ACTIVE session, not just future
    logins — this exercises require_user's disabled guard (offboarding is instant)."""
    org = make_org()
    add_user(org["org_id"], "active@test.dev", "activepass1", role="member")
    c = login("active@test.dev", "activepass1")
    assert c.get("/v1/auth/me").status_code == 200

    db = SessionLocal()
    try:
        u = db.execute(select(User).where(User.email == "active@test.dev")).scalar_one()
        u.disabled = True
        db.commit()
    finally:
        db.close()

    assert c.get("/v1/auth/me").status_code == 401   # live session now rejected


def test_member_rbac_forbidden(make_org, add_user, login):
    org = make_org()
    add_user(org["org_id"], "mem@test.dev", "memberpass1", role="member")
    c = login("mem@test.dev", "memberpass1")
    assert c.get("/v1/keys").status_code == 403
    assert c.post("/v1/keys", json={"name": "x"}).status_code == 403
    assert c.get("/v1/auth/users").status_code == 403
    assert c.post("/v1/auth/users", json={"email": "z@test.dev", "role": "member"}).status_code == 403


def test_admin_can_invite_and_disable(make_org, login, client):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    created = c.post("/v1/auth/users",
                     json={"email": "aud@test.dev", "role": "member", "password": "audpass1234"})
    assert created.status_code == 200
    body = created.json()
    temp = body["temp_password"]
    # The created user can log in with the password the admin set.
    assert client.post("/v1/auth/login",
                       json={"email": "aud@test.dev", "password": temp}).status_code == 200
    # Admin disables them -> login now 403.
    assert c.post(f"/v1/auth/users/{body['id']}/disable").status_code == 200
    assert client.post("/v1/auth/login",
                       json={"email": "aud@test.dev", "password": temp}).status_code == 403


def test_disabled_flag_shows_in_user_list(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    uid = c.post("/v1/auth/users", json={"email": "u@test.dev", "role": "member"}).json()["id"]
    before = next(u for u in c.get("/v1/auth/users").json() if u["id"] == uid)
    assert before["disabled"] is False
    c.post(f"/v1/auth/users/{uid}/disable")
    after = next(u for u in c.get("/v1/auth/users").json() if u["id"] == uid)
    assert after["disabled"] is True


def test_admin_cannot_disable_self(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    me = c.get("/v1/auth/me").json()
    users = c.get("/v1/auth/users").json()
    my_id = next(u["id"] for u in users if u["email"] == me["email"])
    assert c.post(f"/v1/auth/users/{my_id}/disable").status_code == 400


def test_cross_tenant_login_password_selects_org(make_org, add_user, login):
    """Same email in two orgs with different passwords: the credential must pick
    the right tenant (the critical bug the A2+B review caught)."""
    a = make_org()
    b = make_org()
    add_user(a["org_id"], "dup@test.dev", "passAAA111", role="admin")
    add_user(b["org_id"], "dup@test.dev", "passBBB222", role="admin")

    ca = login("dup@test.dev", "passAAA111")
    assert ca.get("/v1/auth/me").json()["org_id"] == a["org_id"]
    cb = login("dup@test.dev", "passBBB222")
    assert cb.get("/v1/auth/me").json()["org_id"] == b["org_id"]
    # org A's session must stay scoped to A after B logs in with the same email.
    assert ca.get("/v1/auth/me").json()["org_id"] == a["org_id"]


def test_login_is_rate_limited(make_org, client):
    """Customer login must throttle brute force — staff login already does, the
    customer login didn't. (Phase 5 · 5B.3)"""
    org = make_org()
    codes = [
        client.post("/v1/auth/login",
                    json={"email": org["admin_email"], "password": "wrong"}).status_code
        for _ in range(15)
    ]
    assert 429 in codes, f"expected a 429 after repeated attempts, got {codes}"


def test_login_rotates_the_session(make_org, client):
    """A pre-existing (attacker-planted) session must be discarded on login, not
    merged into the authenticated session — session-fixation guard. (Phase 5 · 5B.4)"""
    import base64
    import json
    from itsdangerous import TimestampSigner

    org = make_org()
    signer = TimestampSigner("test-session-secret")   # == conftest SESSION_SECRET

    def decode(raw: str) -> dict:
        return json.loads(base64.b64decode(signer.unsign(raw.strip('"'))))

    # Plant a stray key inside a validly-signed session cookie, then log in.
    planted = base64.b64encode(json.dumps({"stray": "evil"}).encode())
    client.cookies.set("session", signer.sign(planted).decode())
    r = client.post("/v1/auth/login",
                    json={"email": org["admin_email"], "password": org["admin_password"]})
    assert r.status_code == 200

    # read the fresh session set by THIS login response (the jar holds two
    # same-named cookies — the planted one + the new one — so get() is ambiguous)
    session = decode(r.cookies.get("session"))
    assert "stray" not in session, "login must clear a pre-existing session (fixation guard)"
    assert "user_id" in session
