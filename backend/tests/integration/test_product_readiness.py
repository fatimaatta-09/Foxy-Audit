"""Trial, entitlement, and workspace-access acceptance tests."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from app.db import SessionLocal
from app.models import AdminAction, Organization


def _h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _row(label: str = "one") -> dict:
    return {"prompt_hash": _h("prompt-" + label),
            "response_hash": _h("response-" + label),
            "token_count": 8, "policy_tag": "test"}


def test_expired_free_trial_blocks_new_capture(client, monkeypatch):
    from app.routers import billing as billing_mod
    monkeypatch.setattr(billing_mod.password_reset, "issue_reset", lambda *a, **k: None)
    response = client.post("/v1/signup", json={"email": "trial@test.dev"})
    assert response.status_code == 200
    body = response.json()

    db = SessionLocal()
    try:
        org = db.get(Organization, uuid.UUID(body["org_id"]))
        org.trial_ends_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()

    blocked = client.post("/v1/logs/batch", json=[_row()],
                          headers={"Authorization": f"Bearer {body['api_key']}"})
    assert blocked.status_code == 402
    assert blocked.json()["detail"]["code"] == "trial_expired"


def test_free_plan_limits_employee_seats(make_org, add_user, login):
    org_data = make_org()
    db = SessionLocal()
    try:
        org = db.get(Organization, uuid.UUID(org_data["org_id"]))
        org.plan_tier = "free"
        db.commit()
    finally:
        db.close()
    add_user(org_data["org_id"], "second@test.dev", "secondpass1")
    dashboard = login(org_data["admin_email"], org_data["admin_password"])
    response = dashboard.post("/v1/auth/users",
                              json={"email": "third@test.dev", "role": "member"})
    assert response.status_code == 402
    assert response.json()["detail"]["code"] == "seat_limit_reached"


def test_free_plan_limits_active_api_keys(make_org, login):
    org_data = make_org()
    db = SessionLocal()
    try:
        org = db.get(Organization, uuid.UUID(org_data["org_id"]))
        org.plan_tier = "free"
        db.commit()
    finally:
        db.close()
    dashboard = login(org_data["admin_email"], org_data["admin_password"])
    response = dashboard.post("/v1/keys", json={"name": "staging"})
    assert response.status_code == 402
    assert response.json()["detail"]["code"] == "api_key_limit_reached"


def test_suspended_workspace_is_closed_on_machine_and_human_channels(make_org, client):
    org_data = make_org()
    db = SessionLocal()
    try:
        org = db.get(Organization, uuid.UUID(org_data["org_id"]))
        org.suspended = True
        db.commit()
    finally:
        db.close()

    machine = client.get("/v1/logs", headers=org_data["auth"])
    assert machine.status_code == 403
    human = client.post("/v1/auth/login", json={
        "email": org_data["admin_email"], "password": org_data["admin_password"]})
    assert human.status_code == 403


def test_superadmin_can_apply_custom_plan_and_audit_it(make_org, make_staff, staff_login):
    org_data = make_org()
    viewer = make_staff(role="viewer")
    superadmin = make_staff(role="superadmin")
    viewer_client = staff_login(viewer["email"], viewer["password"])
    assert viewer_client.post(
        f"/admin/v1/organizations/{org_data['org_id']}/plan",
        json={"plan": "premium"},
    ).status_code == 403

    staff_client = staff_login(superadmin["email"], superadmin["password"])
    response = staff_client.post(
        f"/admin/v1/organizations/{org_data['org_id']}/plan",
        json={"plan": "premium", "monthly_log_quota": 750000},
    )
    assert response.status_code == 200
    assert response.json()["plan_tier"] == "premium"
    db = SessionLocal()
    try:
        org = db.get(Organization, uuid.UUID(org_data["org_id"]))
        action = db.query(AdminAction).filter(
            AdminAction.action == "org.plan.set",
            AdminAction.target_org_id == org.id,
        ).one()
        assert org.plan_tier == "premium"
        assert org.monthly_log_quota == 750000
        assert action.detail["plan"] == "premium"
    finally:
        db.close()
