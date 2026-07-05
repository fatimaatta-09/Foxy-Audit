"""Login history (Phase 5 · 5K): successes + failures are recorded; an org admin
can review the org's recent login attempts."""

from __future__ import annotations

from sqlalchemy import text

from app.db import SessionLocal


def test_admin_sees_successful_logins(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])       # a successful login → recorded
    r = c.get("/v1/auth/login-history")
    assert r.status_code == 200
    assert any(e["success"] and e["email"] == org["admin_email"] for e in r.json())


def test_failed_login_is_recorded(make_org, client):
    org = make_org()
    client.post("/v1/auth/login",
                json={"email": org["admin_email"], "password": "wrongpass"})
    db = SessionLocal()
    try:
        n = db.execute(text("SELECT count(*) FROM login_events "
                            "WHERE success = false AND email = :e"),
                       {"e": org["admin_email"]}).scalar()
        assert n >= 1
    finally:
        db.close()


def test_login_history_requires_admin(make_org, add_user, login):
    org = make_org()
    add_user(org["org_id"], "member@test.dev", "memberpass123", role="member")
    c = login("member@test.dev", "memberpass123")
    assert c.get("/v1/auth/login-history").status_code == 403
