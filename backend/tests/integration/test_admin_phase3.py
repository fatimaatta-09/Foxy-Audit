"""Integration tests for Phase 3 — real Settings: platform config/flags, broadcast,
and staff self-service. Verifies the config actually TAKES EFFECT (default quota +
anchor-stale alert threshold), not just that it persists.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import app.mfa as mfa_mod
from app.db import SessionLocal
from app.models import AdminAction, ChainAnchor, Organization, StaffUser


def _actions(action=None):
    db = SessionLocal()
    try:
        q = db.query(AdminAction)
        if action:
            q = q.filter(AdminAction.action == action)
        return q.all()
    finally:
        db.close()


def _org(oid):
    db = SessionLocal()
    try:
        return db.get(Organization, uuid.UUID(oid))
    finally:
        db.close()


def _seed_confirmed_anchor(org_id, ago_seconds):
    db = SessionLocal()
    try:
        t = datetime.now(timezone.utc) - timedelta(seconds=ago_seconds)
        db.add(ChainAnchor(org_id=uuid.UUID(org_id), root_hash=uuid.uuid4().hex, last_seq=1,
                           chain="stub", status="confirmed", anchored_at=t, confirmed_at=t))
        db.commit()
    finally:
        db.close()


# ------------------------------- config --------------------------------------

def test_config_gating_get_put_audit(make_staff, staff_login, client):
    assert client.get("/admin/v1/config").status_code == 401
    op = make_staff(role="operator")
    co = staff_login(op["email"], op["password"])
    assert co.get("/admin/v1/config").status_code == 403        # superadmin only
    sup = make_staff()
    cs = staff_login(sup["email"], sup["password"])
    d = cs.get("/admin/v1/config").json()["config"]
    assert d["monthly_quota_pro"]["overridden"] is False
    assert d["monthly_quota_pro"]["default"] is not None        # sourced from Settings
    # unknown key / bad type are rejected, nothing persists
    assert cs.put("/admin/v1/config", json={"updates": {"nope": 1}}).status_code == 422
    assert cs.put("/admin/v1/config", json={"updates": {"monthly_quota_pro": "abc"}}).status_code == 422
    # set an override
    r = cs.put("/admin/v1/config", json={"updates": {"monthly_quota_pro": 4321}})
    assert r.status_code == 200
    cfg = r.json()["config"]["monthly_quota_pro"]
    assert cfg["value"] == 4321 and cfg["overridden"] is True
    assert len(_actions("config.update")) == 1


def test_config_default_quota_takes_effect(make_staff, staff_login, make_org):
    org = make_org()
    sup = make_staff()
    cs = staff_login(sup["email"], sup["password"])
    cs.put("/admin/v1/config", json={"updates": {"monthly_quota_pro": 98765}})
    r = cs.post(f"/admin/v1/organizations/{org['org_id']}/plan", json={"plan": "pro"})
    assert r.status_code == 200 and r.json()["monthly_log_quota"] == 98765
    assert _org(org["org_id"]).monthly_log_quota == 98765


def test_config_anchor_stale_threshold_takes_effect(make_staff, staff_login, make_org):
    org = make_org()
    _seed_confirmed_anchor(org["org_id"], ago_seconds=3600)
    sup = make_staff()
    cs = staff_login(sup["email"], sup["password"])
    # default threshold is 0 (off) -> no anchor.stale alert
    ids0 = {a["id"] for a in cs.get("/admin/v1/alerts").json()["items"]}
    assert not any(i.startswith("anchor.stale") for i in ids0)
    # enabling it via config makes the stale alert appear
    cs.put("/admin/v1/config", json={"updates": {"anchor_stale_alert_seconds": 60}})
    ids1 = {a["id"] for a in cs.get("/admin/v1/alerts").json()["items"]}
    assert any(i.startswith("anchor.stale") for i in ids1)


# ------------------------------- broadcast -----------------------------------

def test_broadcast_and_announcements(make_staff, staff_login, monkeypatch):
    import app.routers.admin_config as ac
    sent = []
    monkeypatch.setattr(ac.email_mod, "send_email", lambda **k: sent.append(k) or True)
    op = make_staff(role="operator")
    co = staff_login(op["email"], op["password"])
    assert co.post("/admin/v1/broadcast", json={"title": "x", "body": "y"}).status_code == 403
    sup = make_staff()
    cs = staff_login(sup["email"], sup["password"])
    r = cs.post("/admin/v1/broadcast", json={"title": "Maintenance", "body": "Tonight 2am UTC",
                                             "level": "warning", "email_staff": True})
    assert r.status_code == 200 and r.json()["emailed"] >= 1 and len(sent) >= 1
    assert len(_actions("broadcast.create")) == 1
    # active announcement visible to any staff (operator here)
    items = co.get("/admin/v1/announcements").json()["items"]
    ann = next(x for x in items if x["title"] == "Maintenance")
    assert ann["level"] == "warning" and ann["active"] is True
    # bad level rejected
    assert cs.post("/admin/v1/broadcast", json={"title": "a", "body": "b", "level": "nope"}).status_code == 422
    # deactivate hides it
    assert cs.post(f"/admin/v1/announcements/{ann['id']}/deactivate").status_code == 200
    assert not any(x["id"] == ann["id"] for x in co.get("/admin/v1/announcements").json()["items"])


# ------------------------------- self-service --------------------------------

def test_change_password(make_staff, staff_login):
    s = make_staff(role="viewer")
    c = staff_login(s["email"], s["password"])
    assert c.post("/admin/v1/auth/change-password",
                  json={"current_password": "wrong", "new_password": "newpass1234"}).status_code == 400
    assert c.post("/admin/v1/auth/change-password",
                  json={"current_password": s["password"], "new_password": "newpass1234"}).status_code == 200
    # the new password now works
    c2 = staff_login(s["email"], "newpass1234")
    assert c2.get("/admin/v1/auth/me").status_code == 200
    assert len(_actions("staff.password_change")) == 1


def test_self_mfa_enable_confirm_disable(make_staff, staff_login, monkeypatch):
    monkeypatch.setattr(mfa_mod, "new_otp", lambda: "123456")
    s = make_staff(role="operator")
    c = staff_login(s["email"], s["password"])
    assert c.post("/admin/v1/auth/mfa/enable").status_code == 200
    assert c.post("/admin/v1/auth/mfa/confirm", json={"code": "000000"}).status_code == 400
    assert c.post("/admin/v1/auth/mfa/confirm", json={"code": "123456"}).status_code == 200
    db = SessionLocal()
    try:
        assert db.get(StaffUser, uuid.UUID(s["id"])).mfa_enabled is True
    finally:
        db.close()
    assert c.post("/admin/v1/auth/mfa/disable", json={"password": "wrong"}).status_code == 400
    assert c.post("/admin/v1/auth/mfa/disable", json={"password": s["password"]}).status_code == 200
    db = SessionLocal()
    try:
        assert db.get(StaffUser, uuid.UUID(s["id"])).mfa_enabled is False
    finally:
        db.close()
    for act in ("staff.mfa_enable", "staff.mfa_disable"):
        assert len(_actions(act)) == 1


def test_self_activity(make_staff, staff_login, make_org):
    s = make_staff()   # superadmin
    c = staff_login(s["email"], s["password"])   # audited staff.login
    org = make_org()
    c.post(f"/admin/v1/organizations/{org['org_id']}/suspend", json={"reason": "x"})
    kinds = {a["action"] for a in c.get("/admin/v1/auth/activity").json()["items"]}
    assert "staff.login" in kinds and "org.suspend" in kinds
