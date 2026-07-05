"""Workspace/org soft-deletion (Phase 5 · 5D.3).

An org admin soft-deletes their workspace (organizations.deleted_at). It's then
locked out everywhere — the SDK Bearer key, NEW logins, and EXISTING sessions —
reversibly (the row and its data are retained). confirm_name must match the
workspace name (accidental-deletion guard); only an admin may do it.
"""

from __future__ import annotations


def test_soft_delete_locks_out_key_and_new_login(make_org, login, client):
    org = make_org()
    admin = login(org["admin_email"], org["admin_password"])

    # wrong confirmation name → rejected, nothing deleted
    assert admin.post("/v1/account/delete",
                      json={"confirm_name": "not-the-name"}).status_code == 400

    r = admin.post("/v1/account/delete", json={"confirm_name": org["name"]})
    assert r.status_code == 200

    # SDK Bearer key is now refused
    assert client.get("/v1/logs", headers=org["auth"]).status_code == 403
    assert client.post("/v1/logs/batch", json=[], headers=org["auth"]).status_code == 403
    # a NEW login is refused
    assert client.post("/v1/auth/login", json={
        "email": org["admin_email"], "password": org["admin_password"]}).status_code == 403


def test_soft_delete_blocks_existing_sessions(make_org, login):
    org = make_org()
    deleter = login(org["admin_email"], org["admin_password"])
    other = login(org["admin_email"], org["admin_password"])   # a second, still-open session

    assert deleter.post("/v1/account/delete",
                        json={"confirm_name": org["name"]}).status_code == 200

    # the other still-open session is now refused too
    assert other.get("/v1/auth/me").status_code in (401, 403)


def test_only_admin_can_delete(make_org, add_user, login):
    org = make_org()
    add_user(org["org_id"], "member@test.dev", "memberpass123", role="member")
    member = login("member@test.dev", "memberpass123")
    assert member.post("/v1/account/delete",
                       json={"confirm_name": org["name"]}).status_code == 403
