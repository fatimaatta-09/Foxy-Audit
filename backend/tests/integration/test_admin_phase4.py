"""Integration tests for Phase 4 — Revenue/Billing, Security, Audit viewer, Leads.

Read-heavy sections plus two audited writes (Stripe replay, lead status). Verifies
replay actually RE-PROCESSES (records the invoice) and that the Stripe list never
leaks the raw payload.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    AdminAction, Invoice, LoginEvent, MarketingLead, Organization, StripeEvent,
)


def _set_org(org_id, **kw):
    db = SessionLocal()
    try:
        o = db.get(Organization, uuid.UUID(org_id))
        for k, v in kw.items():
            setattr(o, k, v)
        db.commit()
    finally:
        db.close()


def _seed_invoice(org_id, cents, status="paid", created=None, stripe_id=None):
    db = SessionLocal()
    try:
        inv = Invoice(org_id=uuid.UUID(org_id), stripe_invoice_id=stripe_id or ("in_" + uuid.uuid4().hex),
                      amount_cents=cents, status=status)
        if created is not None:
            inv.created_at = created
        db.add(inv)
        db.commit()
    finally:
        db.close()


def _seed_stripe_event(type_, status, payload):
    db = SessionLocal()
    try:
        e = StripeEvent(stripe_event_id="evt_" + uuid.uuid4().hex, type=type_, status=status, payload=payload)
        db.add(e)
        db.commit()
        db.refresh(e)
        return str(e.id)
    finally:
        db.close()


def _seed_login(email, ip, success):
    db = SessionLocal()
    try:
        db.add(LoginEvent(email=email, ip=ip, success=success))
        db.commit()
    finally:
        db.close()


def _seed_lead(email, status="new", **kw):
    db = SessionLocal()
    try:
        conv = kw.pop("converted_org_id", None)
        lead = MarketingLead(email=email, status=status,
                             converted_org_id=uuid.UUID(conv) if conv else None, **kw)
        db.add(lead)
        db.commit()
        db.refresh(lead)
        return str(lead.id)
    finally:
        db.close()


def _seed_action(staff_id, action, target_org_id=None, target_id=None):
    db = SessionLocal()
    try:
        db.add(AdminAction(staff_user_id=uuid.UUID(staff_id), action=action,
                           target_org_id=uuid.UUID(target_org_id) if target_org_id else None,
                           target_type="x", target_id=target_id))
        db.commit()
    finally:
        db.close()


# ------------------------------- billing / revenue ----------------------------

def test_revenue(make_staff, staff_login, make_org, client):
    org = make_org()
    _set_org(org["org_id"], plan_tier="pro", subscription_status="active")
    now = datetime.now(timezone.utc)
    _seed_invoice(org["org_id"], 5000, "paid", created=now)
    _seed_invoice(org["org_id"], 3000, "paid", created=now - timedelta(days=40))
    _seed_invoice(org["org_id"], 9999, "open", created=now)          # unpaid excluded
    assert client.get("/admin/v1/revenue").status_code == 401
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    d = c.get("/admin/v1/revenue").json()
    assert d["revenue_30d_cents"] == 5000
    assert sum(x["cents"] for x in d["monthly"]) == 8000
    assert d["subscriptions"].get("active") == 1
    assert any("pro" in x["plans"] for x in d["by_plan_monthly"])


def test_stripe_events_no_payload_leak(make_staff, staff_login):
    _seed_stripe_event("invoice.paid", "failed", {"secret": "should-not-leak", "data": {}})
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    r = c.get("/admin/v1/billing/stripe-events")
    assert r.status_code == 200
    assert "should-not-leak" not in r.text and "payload" not in r.text
    assert c.get("/admin/v1/billing/stripe-events?status=failed").json()["total"] >= 1


def test_replay_invoice_effect_and_audit(make_staff, staff_login, make_org):
    org = make_org()
    _set_org(org["org_id"], stripe_customer_id="cus_replay", plan_tier="pro")
    eid = _seed_stripe_event("invoice.paid", "failed", {"type": "invoice.paid", "data": {"object": {
        "customer": "cus_replay", "id": "in_replay", "amount_paid": 7000,
        "currency": "usd", "status": "paid"}}})
    v = make_staff(role="viewer")
    cv = staff_login(v["email"], v["password"])
    assert cv.post(f"/admin/v1/billing/stripe-events/{eid}/replay").status_code == 403
    op = make_staff(role="operator")
    co = staff_login(op["email"], op["password"])
    r = co.post(f"/admin/v1/billing/stripe-events/{eid}/replay")
    assert r.status_code == 200 and r.json()["result"] == "invoice_recorded"
    db = SessionLocal()
    try:
        inv = db.execute(select(Invoice).where(Invoice.stripe_invoice_id == "in_replay")).scalar_one_or_none()
        assert inv is not None and inv.amount_cents == 7000
        assert db.get(StripeEvent, uuid.UUID(eid)).status == "processed"
        assert db.query(AdminAction).filter(AdminAction.action == "stripe.replay").count() == 1
    finally:
        db.close()


def test_replay_ignored_event(make_staff, staff_login):
    eid = _seed_stripe_event("customer.created", "failed", {"type": "customer.created", "data": {"object": {}}})
    op = make_staff(role="operator")
    co = staff_login(op["email"], op["password"])
    r = co.post(f"/admin/v1/billing/stripe-events/{eid}/replay")
    assert r.status_code == 200 and r.json()["result"] == "ignored"


# ------------------------------- security ------------------------------------

def test_security_logins(make_staff, staff_login, client):
    for _ in range(6):
        _seed_login("bad@x.com", "203.0.113.9", False)
    _seed_login("ok@x.com", "198.51.100.1", True)
    assert client.get("/admin/v1/security/logins").status_code == 401
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    d = c.get("/admin/v1/security/logins?days=7").json()
    assert any(o["ip"] == "203.0.113.9" and o["failed"] >= 6 for o in d["top_offenders"])
    assert any(w["email"] == "bad@x.com" and w["failed_24h"] >= 5 for w in d["watchlist"])
    assert sum(x["failed"] for x in d["series"]) >= 6


# ------------------------------- audit viewer --------------------------------

def test_audit_filter_and_csv_export(make_staff, staff_login, make_org, client):
    op = make_staff(role="operator")
    org = make_org()
    _seed_action(op["id"], "org.suspend", target_org_id=org["org_id"], target_id=org["org_id"])
    _seed_action(op["id"], "org.enable", target_org_id=org["org_id"], target_id=org["org_id"])
    assert client.get("/admin/v1/audit").status_code == 401
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    d = c.get("/admin/v1/audit?action=org.suspend").json()
    assert d["total"] == 1 and d["items"][0]["action"] == "org.suspend"
    assert d["items"][0]["actor"] == op["email"]
    assert c.get(f"/admin/v1/audit?actor={op['email']}").json()["total"] == 2
    assert c.get(f"/admin/v1/audit?org_id={org['org_id']}").json()["total"] == 2
    r = c.get("/admin/v1/audit?format=csv")
    assert r.status_code == 200 and "text/csv" in r.headers["content-type"]
    assert "at,actor,action" in r.text


# ------------------------------- leads ---------------------------------------

def test_leads_kanban_and_status(make_staff, staff_login, client):
    l1 = _seed_lead("a@x.com", "new", company="Acme", source="enterprise")
    l2 = _seed_lead("b@x.com", "trial")
    _seed_lead("c@x.com", "converted")
    assert client.get("/admin/v1/leads").status_code == 401
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    d = c.get("/admin/v1/leads").json()
    assert d["counts"]["new"] >= 1 and d["counts"]["trial"] >= 1 and d["counts"]["converted"] >= 1
    assert any(x["email"] == "a@x.com" for x in d["buckets"]["new"])
    assert any(x["email"] == "a@x.com" for x in c.get("/admin/v1/leads?q=acme").json()["buckets"]["new"])
    # transition (operator) + audit
    op = make_staff(role="operator")
    co = staff_login(op["email"], op["password"])
    assert co.post(f"/admin/v1/leads/{l1}/status", json={"status": "nope"}).status_code == 422
    assert co.post(f"/admin/v1/leads/{l1}/status", json={"status": "trial"}).json()["lead_status"] == "trial"
    # viewer forbidden
    v = make_staff(role="viewer")
    cv = staff_login(v["email"], v["password"])
    assert cv.post(f"/admin/v1/leads/{l2}/status", json={"status": "churned"}).status_code == 403
    db = SessionLocal()
    try:
        assert db.get(MarketingLead, uuid.UUID(l1)).status == "trial"
        assert db.query(AdminAction).filter(AdminAction.action == "lead.status").count() == 1
    finally:
        db.close()
