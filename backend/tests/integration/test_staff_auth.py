"""The strict no-bypass proof for the 3-surface split (Phase 4 #1).

Verifies that the customer channel and the platform-staff channel are provably
disjoint, the platform-role ladder is enforced, staff read cross-org while
customers stay isolated, every mutating staff action is audited, and the hardened
policy write rejects non-admins.
"""

from __future__ import annotations

import hashlib

from app.db import SessionLocal
from app.models import AdminAction


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _count_actions(action: str | None = None) -> int:
    db = SessionLocal()
    try:
        rows = db.query(AdminAction).all()
        if action is None:
            return len(rows)
        return sum(1 for r in rows if r.action == action)
    finally:
        db.close()


# ───────────────────────── channel separation (the core) ─────────────────────

def test_unauthenticated_cannot_reach_admin(client):
    assert client.get("/admin/v1/organizations").status_code == 401
    assert client.get("/admin/v1/stats").status_code == 401
    assert client.get("/admin/v1/traffic").status_code == 401
    assert client.get("/admin/v1/staff").status_code == 401


def test_customer_session_cannot_reach_admin(make_org, login):
    """A logged-in CUSTOMER admin (its `session` cookie IS sent to /admin) still
    gets 401 — the admin app reads only the staff cookie, never the customer one."""
    a = make_org()
    ca = login(a["admin_email"], a["admin_password"])
    assert ca.get("/admin/v1/organizations").status_code == 401
    assert ca.get("/admin/v1/stats").status_code == 401
    assert ca.post(f"/admin/v1/organizations/{a['org_id']}/suspend", json={}).status_code == 401


def test_staff_session_cannot_reach_customer_routes(make_staff, staff_login):
    """A logged-in STAFF user cannot act on customer-session routes."""
    s = make_staff(role="superadmin")
    cs = staff_login(s["email"], s["password"])
    assert cs.get("/v1/auth/me").status_code == 401       # no customer session
    assert cs.get("/v1/keys").status_code == 401          # admin-of-org route
    assert cs.get("/v1/logs").status_code == 401          # resolve_org route


def test_staff_login_rejects_customer_credentials(make_org, client):
    """A customer user's email/password must NOT mint a staff session."""
    a = make_org()
    r = client.post("/admin/v1/auth/login",
                    json={"email": a["admin_email"], "password": a["admin_password"]})
    assert r.status_code == 401


# ───────────────────────── cross-org visibility vs isolation ─────────────────

def test_staff_sees_all_orgs_customer_sees_one(make_org, make_staff, staff_login,
                                               login, revealed_org_id):
    a = make_org()
    b = make_org()
    s = make_staff(role="viewer")
    cs = staff_login(s["email"], s["password"])

    orgs = cs.get("/admin/v1/organizations").json()
    ids = {o["id"] for o in orgs}
    assert a["org_id"] in ids and b["org_id"] in ids and len(orgs) >= 2

    # The customer meanwhile still sees exactly its own org via the dashboard —
    # and only after confirming identity, since §7.1 moved the id behind step-up.
    ca = login(a["admin_email"], a["admin_password"])
    assert revealed_org_id(ca) == a["org_id"]


def test_staff_org_detail_counts(make_org, make_staff, staff_login, client):
    a = make_org()
    rows = [{"prompt_hash": _h(f"p{i}"), "response_hash": _h(f"r{i}"),
             "token_count": 10, "policy_tag": "test"} for i in range(3)]
    assert client.post("/v1/logs/batch", json=rows, headers=a["auth"]).status_code == 202

    s = make_staff(role="viewer")
    cs = staff_login(s["email"], s["password"])
    detail = cs.get(f"/admin/v1/organizations/{a['org_id']}").json()
    assert detail["total_logs"] == 3
    assert detail["user_count"] == 1


# ───────────────────────────── platform-role ladder ──────────────────────────

def test_viewer_cannot_suspend_operator_can(make_org, make_staff, staff_login):
    a = make_org()
    viewer = make_staff(role="viewer")
    operator = make_staff(role="operator")

    cv = staff_login(viewer["email"], viewer["password"])
    assert cv.get("/admin/v1/organizations").status_code == 200            # viewer can read
    assert cv.post(f"/admin/v1/organizations/{a['org_id']}/suspend", json={}).status_code == 403

    co = staff_login(operator["email"], operator["password"])
    r = co.post(f"/admin/v1/organizations/{a['org_id']}/suspend",
                json={"reason": "spam"})
    assert r.status_code == 200 and r.json()["status"] == "suspended"


def test_only_superadmin_manages_staff(make_staff, staff_login):
    operator = make_staff(role="operator")
    superadmin = make_staff(role="superadmin")

    co = staff_login(operator["email"], operator["password"])
    assert co.get("/admin/v1/staff").status_code == 403
    assert co.post("/admin/v1/staff", json={"email": "x@foxy.audit"}).status_code == 403

    cs = staff_login(superadmin["email"], superadmin["password"])
    assert cs.get("/admin/v1/staff").status_code == 200
    created = cs.post("/admin/v1/staff",
                      json={"email": "new@foxy.audit", "platform_role": "viewer",
                            "password": "newstaffpass123"})
    assert created.status_code == 200
    body = created.json()
    assert body["platform_role"] == "viewer" and body["temp_password"]


def test_created_staff_can_log_in(make_staff, staff_login, client):
    superadmin = make_staff(role="superadmin")
    cs = staff_login(superadmin["email"], superadmin["password"])
    body = cs.post("/admin/v1/staff",
                   json={"email": "fresh@foxy.audit", "platform_role": "operator",
                         "password": "freshpass1234"}).json()
    r = client.post("/admin/v1/auth/login",
                    json={"email": "fresh@foxy.audit", "password": body["temp_password"]})
    assert r.status_code == 200 and r.json()["platform_role"] == "operator"


def test_superadmin_cannot_disable_self(make_staff, staff_login):
    s = make_staff(role="superadmin")
    cs = staff_login(s["email"], s["password"])
    assert cs.post(f"/admin/v1/staff/{s['id']}/disable").status_code == 400


def test_disabled_staff_cannot_log_in(make_staff, staff_login, client):
    superadmin = make_staff(role="superadmin")
    victim = make_staff(role="viewer")
    cs = staff_login(superadmin["email"], superadmin["password"])
    assert cs.post(f"/admin/v1/staff/{victim['id']}/disable").status_code == 200
    r = client.post("/admin/v1/auth/login",
                    json={"email": victim["email"], "password": victim["password"]})
    assert r.status_code == 403


# ─────────────────────────── admin-action audit trail ────────────────────────

def test_mutations_write_exactly_one_admin_action(make_org, make_staff, staff_login):
    a = make_org()
    operator = make_staff(role="operator")
    co = staff_login(operator["email"], operator["password"])

    assert _count_actions() == 2              # staff.login + staff.step_up (auto-granted by the fixture)
    assert _count_actions("staff.login") == 1
    assert _count_actions("staff.step_up") == 1
    co.post(f"/admin/v1/organizations/{a['org_id']}/suspend", json={"reason": "abuse"})
    assert _count_actions("org.suspend") == 1
    co.post(f"/admin/v1/organizations/{a['org_id']}/enable")
    assert _count_actions("org.enable") == 1
    assert _count_actions() == 4   # login + step_up + org.suspend + org.enable (reads write nothing)


def test_staff_create_is_audited(make_staff, staff_login):
    superadmin = make_staff(role="superadmin")
    cs = staff_login(superadmin["email"], superadmin["password"])
    cs.post("/admin/v1/staff", json={"email": "audit-me@foxy.audit"})
    assert _count_actions("staff.create") == 1


# ─────────────────── hardened policy write (privilege escalation) ─────────────

def test_policy_write_requires_admin(make_org, add_user, login):
    a = make_org()
    add_user(a["org_id"], "member@test.dev", "memberpass1", role="member")

    cm = login("member@test.dev", "memberpass1")
    body = {"pii_detection": True, "prompt_injection": True,
            "regulated_data_mode": True, "max_token_threshold": 1000}
    assert cm.put("/v1/policies", json=body).status_code == 403       # member blocked
    assert cm.get("/v1/policies").status_code == 200                  # but can still read

    ca = login(a["admin_email"], a["admin_password"])
    assert ca.put("/v1/policies", json=body).status_code == 200       # admin allowed


def test_policy_write_rejects_bare_bearer(make_org, client):
    a = make_org()
    body = {"pii_detection": False, "prompt_injection": True,
            "regulated_data_mode": False, "max_token_threshold": 50000}
    # A machine Bearer key must not rewrite compliance policy (was possible before).
    assert client.put("/v1/policies", json=body, headers=a["auth"]).status_code in (401, 403)
    # ... but the SDK key can still READ policy (resolve_org).
    assert client.get("/v1/policies", headers=a["auth"]).status_code == 200
