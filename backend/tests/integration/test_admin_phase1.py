"""Integration tests for the Phase 1 admin deep-dive endpoints.

Covers Org 360 (overview/users/keys/logs/verify/breaches), the Overview/Ops trend
endpoints (stats timeseries / plan-mix / top-orgs, health trends / alert-history),
and the inbox action audit. Mirrors the fixtures + assertion patterns in
conftest.py / test_admin_data.py / test_admin_inbox.py:
  * role gating (401 unauth, 200 viewer, customer session rejected),
  * per-org isolation (a scoped read never leaks another tenant),
  * no secret ever serialized (password_hash / key_hash),
  * correct aggregation, and an admin_actions row on each audited mutation.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.db import SessionLocal
from app.models import (
    AdminAction, AuditLog, ChainAnchor, Invoice, MarketingLead, Organization,
    UsageDaily,
)


def _today():
    return datetime.now(timezone.utc).date()


def _seed_usage(org_id, day, logs=0, breaches=0, tokens=0, graded=0, failed=0, pending=0):
    db = SessionLocal()
    try:
        db.add(UsageDaily(org_id=uuid.UUID(org_id), day=day, logs_count=logs,
                          breach_count=breaches, tokens_sum=tokens, graded_count=graded,
                          failed_count=failed, pending_count=pending))
        db.commit()
    finally:
        db.close()


def _seed_audit(org_id, seq, breach=False, agent="agent-x", policy_tag="default"):
    db = SessionLocal()
    try:
        h = uuid.uuid4().hex + uuid.uuid4().hex  # 64 hex chars
        db.add(AuditLog(org_id=uuid.UUID(org_id), seq=seq, prompt_hash=h, response_hash=h,
                        token_count=10, policy_tag=policy_tag, prev_hash=h, chain_hash=h,
                        agent=agent, grading_status="graded",
                        gemini_verdict={"policy_breach": bool(breach)}))
        db.commit()
    finally:
        db.close()


def _seed_invoice(org_id, cents, status="paid", created=None):
    db = SessionLocal()
    try:
        inv = Invoice(org_id=uuid.UUID(org_id), stripe_invoice_id="in_" + uuid.uuid4().hex,
                      amount_cents=cents, status=status)
        if created is not None:
            inv.created_at = created
        db.add(inv)
        db.commit()
    finally:
        db.close()


def _seed_anchor(org_id, status="confirmed", anchored=None, confirmed=None):
    db = SessionLocal()
    try:
        a = ChainAnchor(org_id=uuid.UUID(org_id), root_hash=uuid.uuid4().hex, last_seq=1,
                        chain="stub", status=status)
        if anchored is not None:
            a.anchored_at = anchored
        if confirmed is not None:
            a.confirmed_at = confirmed
        db.add(a)
        db.commit()
    finally:
        db.close()


def _set_plan(org_id, plan):
    db = SessionLocal()
    try:
        o = db.get(Organization, uuid.UUID(org_id))
        o.plan_tier = plan
        db.commit()
    finally:
        db.close()


def _seed_admin_action(staff_id, action, target_id=None):
    db = SessionLocal()
    try:
        db.add(AdminAction(staff_user_id=uuid.UUID(staff_id), action=action,
                           target_type="alert", target_id=target_id))
        db.commit()
    finally:
        db.close()


def _seed_lead(email, message):
    db = SessionLocal()
    try:
        lead = MarketingLead(email=email, message=message, source="contact")
        db.add(lead)
        db.commit()
        db.refresh(lead)
        return str(lead.id)
    finally:
        db.close()


# ------------------------------- Org 360 -------------------------------------

def test_org360_gating_and_no_secrets(make_staff, staff_login, make_org, login, client):
    org = make_org()
    eps = ("overview", "users", "keys", "logs", "verify", "breaches")
    # unauthenticated -> 401
    for ep in eps:
        assert client.get(f"/admin/v1/organizations/{org['org_id']}/{ep}").status_code == 401
    # a CUSTOMER session must not reach the admin API
    cust = login(org["admin_email"], org["admin_password"])
    assert cust.get(f"/admin/v1/organizations/{org['org_id']}/overview").status_code == 401
    # viewer staff -> 200 on every read, and never leaks a secret
    v = make_staff(role="viewer")
    cv = staff_login(v["email"], v["password"])
    for ep in eps:
        r = cv.get(f"/admin/v1/organizations/{org['org_id']}/{ep}")
        assert r.status_code == 200, (ep, r.status_code, r.text)
        assert "password_hash" not in r.text
        assert "key_hash" not in r.text


def test_org360_overview_counts_and_usage(make_staff, staff_login, make_org, add_user):
    org = make_org()
    add_user(org["org_id"], "u2@test.dev", "pw12345678")
    today = _today()
    _seed_usage(org["org_id"], today, logs=100, breaches=3, tokens=5000)
    _seed_usage(org["org_id"], today - timedelta(days=1), logs=50, breaches=1)
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    d = c.get(f"/admin/v1/organizations/{org['org_id']}/overview").json()
    assert d["user_count"] == 2                 # seeded admin + u2
    assert d["api_key_count"] == 1
    assert d["active_key_count"] == 1
    by_day = {p["day"]: p for p in d["usage"]}
    assert by_day[today.isoformat()]["logs"] == 100
    assert by_day[today.isoformat()]["breaches"] == 3
    assert d["policy"] is None                  # honest: no policy row seeded


def test_org360_users_keys_isolation(make_staff, staff_login, make_org, add_user):
    a = make_org()
    b = make_org()
    add_user(a["org_id"], "alice@a.dev", "pw12345678")
    add_user(b["org_id"], "bob@b.dev", "pw12345678")
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    ua = c.get(f"/admin/v1/organizations/{a['org_id']}/users").json()
    emails = {u["email"] for u in ua}
    assert "alice@a.dev" in emails
    assert "bob@b.dev" not in emails            # per-org isolation
    assert all("password_hash" not in u for u in ua)
    ka = c.get(f"/admin/v1/organizations/{a['org_id']}/keys").json()
    assert ka and all("key_hash" not in k for k in ka)
    assert all(k["key_prefix"] for k in ka)


def test_org360_logs_and_breaches_isolation(make_staff, staff_login, make_org):
    a = make_org()
    b = make_org()
    _seed_audit(a["org_id"], 1, breach=False)
    _seed_audit(a["org_id"], 2, breach=True)
    _seed_audit(b["org_id"], 1, breach=True)    # other tenant's breach
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    la = c.get(f"/admin/v1/organizations/{a['org_id']}/logs").json()
    assert la["total"] == 2
    ba = c.get(f"/admin/v1/organizations/{a['org_id']}/breaches").json()
    assert ba["total"] == 1
    assert {it["seq"] for it in ba["items"]} == {2}   # never b's row
    lb = c.get(f"/admin/v1/organizations/{a['org_id']}/logs?breaches_only=true").json()
    assert lb["total"] == 1


def test_org360_verify_empty_ok(make_staff, staff_login, make_org):
    org = make_org()
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    d = c.get(f"/admin/v1/organizations/{org['org_id']}/verify").json()
    assert d["ok"] is True
    assert d["count"] == 0
    assert d["last_seq"] == 0


def test_org360_bad_id(make_staff, staff_login):
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    assert c.get("/admin/v1/organizations/not-a-uuid/overview").status_code == 422
    assert c.get(f"/admin/v1/organizations/{uuid.uuid4()}/overview").status_code == 404


# ------------------------------- Stats trends --------------------------------

def test_stats_timeseries_interactions_and_delta(make_staff, staff_login, make_org, client):
    org = make_org()
    today = _today()
    _seed_usage(org["org_id"], today, logs=100, breaches=5)
    _seed_usage(org["org_id"], today - timedelta(days=1), logs=40, breaches=2)
    _seed_usage(org["org_id"], today - timedelta(days=40), logs=999)   # outside a 30d window
    assert client.get("/admin/v1/stats/timeseries?metric=interactions").status_code == 401
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    d = c.get("/admin/v1/stats/timeseries?metric=interactions&days=30").json()
    assert d["metric"] == "interactions"
    assert len(d["points"]) == 30
    assert d["total"] == 140                       # 100 + 40 (day -40 excluded)
    db_ = c.get("/admin/v1/stats/timeseries?metric=breaches&days=30").json()
    assert db_["total"] == 7
    assert c.get("/admin/v1/stats/timeseries?metric=nope").status_code == 422


def test_stats_timeseries_signups_and_revenue(make_staff, staff_login, make_org):
    org = make_org()
    _seed_invoice(org["org_id"], 5000, status="paid")
    _seed_invoice(org["org_id"], 2000, status="open")    # unpaid -> excluded
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    su = c.get("/admin/v1/stats/timeseries?metric=signups&days=7").json()
    assert su["total"] >= 1
    rev = c.get("/admin/v1/stats/timeseries?metric=revenue&days=7").json()
    assert rev["unit"] == "cents"
    assert rev["total"] == 5000


def test_stats_plan_mix(make_staff, staff_login, make_org):
    a = make_org()
    b = make_org()
    cc = make_org()
    _set_plan(a["org_id"], "pro")
    _set_plan(b["org_id"], "pro")
    _set_plan(cc["org_id"], "free")
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    d = c.get("/admin/v1/stats/plan-mix").json()
    m = {it["plan"]: it["count"] for it in d["items"]}
    assert m.get("pro") == 2
    assert m.get("free") == 1
    assert d["total"] == 3


def test_stats_top_orgs(make_staff, staff_login, make_org):
    a = make_org()
    b = make_org()
    today = _today()
    _seed_usage(a["org_id"], today, logs=500)
    _seed_usage(b["org_id"], today, logs=100)
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    d = c.get("/admin/v1/stats/top-orgs?days=30&limit=5").json()
    assert d["items"][0]["org_id"] == a["org_id"]
    assert d["items"][0]["logs"] == 500
    assert d["items"][0]["name"] == a["name"]


# ------------------------------- Health trends -------------------------------

def test_health_trends(make_staff, staff_login, make_org, client):
    org = make_org()
    today = _today()
    _seed_usage(org["org_id"], today, graded=10, failed=2, pending=3)
    now = datetime.now(timezone.utc)
    _seed_anchor(org["org_id"], status="confirmed", anchored=now, confirmed=now)
    assert client.get("/admin/v1/health/trends").status_code == 401
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    d = c.get("/admin/v1/health/trends?days=14").json()
    assert len(d["grading"]) == 14
    assert len(d["anchors"]) == 14
    tg = {p["day"]: p for p in d["grading"]}[today.isoformat()]
    assert (tg["graded"], tg["failed"], tg["pending"]) == (10, 2, 3)
    ta = {p["day"]: p for p in d["anchors"]}[today.isoformat()]
    assert (ta["anchored"], ta["confirmed"]) == (1, 1)


def test_alert_history(make_staff, staff_login):
    s = make_staff()
    _seed_admin_action(s["id"], "alert.ack", target_id="alert-1")
    _seed_admin_action(s["id"], "org.suspend", target_id="ignore-me")   # different action
    c = staff_login(s["email"], s["password"])
    d = c.get("/admin/v1/health/alert-history").json()
    tids = {it["target_id"] for it in d["items"]}
    assert "alert-1" in tids
    assert "ignore-me" not in tids                 # only alert.ack surfaces
    row = next(it for it in d["items"] if it["target_id"] == "alert-1")
    assert row["actor"] == s["email"]


# ------------------------------- Inbox audit ---------------------------------

def test_inbox_claim_reply_are_audited(make_staff, staff_login):
    lead_id = _seed_lead("cust@x.com", "please help with compliance")
    s = make_staff(role="operator")
    c = staff_login(s["email"], s["password"])
    assert c.post(f"/admin/v1/inbox/{lead_id}/claim").status_code == 200
    assert c.post(f"/admin/v1/inbox/{lead_id}/reply", json={"message": "on it"}).status_code == 200
    db = SessionLocal()
    try:
        actions = {a.action for a in db.query(AdminAction)
                   .filter(AdminAction.target_id == lead_id).all()}
    finally:
        db.close()
    assert "inbox.claim" in actions
    assert "inbox.reply" in actions
