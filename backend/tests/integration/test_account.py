"""Customer-facing account reads: GET /v1/invoices + GET /v1/usage (Phase 4 #2).

These are the customer dashboard's window onto the billing/usage tables that the
admin site already reads cross-org. Both must stay tenant-isolated (session OR
Bearer resolves to ONE org) and closed to the staff channel.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from app.db import SessionLocal
from app.models import Invoice, Organization


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _add_invoice(org_id: str, stripe_id: str, amount: int = 4900,
                 status: str = "paid", created_offset_days: int = 0):
    db = SessionLocal()
    try:
        db.add(Invoice(
            org_id=uuid.UUID(org_id), stripe_invoice_id=stripe_id,
            amount_cents=amount, currency="usd", status=status,
            created_at=datetime.now(timezone.utc) - timedelta(days=created_offset_days),
        ))
        db.commit()
    finally:
        db.close()


# ─────────────────────────────── /v1/invoices ────────────────────────────────

def test_invoices_requires_auth(client):
    assert client.get("/v1/invoices").status_code == 401


def test_invoices_scoped_to_own_org(make_org, login):
    a, b = make_org(), make_org()
    _add_invoice(a["org_id"], "in_a1", amount=4900, created_offset_days=1)
    _add_invoice(a["org_id"], "in_a2", amount=9900, created_offset_days=0)
    _add_invoice(b["org_id"], "in_b1", amount=1234)

    ca = login(a["admin_email"], a["admin_password"])
    rows = ca.get("/v1/invoices").json()
    assert [r["stripe_invoice_id"] for r in rows] == ["in_a2", "in_a1"]  # newest first
    assert all(r["stripe_invoice_id"] != "in_b1" for r in rows)          # no cross-tenant leak
    assert rows[0]["amount_cents"] == 9900 and rows[0]["status"] == "paid"


def test_invoices_readable_via_bearer_key(make_org, client):
    a = make_org()
    _add_invoice(a["org_id"], "in_key1")
    rows = client.get("/v1/invoices", headers=a["auth"]).json()
    assert len(rows) == 1 and rows[0]["stripe_invoice_id"] == "in_key1"


# ───────────────────────────────── /v1/usage ─────────────────────────────────

def test_usage_requires_auth(client):
    assert client.get("/v1/usage").status_code == 401


def test_usage_returns_rollup_and_quota(make_org, login, client):
    from app import usage

    a = make_org()
    rows = [{"prompt_hash": _h(f"p{i}"), "response_hash": _h(f"r{i}"),
             "token_count": 10, "policy_tag": "test"} for i in range(4)]
    assert client.post("/v1/logs/batch", json=rows, headers=a["auth"]).status_code == 202

    db = SessionLocal()
    try:
        usage.rollup_recent(db)
        org = db.get(Organization, uuid.UUID(a["org_id"]))
        org.monthly_log_quota = 10
        db.commit()
    finally:
        db.close()

    ca = login(a["admin_email"], a["admin_password"])
    body = ca.get("/v1/usage").json()
    assert sum(d["logs_count"] for d in body["days"]) == 4
    assert sum(d["tokens_sum"] for d in body["days"]) == 40
    assert body["quota"]["monthly_log_quota"] == 10
    assert body["quota"]["used_this_month"] == 4
    assert body["quota"]["remaining"] == 6


def test_usage_unlimited_quota_reports_null_remaining(make_org, login):
    a = make_org()   # monthly_log_quota defaults to NULL = unlimited
    ca = login(a["admin_email"], a["admin_password"])
    body = ca.get("/v1/usage").json()
    assert body["quota"]["monthly_log_quota"] is None
    assert body["quota"]["remaining"] is None
    assert body["days"] == []


def test_usage_scoped_to_own_org(make_org, login, client):
    from app import usage

    a, b = make_org(), make_org()
    rows = [{"prompt_hash": _h("bp"), "response_hash": _h("br"),
             "token_count": 99, "policy_tag": "test"}]
    assert client.post("/v1/logs/batch", json=rows, headers=b["auth"]).status_code == 202
    db = SessionLocal()
    try:
        usage.rollup_recent(db)
    finally:
        db.close()

    ca = login(a["admin_email"], a["admin_password"])
    body = ca.get("/v1/usage").json()
    assert body["days"] == []                       # org B's rollup is invisible to A
    assert body["quota"]["used_this_month"] == 0


# ─────────────────────── channel separation (no-bypass) ──────────────────────

def test_staff_session_rejected_on_account_routes(make_staff, staff_login):
    s = make_staff(role="superadmin")
    cs = staff_login(s["email"], s["password"])
    assert cs.get("/v1/invoices").status_code == 401
    assert cs.get("/v1/usage").status_code == 401


# ───────────────────────── soft usage quotas (Phase 3) ───────────────────────

def test_quota_for_tiers():
    from app.config import get_settings
    s = get_settings()
    assert s.quota_for("free") == 500
    assert s.quota_for("companion") == 25000
    assert s.quota_for("pro") == 25000
    assert s.quota_for("max") == 250000
    assert s.quota_for("guardian") is None        # 0 = unlimited (lifetime tier)
    assert s.quota_for("nonsense") is None         # unknown = unlimited
    assert s.quota_for(None) is None


def test_usage_over_quota_flag_and_pct(make_org, login, client):
    from app import usage
    a = make_org()
    rows = [{"prompt_hash": _h(f"p{i}"), "response_hash": _h(f"r{i}"),
             "token_count": 10, "policy_tag": "test"} for i in range(4)]
    assert client.post("/v1/logs/batch", json=rows, headers=a["auth"]).status_code == 202
    db = SessionLocal()
    try:
        usage.rollup_recent(db)
        org = db.get(Organization, uuid.UUID(a["org_id"]))
        org.monthly_log_quota = 3
        org.plan_tier = "companion"
        db.commit()
    finally:
        db.close()
    q = login(a["admin_email"], a["admin_password"]).get("/v1/usage").json()["quota"]
    assert q["plan_tier"] == "companion"
    assert q["over_quota"] is True                  # 4 used >= 3 quota
    assert q["usage_pct"] == 133                    # round(4 / 3 * 100)
    assert q["remaining"] == 0


def test_usage_under_quota_not_over(make_org, login, client):
    from app import usage
    a = make_org()
    rows = [{"prompt_hash": _h(f"p{i}"), "response_hash": _h(f"r{i}"),
             "token_count": 10, "policy_tag": "test"} for i in range(4)]
    client.post("/v1/logs/batch", json=rows, headers=a["auth"])
    db = SessionLocal()
    try:
        usage.rollup_recent(db)
        org = db.get(Organization, uuid.UUID(a["org_id"]))
        org.monthly_log_quota = 10
        db.commit()
    finally:
        db.close()
    q = login(a["admin_email"], a["admin_password"]).get("/v1/usage").json()["quota"]
    assert q["over_quota"] is False
    assert q["usage_pct"] == 40


def test_ingest_blocks_when_credits_are_exhausted(make_org, client):
    """The ledger, not a delayed rollup, enforces the plan credit limit."""
    a = make_org()
    db = SessionLocal()
    try:
        org = db.get(Organization, uuid.UUID(a["org_id"]))
        org.monthly_log_quota = 1
        db.commit()
    finally:
        db.close()
    rows = [{"prompt_hash": _h(f"p{i}"), "response_hash": _h(f"r{i}"),
             "token_count": 10, "policy_tag": "test"} for i in range(2)]
    r = client.post("/v1/logs/batch", json=rows, headers=a["auth"])
    assert r.status_code == 402
    assert r.json()["detail"]["code"] == "credits_exhausted"


def test_signup_assigns_free_quota(client, monkeypatch):
    """Self-serve signup provisions the org with the free-tier monthly quota."""
    from app.routers import billing as billing_mod
    monkeypatch.setattr(billing_mod.password_reset, "issue_reset", lambda *a, **k: None)
    r = client.post("/v1/signup", json={"email": "quota-user@test.dev"})
    assert r.status_code == 200, r.text
    key = r.json()["api_key"]
    q = client.get("/v1/usage", headers={"Authorization": f"Bearer {key}"}).json()["quota"]
    assert q["plan_tier"] == "free"
    assert q["monthly_log_quota"] == 500
    assert q["trial_active"] is True
    assert q["trial_ends_at"]
