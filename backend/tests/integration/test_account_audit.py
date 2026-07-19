"""P2 · §D/§H/§E — account audit log, full/GDPR export, invoice PDF link.

Audit rows are written atomically with the mutation, admin-only + org-isolated;
export returns a content-blind bundle; the invoice link degrades gracefully.
"""
from __future__ import annotations

import uuid

from app.db import SessionLocal
from app.models import User


def _uid(org_id, email):
    db = SessionLocal()
    try:
        return str(db.query(User).filter(User.org_id == org_id, User.email == email).one().id)
    finally:
        db.close()


# ── audit log ──────────────────────────────────────────────────────────────

def test_mutations_are_audited_and_listed(make_org, add_user, login):
    org = make_org()
    add_user(org["org_id"], "m@corp.com", "memberpass1", role="member")
    mid = _uid(org["org_id"], "m@corp.com")
    c = login(org["admin_email"], org["admin_password"])
    c.post("/v1/keys", json={"name": "audited-key"})
    c.post(f"/v1/auth/users/{mid}/role", json={"role": "admin"})
    actions = {a["action"] for a in c.get("/v1/account/audit").json()}
    assert "key.create" in actions and "member.role_change" in actions


def test_audit_requires_admin(make_org, add_user, login):
    org = make_org()
    add_user(org["org_id"], "member2@corp.com", "memberpass1", role="member")
    c = login("member2@corp.com", "memberpass1")
    assert c.get("/v1/account/audit").status_code == 403


def test_audit_is_tenant_isolated(make_org, login):
    org_a = make_org()
    org_b = make_org()
    login(org_b["admin_email"], org_b["admin_password"]).post("/v1/keys", json={"name": "b-key"})
    ca = login(org_a["admin_email"], org_a["admin_password"])
    ca.post("/v1/keys", json={"name": "a-key"})
    targets = [a.get("target") for a in ca.get("/v1/account/audit").json()]
    assert "a-key" in targets and "b-key" not in targets


# ── full / GDPR export ─────────────────────────────────────────────────────

def test_account_export_bundle(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    r = c.get("/v1/account/export")
    assert r.status_code == 200 and "attachment" in r.headers.get("content-disposition", "")
    b = r.json()
    for k in ("organization", "users", "policy", "api_keys", "invoices", "anchors", "ai_systems", "ledger"):
        assert k in b
    assert any(u["email"] == org["admin_email"] for u in b["users"])


def test_export_requires_admin(make_org, add_user, login):
    org = make_org()
    add_user(org["org_id"], "m3@corp.com", "memberpass1", role="member")
    c = login("m3@corp.com", "memberpass1")
    assert c.get("/v1/account/export").status_code == 403


# ── invoice link ───────────────────────────────────────────────────────────

def test_invoice_link_degrades_without_stripe(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    # billing unconfigured locally -> 503 before any lookup
    r = c.get(f"/v1/invoices/{uuid.uuid4()}/link")
    assert r.status_code == 503


def test_invoice_link_requires_login(client):
    assert client.get(f"/v1/invoices/{uuid.uuid4()}/link").status_code == 401
