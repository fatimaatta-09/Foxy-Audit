"""Generic staff data browser (Phase 4 #3) — view/search/edit/delete across
the platform's tables, gated by a strict server-side per-table allowlist.

The hard security invariant this file proves: audit_logs, admin_actions,
chain_anchors, and stripe_events can NEVER be mutated through this surface
(hash-chain integrity, append-only audit trail, external-receipt tables), no
matter what role is calling. Every other mutation must (a) reject fields not
on that table's editable allowlist and (b) write exactly one admin_actions row.
"""

from __future__ import annotations

import hashlib

from app.db import SessionLocal
from app.models import AdminAction, ApiKey, MarketingLead, User


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


LOCKED_TABLES = ("audit_logs", "admin_actions", "chain_anchors", "stripe_events")


# ─────────────────────────────── auth / role gates ────────────────────────────

def test_requires_staff_session(client):
    assert client.get("/admin/v1/data/tables").status_code == 401
    assert client.get("/admin/v1/data/marketing_leads").status_code == 401
    assert client.patch("/admin/v1/data/marketing_leads/x", json={"fields": {}}).status_code == 401
    assert client.delete("/admin/v1/data/marketing_leads/x").status_code == 401


def test_customer_session_rejected(make_org, login):
    a = make_org()
    ca = login(a["admin_email"], a["admin_password"])
    assert ca.get("/admin/v1/data/tables").status_code == 401


def test_viewer_can_read_but_not_mutate(make_staff, staff_login, client):
    s = make_staff(role="viewer")
    cv = staff_login(s["email"], s["password"])
    assert cv.get("/admin/v1/data/tables").status_code == 200
    assert cv.get("/admin/v1/data/marketing_leads").status_code == 200

    client.post("/v1/leads", json={"email": "viewer-target@corp.com"})
    db = SessionLocal()
    lead_id = str(db.query(MarketingLead).filter(
        MarketingLead.email == "viewer-target@corp.com").one().id)
    db.close()

    assert cv.patch(f"/admin/v1/data/marketing_leads/{lead_id}",
                     json={"fields": {"status": "trial"}}).status_code == 403
    assert cv.delete(f"/admin/v1/data/marketing_leads/{lead_id}").status_code == 403


def test_unknown_table_404s(make_staff, staff_login):
    s = make_staff(role="operator")
    co = staff_login(s["email"], s["password"])
    assert co.get("/admin/v1/data/not_a_real_table").status_code == 404
    assert co.patch("/admin/v1/data/not_a_real_table/x", json={"fields": {}}).status_code == 404
    assert co.delete("/admin/v1/data/not_a_real_table/x").status_code == 404


# ───────────────────────── the 4 tables that can NEVER mutate ─────────────────

def test_locked_tables_reject_patch_and_delete(make_staff, staff_login):
    s = make_staff(role="superadmin")   # even the highest role — this must not matter
    cs = staff_login(s["email"], s["password"])
    for table in LOCKED_TABLES:
        assert cs.get(f"/admin/v1/data/{table}").status_code == 200, f"{table} should still be viewable"
        assert cs.patch(f"/admin/v1/data/{table}/00000000-0000-0000-0000-000000000000",
                         json={"fields": {"status": "x"}}).status_code == 403, f"{table} must reject PATCH"
        assert cs.delete(f"/admin/v1/data/{table}/00000000-0000-0000-0000-000000000000"
                          ).status_code == 403, f"{table} must reject DELETE"


def test_organizations_and_staff_users_are_view_only_here(make_staff, staff_login, make_org):
    """These have DEDICATED mutation endpoints (suspend/enable, disable) — the
    generic browser must not offer a second, uncontrolled path to change them."""
    s = make_staff(role="superadmin")
    cs = staff_login(s["email"], s["password"])
    a = make_org()
    assert cs.get("/admin/v1/data/organizations").status_code == 200
    assert cs.patch(f"/admin/v1/data/organizations/{a['org_id']}",
                     json={"fields": {"suspended": True}}).status_code == 403
    assert cs.get("/admin/v1/data/staff_users").status_code == 200
    assert cs.patch(f"/admin/v1/data/staff_users/{s['id']}",
                     json={"fields": {"platform_role": "superadmin"}}).status_code == 403


def test_api_key_status_is_not_editable_here(make_org, make_staff, staff_login):
    """Regression: status and revoked_at are a paired field the audited
    DELETE /v1/keys/{id} route sets together. Editing status alone here would
    silently un-revoke a live credential without touching revoked_at."""
    a = make_org()
    db = SessionLocal()
    try:
        key_id = str(db.query(ApiKey).filter(ApiKey.org_id == a["org_id"]).one().id)
    finally:
        db.close()
    co = staff_login(make_staff(role="superadmin")["email"], "staffpass123")
    r = co.patch(f"/admin/v1/data/api_keys/{key_id}", json={"fields": {"status": "active"}})
    assert r.status_code == 403


# ────────────────────────── sensitive fields never leak ───────────────────────

def test_password_and_key_hashes_never_returned(make_org, make_staff, staff_login):
    a = make_org()
    co = staff_login(make_staff(role="operator")["email"], "staffpass123")
    users_resp = co.get("/admin/v1/data/users?q=" + a["admin_email"]).json()
    assert users_resp["rows"], "expected the seeded admin user to be found"
    assert "password_hash" not in users_resp["rows"][0]

    keys_resp = co.get("/admin/v1/data/api_keys").json()
    if keys_resp["rows"]:
        assert "key_hash" not in keys_resp["rows"][0]

    orgs_resp = co.get("/admin/v1/data/organizations").json()
    assert "api_key_hash" not in orgs_resp["rows"][0]


# ───────────────────────────── editable-field allowlist ───────────────────────

def test_edit_rejects_fields_outside_the_allowlist(make_org, make_staff, staff_login):
    a = make_org()
    co = staff_login(make_staff(role="operator")["email"], "staffpass123")
    db = SessionLocal()
    user_id = str(db.query(User).filter(User.org_id == a["org_id"]).one().id)
    db.close()

    r = co.patch(f"/admin/v1/data/users/{user_id}",
                 json={"fields": {"password_hash": "hacked"}})
    assert r.status_code == 400
    r2 = co.patch(f"/admin/v1/data/users/{user_id}",
                  json={"fields": {"role": "member"}})
    assert r2.status_code == 200


def test_edit_writes_one_admin_action_and_applies(client, make_staff, staff_login):
    client.post("/v1/leads", json={"email": "edit-target@corp.com", "name": "Old Name"})
    db = SessionLocal()
    lead = db.query(MarketingLead).filter(MarketingLead.email == "edit-target@corp.com").one()
    lead_id = str(lead.id)
    db.close()

    co = staff_login(make_staff(role="operator")["email"], "staffpass123")
    r = co.patch(f"/admin/v1/data/marketing_leads/{lead_id}",
                 json={"fields": {"name": "New Name", "status": "trial"}})
    assert r.status_code == 200

    db = SessionLocal()
    lead = db.query(MarketingLead).filter(MarketingLead.id == lead.id).one()
    assert lead.name == "New Name" and lead.status == "trial"
    actions = db.query(AdminAction).filter(AdminAction.target_id == lead_id).all()
    assert len(actions) == 1
    assert actions[0].action == "data.edit.marketing_leads"
    db.close()


# ────────────────────────────────── delete ─────────────────────────────────────

def test_delete_removes_row_and_writes_snapshot(client, make_staff, staff_login):
    client.post("/v1/leads", json={"email": "delete-target@corp.com"})
    db = SessionLocal()
    lead_id = str(db.query(MarketingLead).filter(
        MarketingLead.email == "delete-target@corp.com").one().id)
    db.close()

    co = staff_login(make_staff(role="operator")["email"], "staffpass123")
    r = co.delete(f"/admin/v1/data/marketing_leads/{lead_id}")
    assert r.status_code == 200

    db = SessionLocal()
    assert db.query(MarketingLead).filter(MarketingLead.id == lead_id).first() is None
    action = db.query(AdminAction).filter(
        AdminAction.target_id == lead_id, AdminAction.action == "data.delete.marketing_leads").one()
    assert action.detail and "snapshot" in action.detail
    assert action.detail["snapshot"]["email"] == "delete-target@corp.com"
    db.close()


# ──────────────────────────────── search / filter ──────────────────────────────

def test_free_text_search_matches_substring(client, make_staff, staff_login):
    client.post("/v1/leads", json={"email": "findme@uniquecorp.com", "company": "Unique Corp"})
    client.post("/v1/leads", json={"email": "other@example.com", "company": "Example Inc"})
    co = staff_login(make_staff(role="viewer")["email"], "staffpass123")
    r = co.get("/admin/v1/data/marketing_leads?q=uniquecorp")
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert len(rows) == 1 and rows[0]["email"] == "findme@uniquecorp.com"


def test_column_filter_exact_match(make_org, client, make_staff, staff_login):
    a, b = make_org(), make_org()
    rows = [{"prompt_hash": _h("p"), "response_hash": _h("r"), "token_count": 1, "policy_tag": "x"}]
    assert client.post("/v1/logs/batch", json=rows, headers=a["auth"]).status_code == 202
    co = staff_login(make_staff(role="viewer")["email"], "staffpass123")
    r = co.get(f"/admin/v1/data/audit_logs?col_org_id={a['org_id']}")
    assert r.status_code == 200
    result = r.json()["rows"]
    assert len(result) == 1
    assert all(row["org_id"] == a["org_id"] for row in result)


def test_col_filter_cannot_target_hash_columns(make_org, make_staff, staff_login):
    """Regression: col_password_hash / col_key_hash must never be usable as an
    exact-match filter — even though the hash value itself is stripped from
    the response body, filtering on it would let a caller confirm a guessed
    hash matches a real row (total>0) without ever seeing the hash returned,
    a side-channel oracle around the same secret _NEVER_EXPOSE protects."""
    a = make_org()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.org_id == a["org_id"]).one()
        real_password_hash = user.password_hash
        key_row = db.query(ApiKey).filter(ApiKey.org_id == a["org_id"]).one()
        real_key_hash = key_row.key_hash
    finally:
        db.close()

    co = staff_login(make_staff(role="viewer")["email"], "staffpass123")
    baseline = co.get("/admin/v1/data/users").json()["total"]
    filtered = co.get(f"/admin/v1/data/users?col_password_hash={real_password_hash}").json()
    assert filtered["total"] == baseline, "the filter must be ignored, not applied"

    baseline_keys = co.get("/admin/v1/data/api_keys").json()["total"]
    filtered_keys = co.get(f"/admin/v1/data/api_keys?col_key_hash={real_key_hash}").json()
    assert filtered_keys["total"] == baseline_keys, "the filter must be ignored, not applied"
