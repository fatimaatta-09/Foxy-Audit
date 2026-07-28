"""Security surfaces in Settings (P3 §7).

Most of §7 was already shipped — the device list, per-session revoke, log-out-
everywhere and the admin login-history view all exist and are wired into the SPA.
What was missing is covered here, plus the checks §7.4 asks for: that MFA
enrol/disable still behaves after the §1 session work, and that a step-up grant
survives inside the refreshed session cookie.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

UA_A = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0 Safari/537.36"
UA_B = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Version/17.0 Safari/605.1.15"


def _grant_step_up(c: TestClient) -> None:
    import app.mfa as _mfa
    orig = _mfa.new_otp
    _mfa.new_otp = lambda: "000000"
    try:
        assert c.post("/v1/auth/step-up/request").status_code == 200
        assert c.post("/v1/auth/step-up/confirm", json={"code": "000000"}).status_code == 200
    finally:
        _mfa.new_otp = orig


# ── §7.3 · a member can read their OWN login history ────────────────────────

def test_member_can_read_their_own_login_history(make_org, add_user, login):
    """The org-wide view is admin-only. Without this, a member had no way to
    check their own account for a sign-in they do not recognise."""
    org = make_org()
    add_user(org["org_id"], "mem@test.dev", "memberpass1", role="member")
    c = login("mem@test.dev", "memberpass1")
    assert c.get("/v1/auth/login-history").status_code == 403      # org-wide stays admin-only
    r = c.get("/v1/auth/login-history/me")
    assert r.status_code == 200
    rows = r.json()
    assert rows and all(row["email"] == "mem@test.dev" for row in rows)


def test_own_login_history_never_leaks_a_colleague(make_org, add_user, login):
    org = make_org()
    add_user(org["org_id"], "alice@test.dev", "alicepass123", role="member")
    add_user(org["org_id"], "bob@test.dev", "bobpass12345", role="member")
    login("bob@test.dev", "bobpass12345")
    c = login("alice@test.dev", "alicepass123")
    emails = {row["email"] for row in c.get("/v1/auth/login-history/me").json()}
    assert emails == {"alice@test.dev"}


def test_own_login_history_requires_a_session(client):
    assert client.get("/v1/auth/login-history/me").status_code == 401


def test_login_history_shows_failed_attempts(make_org, login, client):
    """A failed attempt on your account is the half of login history worth
    reading. Failures used to be recorded with org_id NULL, so the org-scoped
    admin view never showed one."""
    org = make_org()
    client.post("/v1/auth/login",
                json={"email": org["admin_email"], "password": "not-the-password"})
    c = login(org["admin_email"], org["admin_password"])
    assert any(not row["success"] for row in c.get("/v1/auth/login-history").json())
    assert any(not row["success"] for row in c.get("/v1/auth/login-history/me").json())


def test_failure_on_a_shared_address_is_not_attributed(make_org, add_user, login, client):
    """When one address exists in several tenants, guessing which one a failed
    attempt was aimed at would leak that the other tenant exists."""
    a, b = make_org(), make_org()
    add_user(a["org_id"], "shared@test.dev", "alpha-pass-11", role="admin")
    add_user(b["org_id"], "shared@test.dev", "bravo-pass-22", role="admin")
    client.post("/v1/auth/login", json={"email": "shared@test.dev", "password": "nope-nope-1"})
    c = login("shared@test.dev", "alpha-pass-11")
    assert all(row["success"] for row in c.get("/v1/auth/login-history").json())


# ── §7.2 · device list + revoke (already shipped — pinned against regression) ─

def test_device_list_flags_this_device_and_revoke_works(make_org, login):
    org = make_org()
    a = TestClient(app)
    a.post("/v1/auth/login", json={"email": org["admin_email"], "password": org["admin_password"]},
           headers={"user-agent": UA_A})
    csrf = a.cookies.get("foxy_csrf")
    if csrf:
        a.headers.update({"X-CSRF-Token": csrf})
    b = TestClient(app)
    b.post("/v1/auth/login", json={"email": org["admin_email"], "password": org["admin_password"]},
           headers={"user-agent": UA_B})

    items = a.get("/v1/auth/sessions").json()["items"]
    assert len(items) == 2
    assert sum(1 for i in items if i["current"]) == 1
    for i in items:
        assert "token_hash" not in i          # never serialised

    other = next(i for i in items if not i["current"])
    assert a.post(f"/v1/auth/sessions/{other['id']}/revoke").status_code == 200
    assert b.get("/v1/auth/me").status_code == 401       # the revoked device is out
    assert a.get("/v1/auth/me").status_code == 200       # this one is not


def test_logout_everywhere_ends_every_session(make_org):
    org = make_org()
    a, b = TestClient(app), TestClient(app)
    for c in (a, b):
        c.post("/v1/auth/login",
               json={"email": org["admin_email"], "password": org["admin_password"]})
        csrf = c.cookies.get("foxy_csrf")
        if csrf:
            c.headers.update({"X-CSRF-Token": csrf})
    assert a.post("/v1/auth/logout-all").status_code == 200
    assert a.get("/v1/auth/me").status_code == 401
    assert b.get("/v1/auth/me").status_code == 401


# ── §7.1 · the step-up grant lives in the refreshed cookie ──────────────────

def test_step_up_grant_persists_across_requests(make_org):
    """The grant rides inside the session cookie. If the refreshed cookie is not
    kept, every danger action re-prompts and the gate becomes noise people click
    through without reading."""
    org = make_org()
    c = TestClient(app)
    c.post("/v1/auth/login",
           json={"email": org["admin_email"], "password": org["admin_password"]})
    csrf = c.cookies.get("foxy_csrf")
    if csrf:
        c.headers.update({"X-CSRF-Token": csrf})

    assert c.post("/v1/auth/change-password",
                  json={"current_password": org["admin_password"],
                        "new_password": "unused-pass-1"}).status_code == 403
    _grant_step_up(c)
    for _ in range(3):
        assert c.get("/v1/auth/sessions").status_code == 200
    assert c.post("/v1/auth/change-password",
                  json={"current_password": org["admin_password"],
                        "new_password": "granted-pass-1"}).status_code == 200


def test_a_fresh_session_does_not_inherit_a_grant(make_org):
    """Logging in again must not carry a previous step-up over."""
    org = make_org()
    c = TestClient(app)
    c.post("/v1/auth/login",
           json={"email": org["admin_email"], "password": org["admin_password"]})
    csrf = c.cookies.get("foxy_csrf")
    if csrf:
        c.headers.update({"X-CSRF-Token": csrf})
    _grant_step_up(c)
    c.post("/v1/auth/logout")

    c.post("/v1/auth/login",
           json={"email": org["admin_email"], "password": org["admin_password"]})
    csrf = c.cookies.get("foxy_csrf")
    if csrf:
        c.headers.update({"X-CSRF-Token": csrf})
    assert c.post("/v1/auth/change-password",
                  json={"current_password": org["admin_password"],
                        "new_password": "should-not-work"}).status_code == 403


# ── §7.4 · MFA still behaves after the §1 session changes ───────────────────

def test_mfa_enrol_and_disable_still_work(make_org, login):
    import app.mfa as _mfa
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])

    orig = _mfa.new_otp
    _mfa.new_otp = lambda: "111111"
    try:
        assert c.post("/v1/auth/mfa/enroll").status_code == 200
        assert c.post("/v1/auth/mfa/enable", json={"code": "111111"}).status_code == 200
    finally:
        _mfa.new_otp = orig

    # Enrolled: a password login now stops at the code step.
    fresh = TestClient(app)
    r = fresh.post("/v1/auth/login",
                   json={"email": org["admin_email"], "password": org["admin_password"]})
    assert r.status_code == 200 and r.json().get("mfa_required") is True
    assert fresh.get("/v1/auth/me").status_code == 401       # no session until verified

    assert c.post("/v1/auth/mfa/disable",
                  json={"password": org["admin_password"]}).status_code == 200
    again = TestClient(app)
    assert again.post("/v1/auth/login",
                      json={"email": org["admin_email"],
                            "password": org["admin_password"]}).json().get("mfa_required") is None
    assert again.get("/v1/auth/me").status_code == 200


def test_mfa_login_mints_a_device_session(make_org, login):
    """The MFA branch establishes its own session — it must appear in the device
    list like any other, or a code-verified sign-in is invisible and unrevokable."""
    import app.mfa as _mfa
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    orig = _mfa.new_otp
    _mfa.new_otp = lambda: "222222"
    try:
        c.post("/v1/auth/mfa/enroll")
        c.post("/v1/auth/mfa/enable", json={"code": "222222"})
        fresh = TestClient(app)
        fresh.post("/v1/auth/login",
                   json={"email": org["admin_email"], "password": org["admin_password"]},
                   headers={"user-agent": UA_B})
        assert fresh.post("/v1/auth/mfa",
                          json={"email": org["admin_email"], "code": "222222"},
                          headers={"user-agent": UA_B}).status_code == 200
    finally:
        _mfa.new_otp = orig
    assert fresh.get("/v1/auth/me").status_code == 200
    agents = [i["user_agent"] for i in fresh.get("/v1/auth/sessions").json()["items"]]
    assert any(a and UA_B in a for a in agents)
