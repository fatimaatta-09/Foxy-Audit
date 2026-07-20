"""Integration coverage for staff-managed evaluation campaigns."""

from app.db import SessionLocal
from app.models import AdminAction, EvaluationCampaign, Organization


def _payload(offer_id="judges-july", code="JULY-JUDGES-ACCESS-2026"):
    return {
        "offer_id": offer_id,
        "label": "July judge access",
        "code": code,
        "credits": 250,
        "duration_days": 31,
        "max_redemptions": 5,
    }


def test_campaign_listing_requires_staff_and_hides_code(client, make_staff, staff_login):
    assert client.get("/admin/v1/evaluation-campaigns").status_code == 401
    viewer = make_staff(role="viewer")
    staff_client = staff_login(viewer["email"], viewer["password"])
    response = staff_client.get("/admin/v1/evaluation-campaigns")
    assert response.status_code == 200
    assert response.json() == []


def test_superadmin_creates_audited_campaign_and_viewers_can_list(
    client, make_staff, staff_login,
):
    viewer = make_staff(role="viewer")
    operator = make_staff(role="operator")
    superadmin = make_staff(role="superadmin")
    viewer_client = staff_login(viewer["email"], viewer["password"])
    operator_client = staff_login(operator["email"], operator["password"])
    superadmin_client = staff_login(superadmin["email"], superadmin["password"])

    assert viewer_client.post("/admin/v1/evaluation-campaigns", json=_payload()).status_code == 403
    assert operator_client.post("/admin/v1/evaluation-campaigns", json=_payload()).status_code == 403
    created = superadmin_client.post("/admin/v1/evaluation-campaigns", json=_payload())
    assert created.status_code == 200
    body = created.json()
    assert body["redemption_code"] == "JULY-JUDGES-ACCESS-2026"
    assert "code_hash" not in body

    listed = viewer_client.get("/admin/v1/evaluation-campaigns")
    assert listed.status_code == 200
    item = listed.json()[0]
    assert item["offer_id"] == "judges-july"
    assert item["remaining_slots"] == 5
    assert "redemption_code" not in item

    db = SessionLocal()
    try:
        campaign = db.query(EvaluationCampaign).filter_by(offer_id="judges-july").one()
        action = db.query(AdminAction).filter_by(
            action="evaluation_campaign.create", target_id="judges-july"
        ).one()
        assert campaign.code_hash != "JULY-JUDGES-ACCESS-2026"
        assert action.detail["max_redemptions"] == 5
        assert "code" not in action.detail
    finally:
        db.close()


def test_campaign_redemption_is_real_finite_access_and_revoke_stops_new_signups(
    client, make_staff, staff_login, monkeypatch,
):
    from app.routers import billing

    monkeypatch.setattr(billing.password_reset, "issue_reset", lambda *args, **kwargs: None)
    superadmin = make_staff(role="superadmin")
    operator = make_staff(role="operator")
    superadmin_client = staff_login(superadmin["email"], superadmin["password"])
    operator_client = staff_login(operator["email"], operator["password"])
    superadmin_client.post("/admin/v1/evaluation-campaigns", json=_payload(
        offer_id="judges-flow", code="JUDGES-FLOW-ACCESS-2026"
    )).raise_for_status()

    signup = client.post("/v1/signup", json={
        "email": "judge-flow@example.com",
        "name": "Judge Flow",
        "offer_code": "judges-flow-access-2026",
    })
    assert signup.status_code == 200
    body = signup.json()
    assert body["evaluation_offer"]["credits_total"] == 250
    assert body["evaluation_offer"]["no_auto_charge"] is True

    db = SessionLocal()
    try:
        org = db.query(Organization).filter_by(contact_email="judge-flow@example.com").one()
        assert org.plan_tier == "premium"
        assert org.evaluation_credit_limit == 250
    finally:
        db.close()

    revoked = operator_client.post("/admin/v1/evaluation-campaigns/judges-flow/revoke")
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    blocked = client.post("/v1/signup", json={
        "email": "second-judge@example.com",
        "offer_code": "JUDGES-FLOW-ACCESS-2026",
    })
    assert blocked.status_code == 422

    listed = operator_client.get("/admin/v1/evaluation-campaigns").json()
    item = next(item for item in listed if item["offer_id"] == "judges-flow")
    assert item["status"] == "revoked"
    assert item["redemptions"] == 1

    db = SessionLocal()
    try:
        action = db.query(AdminAction).filter_by(
            action="evaluation_campaign.revoke", target_id="judges-flow"
        ).one()
        assert action.staff_user_id == operator["id"]
    finally:
        db.close()
