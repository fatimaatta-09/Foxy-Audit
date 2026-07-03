"""In/out tracking, marketing leads, Stripe idempotency, and usage rollups (Phase 4 #1)."""

from __future__ import annotations

import hashlib

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import SessionLocal
from app.models import Invoice, MarketingLead, StripeEvent, TrafficEvent, UsageDaily


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


# ─────────────────────────── marketing leads + beacon ────────────────────────

def test_lead_capture_and_dedup(client):
    r1 = client.post("/v1/leads", json={"email": "Buyer@Corp.com", "company": "Corp"})
    assert r1.status_code == 200 and r1.json()["status"] == "created"
    # same email (case-insensitive) updates the active lead rather than duplicating
    r2 = client.post("/v1/leads", json={"email": "buyer@corp.com", "name": "Bee"})
    assert r2.json()["status"] == "updated"

    db = SessionLocal()
    try:
        rows = db.query(MarketingLead).all()
        assert len(rows) == 1
        assert rows[0].name == "Bee" and rows[0].company == "Corp"
    finally:
        db.close()


def test_marketing_beacon_writes_traffic(client):
    assert client.post("/v1/track", json={"path": "/pricing?ref=x"}).status_code == 200
    db = SessionLocal()
    try:
        rows = db.query(TrafficEvent).filter(TrafficEvent.site == "marketing").all()
        assert len(rows) == 1
        assert rows[0].path == "/pricing"        # query string stripped
        assert rows[0].org_id is None            # anonymous
    finally:
        db.close()


# ──────────────────────── traffic middleware (full capture) ──────────────────

def test_traffic_middleware_captures_authenticated_request(make_org, login):
    from app.config import get_settings
    from app.middleware import traffic as traffic_mw

    s = get_settings()
    a = make_org()
    ca = login(a["admin_email"], a["admin_password"])   # login itself not captured (disabled)

    s.traffic_tracking_enabled = True
    try:
        assert ca.get("/v1/stats").status_code == 200
        traffic_mw.flush(timeout=3.0)
    finally:
        s.traffic_tracking_enabled = False

    db = SessionLocal()
    try:
        rows = db.query(TrafficEvent).filter(TrafficEvent.site == "app").all()
        stats_rows = [r for r in rows if r.path == "/v1/stats"]
        assert stats_rows, "expected a traffic row for /v1/stats"
        row = stats_rows[0]
        assert str(row.org_id) == a["org_id"]            # attributed to the session's org
        assert row.method == "GET" and row.status_code == 200
        # privacy: IP/UA are hashed (64 hex) or absent — never raw
        assert row.ip_hash is None or len(row.ip_hash) == 64
    finally:
        db.close()


# ─────────────────────────── Stripe idempotency + invoices ───────────────────

def test_stripe_event_insert_is_idempotent():
    db = SessionLocal()
    try:
        def _insert():
            return db.execute(
                pg_insert(StripeEvent)
                .values(stripe_event_id="evt_dupe", type="x", payload={}, status="received")
                .on_conflict_do_nothing(index_elements=["stripe_event_id"])
                .returning(StripeEvent.id)
            ).scalar_one_or_none()
        first = _insert(); db.commit()
        second = _insert(); db.commit()
        assert first is not None
        assert second is None                    # replay is a no-op
        assert db.query(StripeEvent).count() == 1
    finally:
        db.close()


def test_checkout_provisions_org_and_converts_lead(client):
    client.post("/v1/leads", json={"email": "founder@startup.io", "company": "Startup"})
    from app.routers.billing import _handle_checkout

    db = SessionLocal()
    try:
        result, org_id = _handle_checkout(
            db, {"customer": "cus_A", "customer_email": "founder@startup.io",
                 "subscription": "sub_A"})
        db.commit()
        assert result["status"] == "provisioned" and org_id
        lead = db.query(MarketingLead).filter(
            MarketingLead.email == "founder@startup.io").one()
        assert lead.status == "converted"
        assert str(lead.converted_org_id) == org_id
    finally:
        db.close()


def test_invoice_upsert_is_idempotent(client):
    from app.routers.billing import _handle_checkout, _handle_invoice

    db = SessionLocal()
    try:
        _, org_id = _handle_checkout(
            db, {"customer": "cus_B", "customer_email": "b@corp.com", "subscription": "sub_B"})
        db.commit()
        inv = {"id": "in_1", "customer": "cus_B", "amount_paid": 4999,
               "currency": "usd", "status": "paid",
               "period_start": 1700000000, "period_end": 1702600000}
        _handle_invoice(db, inv); db.commit()
        _handle_invoice(db, {**inv, "status": "open"}); db.commit()   # same id → update

        rows = db.query(Invoice).filter(Invoice.stripe_invoice_id == "in_1").all()
        assert len(rows) == 1
        assert rows[0].status == "open" and rows[0].amount_cents == 4999
        assert str(rows[0].org_id) == org_id
    finally:
        db.close()


# ──────────────────────────────── usage rollups ──────────────────────────────

def test_usage_rollup_aggregates_ledger(make_org, client):
    from app import usage

    a = make_org()
    rows = [{"prompt_hash": _h(f"p{i}"), "response_hash": _h(f"r{i}"),
             "token_count": 10, "policy_tag": "test"} for i in range(4)]
    assert client.post("/v1/logs/batch", json=rows, headers=a["auth"]).status_code == 202

    db = SessionLocal()
    try:
        usage.rollup_recent(db)
        totals = db.query(UsageDaily).filter(
            UsageDaily.org_id == a["org_id"]).all()
        assert totals, "expected a usage_daily row"
        assert sum(t.logs_count for t in totals) == 4
        assert sum(t.tokens_sum for t in totals) == 40
        # nothing graded yet (no worker running in tests) → all pending
        assert sum(t.pending_count for t in totals) == 4
        assert sum(t.graded_count for t in totals) == 0
    finally:
        db.close()
