"""P14 — profile (full_name) + preferences.

Covers: auth-gating; setting the name persists and shows in /v1/auth/me (identity drives the avatar);
preferences merge (allowed boolean keys only; unknown ignored); /v1/auth/me carries preferences;
name/prefs are per-user within an org.
"""

from __future__ import annotations


def test_profile_prefs_require_auth(client):
    assert client.put("/v1/account/profile", json={"full_name": "X"}).status_code in (401, 403)
    assert client.get("/v1/account/preferences").status_code == 401
    assert client.put("/v1/account/preferences", json={"preferences": {}}).status_code in (401, 403)


def test_set_name_shows_in_me(make_org, login):
    o = make_org()
    c = login(o["admin_email"], o["admin_password"])
    assert c.get("/v1/auth/me").json().get("full_name") is None      # unset → email identity
    r = c.put("/v1/account/profile", json={"full_name": "  Ada Lovelace  "})
    assert r.status_code == 200 and r.json()["full_name"] == "Ada Lovelace"   # trimmed
    assert c.get("/v1/auth/me").json()["full_name"] == "Ada Lovelace"
    assert c.put("/v1/account/profile", json={"full_name": ""}).json()["full_name"] is None  # cleared


def test_preferences_merge_allowed_only(make_org, login):
    o = make_org()
    c = login(o["admin_email"], o["admin_password"])
    assert c.get("/v1/account/preferences").json()["preferences"] == {}
    p = c.put("/v1/account/preferences",
              json={"preferences": {"hide_sensitive_metadata": True, "evil": "x"}}).json()["preferences"]
    assert p["hide_sensitive_metadata"] is True and "evil" not in p        # unknown key dropped
    p2 = c.put("/v1/account/preferences",
               json={"preferences": {"notify_security_alerts": True}}).json()["preferences"]
    assert p2["hide_sensitive_metadata"] is True and p2["notify_security_alerts"] is True  # merged
    assert c.get("/v1/auth/me").json()["preferences"]["hide_sensitive_metadata"] is True


def test_name_and_prefs_are_per_user(make_org, login, add_user):
    o = make_org()
    add_user(o["org_id"], "bob@test.dev", "bobpass123", role="member")
    admin = login(o["admin_email"], o["admin_password"])
    bob = login("bob@test.dev", "bobpass123")
    admin.put("/v1/account/profile", json={"full_name": "Admin A"})
    bob.put("/v1/account/profile", json={"full_name": "Bob B"})
    admin.put("/v1/account/preferences", json={"preferences": {"hide_sensitive_metadata": True}})
    assert admin.get("/v1/auth/me").json()["full_name"] == "Admin A"
    assert bob.get("/v1/auth/me").json()["full_name"] == "Bob B"
    assert bob.get("/v1/auth/me").json()["preferences"].get("hide_sensitive_metadata") in (None, False)
