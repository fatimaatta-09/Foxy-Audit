"""Integration tests for Phase 2 — org lifecycle + staff management (audited writes).

Covers, mirroring conftest fixtures + the test_admin_data assertion style:
  * role gating (operator vs superadmin get the right 200/403),
  * self-lockout / last-superadmin guards,
  * an admin_actions row per mutation, the effect is applied,
  * no secret is ever serialized, and staff login/logout are audited.
"""
from __future__ import annotations

import uuid

from app.db import SessionLocal
from app.models import AdminAction, ApiKey, Organization, StaffUser


def _org(org_id):
    db = SessionLocal()
    try:
        return db.get(Organization, uuid.UUID(org_id))
    finally:
        db.close()


def _staff(staff_id):
    db = SessionLocal()
    try:
        return db.get(StaffUser, uuid.UUID(staff_id))
    finally:
        db.close()


def _first_key(org_id):
    db = SessionLocal()
    try:
        return db.execute(
            ApiKey.__table__.select().where(ApiKey.org_id == uuid.UUID(org_id))
        ).first()
    finally:
        db.close()


def _actions(**where):
    db = SessionLocal()
    try:
        q = db.query(AdminAction)
        for k, v in where.items():
            q = q.filter(getattr(AdminAction, k) == v)
        return q.all()
    finally:
        db.close()


# ------------------------------- Org lifecycle -------------------------------

def test_key_revoke_gating_effect_audit(make_staff, staff_login, make_org, client):
    org = make_org()
    key_id = str(_first_key(org["org_id"]).id)
    base = f"/admin/v1/organizations/{org['org_id']}/keys/{key_id}/revoke"
    # unauth + viewer are refused
    assert client.post(base).status_code == 401
    cv = staff_login(*(lambda s: (s["email"], s["password"]))(make_staff(role="viewer")))
    assert cv.post(base).status_code == 403
    # operator revokes
    op = make_staff(role="operator")
    co = staff_login(op["email"], op["password"])
    r = co.post(base)
    assert r.status_code == 200 and r.json()["status"] == "revoked"
    db = SessionLocal()
    try:
        k = db.get(ApiKey, uuid.UUID(key_id))
        assert k.status == "revoked" and k.revoked_at is not None
    finally:
        db.close()
    assert any(a.action == "org.key.revoke" and a.target_id == key_id
               for a in _actions(target_type="api_key"))
    # idempotent
    assert co.post(base).json()["status"] == "already_revoked"
    # wrong org / bad key -> 404
    other = make_org()
    assert co.post(f"/admin/v1/organizations/{other['org_id']}/keys/{key_id}/revoke").status_code == 404


def test_ip_allowlist_get_put(make_staff, staff_login, make_org):
    org = make_org()
    sup = make_staff()
    cs = staff_login(sup["email"], sup["password"])
    assert cs.get(f"/admin/v1/organizations/{org['org_id']}/ip-allowlist").json()["entries"] == []
    # viewer cannot write
    cv = staff_login(*(lambda s: (s["email"], s["password"]))(make_staff(role="viewer")))
    assert cv.put(f"/admin/v1/organizations/{org['org_id']}/ip-allowlist",
                  json={"entries": ["10.0.0.0/8"]}).status_code == 403
    # operator sets a valid list
    op = make_staff(role="operator")
    co = staff_login(op["email"], op["password"])
    r = co.put(f"/admin/v1/organizations/{org['org_id']}/ip-allowlist",
               json={"entries": ["10.0.0.0/8", "203.0.113.5"]})
    assert r.status_code == 200 and r.json()["entries"] == ["10.0.0.0/8", "203.0.113.5"]
    assert _org(org["org_id"]).ip_allowlist == "10.0.0.0/8,203.0.113.5"
    assert any(a.action == "org.ip_allowlist.set" for a in _actions(target_type="organization"))
    # invalid entry -> 422, unchanged
    assert co.put(f"/admin/v1/organizations/{org['org_id']}/ip-allowlist",
                  json={"entries": ["not-an-ip"]}).status_code == 422
    # clearing works
    assert co.put(f"/admin/v1/organizations/{org['org_id']}/ip-allowlist",
                  json={"entries": []}).json()["entries"] == []
    assert _org(org["org_id"]).ip_allowlist is None


def test_trial_extend(make_staff, staff_login, make_org):
    org = make_org()
    op = make_staff(role="operator")
    co = staff_login(op["email"], op["password"])
    assert co.post(f"/admin/v1/organizations/{org['org_id']}/trial", json={"days": 0}).status_code == 422
    r = co.post(f"/admin/v1/organizations/{org['org_id']}/trial", json={"days": 14})
    assert r.status_code == 200
    assert _org(org["org_id"]).trial_ends_at is not None
    assert any(a.action == "org.trial.extend" for a in _actions(target_type="organization"))


def test_offboard_superadmin_only(make_staff, staff_login, make_org):
    org = make_org()
    # operator is refused (destructive → superadmin)
    op = make_staff(role="operator")
    co = staff_login(op["email"], op["password"])
    assert co.post(f"/admin/v1/organizations/{org['org_id']}/offboard").status_code == 403
    # superadmin offboards
    sup = make_staff()
    cs = staff_login(sup["email"], sup["password"])
    r = cs.post(f"/admin/v1/organizations/{org['org_id']}/offboard")
    assert r.status_code == 200 and r.json()["keys_revoked"] >= 1
    o = _org(org["org_id"])
    assert o.deleted_at is not None and o.suspended is True
    db = SessionLocal()
    try:
        actives = db.execute(
            ApiKey.__table__.select().where(ApiKey.org_id == uuid.UUID(org["org_id"]),
                                            ApiKey.status == "active")
        ).all()
        assert actives == []
    finally:
        db.close()
    assert any(a.action == "org.offboard" for a in _actions(target_type="organization"))
    # idempotent
    assert cs.post(f"/admin/v1/organizations/{org['org_id']}/offboard").json()["status"] == "already_offboarded"


# ------------------------------- Staff management ----------------------------

def test_staff_role_change_guards(make_staff, staff_login, client):
    sup = make_staff(role="superadmin")
    cs = staff_login(sup["email"], sup["password"])
    target = make_staff(role="viewer")
    base = f"/admin/v1/staff/{target['id']}/role"
    # operator cannot manage staff
    op = make_staff(role="operator")
    co = staff_login(op["email"], op["password"])
    assert co.post(base, json={"platform_role": "operator"}).status_code == 403
    # bad role -> 422
    assert cs.post(base, json={"platform_role": "root"}).status_code == 422
    # self role change -> 400
    assert cs.post(f"/admin/v1/staff/{sup['id']}/role", json={"platform_role": "viewer"}).status_code == 400
    # promote target
    r = cs.post(base, json={"platform_role": "operator"})
    assert r.status_code == 200
    assert _staff(target["id"]).platform_role == "operator"
    assert any(a.action == "staff.role" and a.target_id == target["id"]
               for a in _actions(target_type="staff_user"))


def test_staff_enable_and_mfa_reset(make_staff, staff_login):
    sup = make_staff()
    cs = staff_login(sup["email"], sup["password"])
    target = make_staff(role="operator")
    # disable then re-enable
    assert cs.post(f"/admin/v1/staff/{target['id']}/disable").status_code == 200
    assert _staff(target["id"]).disabled is True
    assert cs.post(f"/admin/v1/staff/{target['id']}/enable").status_code == 200
    assert _staff(target["id"]).disabled is False
    # mfa reset clears enrolment
    db = SessionLocal()
    try:
        t = db.get(StaffUser, uuid.UUID(target["id"]))
        t.mfa_enabled = True
        t.mfa_code_hash = "x" * 64
        db.commit()
    finally:
        db.close()
    assert cs.post(f"/admin/v1/staff/{target['id']}/mfa/reset").status_code == 200
    t2 = _staff(target["id"])
    assert t2.mfa_enabled is False and t2.mfa_code_hash is None
    for act in ("staff.enable", "staff.mfa_reset"):
        assert any(a.action == act for a in _actions(target_type="staff_user"))


def test_staff_list_last_login_and_activity(make_staff, staff_login):
    sup = make_staff()
    cs = staff_login(sup["email"], sup["password"])   # this login is audited
    # list shows this superadmin with a last_login + no secret
    r = cs.get("/admin/v1/staff")
    assert r.status_code == 200
    assert "password_hash" not in r.text and "mfa_code_hash" not in r.text
    me = next(x for x in r.json() if x["id"] == sup["id"])
    assert me["last_login"] is not None
    assert "mfa_enabled" in me and "created_at" in me
    # a second staff logs in, then superadmin changes their role -> both show in activity
    target = make_staff(role="viewer")
    staff_login(target["email"], target["password"])
    cs.post(f"/admin/v1/staff/{target['id']}/role", json={"platform_role": "operator"})
    acts = cs.get(f"/admin/v1/staff/{target['id']}/activity").json()["items"]
    kinds = {a["action"] for a in acts}
    assert "staff.login" in kinds          # their own login (by_self)
    assert "staff.role" in kinds           # done to them by the superadmin
    assert any(a["action"] == "staff.login" and a["by_self"] for a in acts)
    assert any(a["action"] == "staff.role" and not a["by_self"] for a in acts)


def test_staff_login_logout_audited(make_staff, staff_login):
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    assert any(a.action == "staff.login" and a.target_id == s["id"]
               for a in _actions(staff_user_id=uuid.UUID(s["id"])))
    assert c.post("/admin/v1/auth/logout").status_code == 200
    assert any(a.action == "staff.logout" for a in _actions(staff_user_id=uuid.UUID(s["id"])))
