"""P3 — customer device sessions (remember-me + active-devices + revoke + log-out-everywhere).

Covers: login mints a DB session with a this-device flag and NO token_hash leak; remember-me gets a
longer expiry than the 12h default; revoke kills another device; logout-all kills every device
including this one; logout revokes the current row; you can revoke only your OWN sessions (same-org
other-user → 404); cross-org sessions are RLS-invisible (→ 404); endpoints require a session; revoke
writes an account_actions audit row.
"""

from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

from app.main import app


def _device(email: str, password: str, remember: bool = False) -> TestClient:
    """A fresh logged-in client (its own cookie jar) = one device."""
    c = TestClient(app)
    r = c.post("/v1/auth/login",
               json={"email": email, "password": password, "remember_me": remember})
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    csrf = c.cookies.get("foxy_csrf")
    if csrf:
        c.headers.update({"X-CSRF-Token": csrf})
    return c


def _ttl(item: dict) -> float:
    return (datetime.fromisoformat(item["expires_at"])
            - datetime.fromisoformat(item["created_at"])).total_seconds()


def test_login_mints_session_and_no_secret_leak(make_org):
    o = make_org()
    c = _device(o["admin_email"], o["admin_password"])
    d = c.get("/v1/auth/sessions").json()
    assert len(d["items"]) == 1 and d["items"][0]["current"] is True
    for s in d["items"]:                         # token_hash must never be serialized
        assert "token_hash" not in s


def test_two_sessions_and_revoke_kills_the_other(make_org):
    o = make_org()
    c1 = _device(o["admin_email"], o["admin_password"])       # device A
    c2 = _device(o["admin_email"], o["admin_password"])       # device B
    d = c1.get("/v1/auth/sessions").json()
    assert len(d["items"]) == 2 and sum(1 for x in d["items"] if x["current"]) == 1
    other = next(x for x in d["items"] if not x["current"])
    assert c1.post(f"/v1/auth/sessions/{other['id']}/revoke").status_code == 200
    assert c2.get("/v1/auth/me").status_code == 401           # revoked device is out
    assert len(c1.get("/v1/auth/sessions").json()["items"]) == 1


def test_logout_all_revokes_everything(make_org):
    o = make_org()
    c1 = _device(o["admin_email"], o["admin_password"])
    c2 = _device(o["admin_email"], o["admin_password"])
    r = c1.post("/v1/auth/logout-all")
    assert r.status_code == 200 and r.json()["revoked"] >= 2
    assert c1.get("/v1/auth/me").status_code == 401           # this device out too
    assert c2.get("/v1/auth/me").status_code == 401


def test_logout_revokes_current_device(make_org):
    o = make_org()
    c = _device(o["admin_email"], o["admin_password"])
    assert c.get("/v1/auth/me").status_code == 200
    assert c.post("/v1/auth/logout").status_code == 200
    assert c.get("/v1/auth/me").status_code == 401            # row revoked + cookie cleared


def test_remember_me_gets_longer_expiry(make_org):
    o = make_org()
    _device(o["admin_email"], o["admin_password"], remember=False)     # 12h device
    lc = _device(o["admin_email"], o["admin_password"], remember=True)  # 30d device
    items = lc.get("/v1/auth/sessions").json()["items"]
    cur = next(x for x in items if x["current"])              # the remember-me (30d) one
    oth = next(x for x in items if not x["current"])          # the default (12h) one
    assert _ttl(cur) > _ttl(oth) * 5                          # 30d ≫ 12h


def test_sessions_endpoints_require_auth(client):
    assert client.get("/v1/auth/sessions").status_code == 401
    assert client.post("/v1/auth/logout-all").status_code in (401, 403)


def test_can_only_revoke_your_own_session(make_org, add_user):
    o = make_org()
    add_user(o["org_id"], "bob@test.dev", "bobpass123", role="member")
    admin = _device(o["admin_email"], o["admin_password"])
    bob = _device("bob@test.dev", "bobpass123")
    bob_sid = bob.get("/v1/auth/sessions").json()["items"][0]["id"]
    # same org, different user → not your session → 404 (and bob stays logged in)
    assert admin.post(f"/v1/auth/sessions/{bob_sid}/revoke").status_code == 404
    assert bob.get("/v1/auth/me").status_code == 200


def test_cross_org_session_is_invisible(make_org):
    a = make_org()
    b = make_org()
    ca = _device(a["admin_email"], a["admin_password"])
    cb = _device(b["admin_email"], b["admin_password"])
    a_sid = ca.get("/v1/auth/sessions").json()["items"][0]["id"]
    # org B cannot revoke org A's session (RLS-invisible → 404)
    assert cb.post(f"/v1/auth/sessions/{a_sid}/revoke").status_code == 404
    assert ca.get("/v1/auth/me").status_code == 200


def test_revoke_writes_audit_row(make_org):
    o = make_org()
    c1 = _device(o["admin_email"], o["admin_password"])
    _device(o["admin_email"], o["admin_password"])
    other = next(x for x in c1.get("/v1/auth/sessions").json()["items"] if not x["current"])
    assert c1.post(f"/v1/auth/sessions/{other['id']}/revoke").status_code == 200
    audit = c1.get("/v1/account/audit").json()
    assert any(a.get("action") == "auth.session_revoke" for a in audit)
