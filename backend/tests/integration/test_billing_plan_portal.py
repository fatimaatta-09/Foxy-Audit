"""P1 · §E — current-plan view + billing-portal session.

The portal needs a live Stripe customer + keys, so the happy path isn't
reachable on the local stack; we assert the guards (auth, admin-only, and the
graceful 503/400 when billing isn't configured / no customer yet).
"""
from __future__ import annotations

from app.db import SessionLocal
from app.models import Organization


def test_plan_view_free_org(make_org, client):
    org = make_org()
    r = client.get("/v1/billing/plan", headers=org["auth"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert set(d) == {"plan_tier", "subscription_status", "trial_ends_at",
                      "monthly_log_quota", "has_billing_account"}
    assert d["has_billing_account"] is False


def test_plan_view_reflects_stripe_customer(make_org, client):
    org = make_org()
    db = SessionLocal()
    try:
        o = db.get(Organization, org["org_id"])
        o.plan_tier = "pro"
        o.stripe_customer_id = "cus_test123"
        o.subscription_status = "active"
        db.commit()
    finally:
        db.close()
    d = client.get("/v1/billing/plan", headers=org["auth"]).json()
    assert d["plan_tier"] == "pro" and d["has_billing_account"] is True
    assert d["subscription_status"] == "active"


def test_plan_requires_auth(client):
    assert client.get("/v1/billing/plan").status_code == 401


def test_portal_requires_admin(make_org, add_user, login):
    org = make_org()
    add_user(org["org_id"], "member@test.dev", "memberpass1", role="member")
    c = login("member@test.dev", "memberpass1")
    assert c.post("/v1/billing/portal").status_code == 403


def test_portal_requires_login(client):
    assert client.post("/v1/billing/portal").status_code == 401


def test_portal_graceful_without_billing(make_org, login):
    """Admin, but billing isn't configured locally (503) or there's no Stripe
    customer yet (400) — never a 500 and never a 200 with a bogus URL."""
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    r = c.post("/v1/billing/portal")
    assert r.status_code in (400, 503), r.text
