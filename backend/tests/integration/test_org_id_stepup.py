"""P3 §7.1 · the org-ID reveal is gated for real, not decorated.

The design this replaces shipped `org_id` inside `/v1/auth/me` and masked it in
the DOM. Every assertion here exists because that arrangement passed a "the eye
button is gated" review while protecting nothing — the value was already in the
network response, two clicks away in devtools.

So the guards are written against the WIRE, not the UI: if `org_id` ever creeps
back into an un-gated payload, one of these fails.
"""

from __future__ import annotations

import pytest

# Every un-gated path that authenticates a dashboard session. A session user who
# hits any of these must not learn the workspace id from the response body.
SESSION_PAYLOAD_PATHS = ("/v1/auth/me",)


def test_me_does_not_carry_the_org_id(make_org, login):
    """Guard 1. The whole point of the change."""
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    body = c.get("/v1/auth/me").json()
    assert body["email"] == org["admin_email"]
    assert "org_id" not in body, (
        "org_id is back in /v1/auth/me — the DOM mask over it is decoration, "
        "and the step-up gate on /v1/account/org-id is now bypassable")


def test_no_sign_in_path_hands_back_the_org_id(make_org, client):
    """Guard 1b. Removing it from `me` alone would be pointless: a client could
    cache it from the login response instead. Password login is the path a
    browser actually takes; MFA, SSO and handoff are covered by their own
    suites' `org_id not in` assertions."""
    org = make_org()
    r = client.post("/v1/auth/login",
                    json={"email": org["admin_email"], "password": org["admin_password"]})
    assert r.status_code == 200, r.text
    assert "org_id" not in r.json(), "login still leaks the workspace id"
    for path in SESSION_PAYLOAD_PATHS:
        assert "org_id" not in client.get(path).json(), f"{path} still leaks it"


def test_reveal_refuses_without_a_step_up_grant(make_org, login):
    """Guard 2a. A plain authenticated session is NOT enough."""
    org = make_org()
    c = login(org["admin_email"], org["admin_password"], with_step_up=False)
    r = c.post("/v1/account/org-id")
    assert r.status_code == 403
    assert r.json()["detail"] == "step_up_required"
    # And it refuses without leaking the value it is refusing to hand over.
    assert org["org_id"] not in r.text


def test_reveal_succeeds_with_a_step_up_grant(make_org, login, step_up_user):
    """Guard 2b. The gate opens for the right person — a gate that never opens
    is as useless as one that never closes."""
    org = make_org()
    c = login(org["admin_email"], org["admin_password"], with_step_up=False)
    assert c.post("/v1/account/org-id").status_code == 403
    step_up_user(c)
    r = c.post("/v1/account/org-id")
    assert r.status_code == 200, r.text
    assert r.json()["org_id"] == org["org_id"]


def test_the_refreshed_session_cookie_survives_the_step_up_round_trip(
        make_org, login, step_up_user):
    """Guard 3. The grant lives INSIDE the session cookie, so `/confirm` hands
    back a re-signed cookie. A client that drops it loses the grant the instant
    it is minted — this has bitten the product before, and the symptom is a
    step-up prompt that reappears immediately after being satisfied.

    Two reveals with one code is the observable proof the grant persisted."""
    org = make_org()
    c = login(org["admin_email"], org["admin_password"], with_step_up=False)
    step_up_user(c)
    first = c.post("/v1/account/org-id")
    second = c.post("/v1/account/org-id")
    assert first.status_code == 200 and second.status_code == 200, (
        f"grant evaporated between calls: {first.status_code}/{second.status_code}")
    assert first.json()["org_id"] == second.json()["org_id"] == org["org_id"]
    # The session must still be a working session, not just a grant carrier.
    assert c.get("/v1/auth/me").status_code == 200


def test_reveal_is_recorded_in_the_account_trail(make_org, login, step_up_user):
    """Who un-masked the workspace id, and when. A product selling tamper-evident
    audit should not make its own sensitive reveal the one unlogged action."""
    org = make_org()
    c = login(org["admin_email"], org["admin_password"], with_step_up=False)
    step_up_user(c)
    assert c.post("/v1/account/org-id").status_code == 200
    actions = [row["action"] for row in c.get("/v1/account/audit").json()]
    assert "account.org_id_reveal" in actions


def test_reveal_is_org_scoped(make_org, add_user, login, step_up_user):
    """Two tenants, one endpoint: each session gets its OWN id and never the
    other's."""
    a, b = make_org(), make_org()
    add_user(a["org_id"], "same@test.dev", "alphaPass111", role="admin")
    add_user(b["org_id"], "same@test.dev", "bravoPass222", role="admin")
    ca = login("same@test.dev", "alphaPass111")
    cb = login("same@test.dev", "bravoPass222")
    assert ca.post("/v1/account/org-id").json()["org_id"] == a["org_id"]
    assert cb.post("/v1/account/org-id").json()["org_id"] == b["org_id"]


def test_reveal_requires_authentication_at_all(client):
    """No session, no reveal — and specifically not a 403 that would imply the
    endpoint exists for anonymous callers who merely lack a grant."""
    assert client.post("/v1/account/org-id").status_code == 401


@pytest.mark.parametrize("field", ["password_hash", "key_hash", "token_hash"])
def test_reveal_serializes_nothing_sensitive(make_org, login, step_up_user, field):
    """The response is one field wide. This fails loudly if it ever grows."""
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    body = c.post("/v1/account/org-id").json()
    assert set(body) == {"org_id"}
    assert field not in c.post("/v1/account/org-id").text
