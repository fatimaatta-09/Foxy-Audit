"""P15 — step-up (emailed code) on danger actions.

Covers: a gated action 403s (step_up_required) without a grant; request + confirm (patched OTP) mints a
grant so the action succeeds; one grant covers a burst; wrong code → 400 and the gate stays closed;
step-up endpoints require auth; the response never leaks the code or its hash; codes are per-user.
"""

from __future__ import annotations

import app.mfa as _mfa


def _request_code(c, code="000000"):
    orig = _mfa.new_otp
    _mfa.new_otp = lambda: code
    try:
        assert c.post("/v1/auth/step-up/request").status_code == 200
    finally:
        _mfa.new_otp = orig


def _grant(c):
    _request_code(c)
    assert c.post("/v1/auth/step-up/confirm", json={"code": "000000"}).status_code == 200


def test_gated_action_requires_step_up(make_org, login):
    o = make_org()
    c = login(o["admin_email"], o["admin_password"], with_step_up=False)
    r = c.post("/v1/account/ip-allowlist", json={"allowlist": ""})
    assert r.status_code == 403 and r.json()["detail"] == "step_up_required"


def test_step_up_grants_and_action_succeeds(make_org, login):
    o = make_org()
    c = login(o["admin_email"], o["admin_password"], with_step_up=False)
    assert c.post("/v1/account/ip-allowlist", json={"allowlist": ""}).status_code == 403
    _grant(c)
    assert c.post("/v1/account/ip-allowlist", json={"allowlist": ""}).status_code == 200


def test_one_grant_covers_a_burst(make_org, login):
    o = make_org()
    c = login(o["admin_email"], o["admin_password"], with_step_up=False)
    _grant(c)
    assert c.post("/v1/account/ip-allowlist", json={"allowlist": ""}).status_code == 200
    assert c.post("/v1/account/ip-allowlist", json={"allowlist": ""}).status_code == 200  # no re-prompt


def test_wrong_code_rejected_and_gate_stays_closed(make_org, login):
    o = make_org()
    c = login(o["admin_email"], o["admin_password"], with_step_up=False)
    _request_code(c, code="111111")
    assert c.post("/v1/auth/step-up/confirm", json={"code": "000000"}).status_code == 400
    assert c.post("/v1/account/ip-allowlist", json={"allowlist": ""}).status_code == 403


def test_step_up_endpoints_require_auth(client):
    assert client.post("/v1/auth/step-up/request").status_code == 401
    assert client.post("/v1/auth/step-up/confirm", json={"code": "000000"}).status_code in (401, 403)


def test_response_never_leaks_code_or_hash(make_org, login):
    o = make_org()
    c = login(o["admin_email"], o["admin_password"], with_step_up=False)
    _mfa_orig = _mfa.new_otp
    _mfa.new_otp = lambda: "000000"
    try:
        r = c.post("/v1/auth/step-up/request")
    finally:
        _mfa.new_otp = _mfa_orig
    assert "code_hash" not in r.text and "000000" not in r.text   # neither the hash nor the code
    r2 = c.post("/v1/auth/step-up/confirm", json={"code": "000000"})
    assert "code_hash" not in r2.text


def test_codes_are_per_user(make_org, login, add_user):
    o = make_org()
    add_user(o["org_id"], "bob@test.dev", "bobpass123", role="member")
    a = login(o["admin_email"], o["admin_password"], with_step_up=False)
    _request_code(a)                                             # admin requests a "000000" code
    b = login("bob@test.dev", "bobpass123", with_step_up=False)
    assert b.post("/v1/auth/step-up/confirm", json={"code": "000000"}).status_code == 400  # not bob's
