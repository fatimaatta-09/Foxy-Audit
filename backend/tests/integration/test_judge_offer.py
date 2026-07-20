"""Real judge-offer provisioning and finite-credit enforcement."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models import EvaluationRedemption, Organization


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _rows(count: int) -> list[dict]:
    return [
        {
            "prompt_hash": _hash(f"judge-prompt-{index}"),
            "response_hash": _hash(f"judge-response-{index}"),
            "token_count": 12,
            "policy_tag": "judge_test",
            "agent": "judge-demo",
        }
        for index in range(count)
    ]


def _enable_offer(monkeypatch, *, credits: int = 2, days: int = 30, max_redemptions: int = 2):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "judge_offer_code", "JUDGE-2026-PRIVATE")
    monkeypatch.setattr(settings, "judge_offer_id", "judge-test")
    monkeypatch.setattr(settings, "judge_offer_credits", credits)
    monkeypatch.setattr(settings, "judge_offer_days", days)
    monkeypatch.setattr(settings, "judge_offer_max_redemptions", max_redemptions)


def _disable_signup_email(monkeypatch):
    from app.routers import billing

    monkeypatch.setattr(billing.password_reset, "issue_reset", lambda *args, **kwargs: None)


def test_judge_offer_provisions_premium_workspace_and_reports_allowance(client, monkeypatch):
    _enable_offer(monkeypatch, credits=2000, days=30)
    _disable_signup_email(monkeypatch)

    response = client.post("/v1/signup", json={
        "email": "judge@devpost.test", "name": "Judge Workspace",
        "offer_code": "judge-2026-private",
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["evaluation_offer"]["credits_total"] == 2000
    assert body["evaluation_offer"]["credits_remaining"] == 2000
    assert body["evaluation_offer"]["no_auto_charge"] is True

    db = SessionLocal()
    try:
        org = db.get(Organization, uuid.UUID(body["org_id"]))
        redemption = db.execute(select(EvaluationRedemption)).scalar_one()
        assert org.plan_tier == "premium"
        assert org.monthly_log_quota is None
        assert org.evaluation_credit_limit == 2000
        assert org.evaluation_credits_used == 0
        assert redemption.offer_id == "judge-test"
        assert "JUDGE-2026-PRIVATE" not in repr(redemption.__dict__)
    finally:
        db.close()

    usage = client.get("/v1/usage", headers={"Authorization": f"Bearer {body['api_key']}"})
    assert usage.status_code == 200
    evaluation = usage.json()["quota"]["evaluation"]
    assert evaluation["active"] is True
    assert evaluation["credits_total"] == 2000
    assert evaluation["credits_remaining"] == 2000


def test_judge_offer_rejects_invalid_reused_and_exhausted_codes(client, monkeypatch):
    _enable_offer(monkeypatch, max_redemptions=1)
    _disable_signup_email(monkeypatch)

    invalid = client.post("/v1/signup", json={
        "email": "invalid@devpost.test", "offer_code": "not-the-code",
    })
    assert invalid.status_code == 422
    assert invalid.json()["detail"] == "This evaluation offer is unavailable"

    first = client.post("/v1/signup", json={
        "email": "judge@devpost.test", "offer_code": "JUDGE-2026-PRIVATE",
    })
    assert first.status_code == 200, first.text

    repeated = client.post("/v1/signup", json={
        "email": "judge@devpost.test", "offer_code": "JUDGE-2026-PRIVATE",
    })
    assert repeated.status_code == 422
    assert repeated.json()["detail"] == "This evaluation offer is unavailable"

    exhausted = client.post("/v1/signup", json={
        "email": "second@devpost.test", "offer_code": "JUDGE-2026-PRIVATE",
    })
    assert exhausted.status_code == 422
    assert exhausted.json()["detail"] == "This evaluation offer is unavailable"


def test_judge_offer_enforces_credits_and_preserves_existing_evidence(client, monkeypatch):
    _enable_offer(monkeypatch, credits=2)
    _disable_signup_email(monkeypatch)
    signup = client.post("/v1/signup", json={
        "email": "credits@devpost.test", "offer_code": "JUDGE-2026-PRIVATE",
    })
    assert signup.status_code == 200, signup.text
    key = signup.json()["api_key"]
    headers = {"Authorization": f"Bearer {key}"}

    accepted = client.post("/v1/logs/batch", json=_rows(2), headers=headers)
    assert accepted.status_code == 202, accepted.text
    blocked = client.post("/v1/logs/batch", json=_rows(1), headers=headers)
    assert blocked.status_code == 402
    assert blocked.json()["detail"]["code"] == "evaluation_credits_exhausted"

    usage = client.get("/v1/usage", headers=headers).json()["quota"]["evaluation"]
    assert usage["credits_used"] == 2
    assert usage["credits_remaining"] == 0

    db = SessionLocal()
    try:
        org = db.get(Organization, uuid.UUID(signup.json()["org_id"]))
        org.evaluation_ends_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    finally:
        db.close()

    expired = client.post("/v1/logs/batch", json=_rows(1), headers=headers)
    assert expired.status_code == 402
    assert expired.json()["detail"]["code"] == "evaluation_expired"

    # An offer end never erases records or invalidates verification of prior work.
    assert client.get("/v1/logs", headers=headers).json()["total"] == 2
    assert client.get("/v1/verify", headers=headers).json()["ok"] is True
    assert client.get("/v1/usage", headers=headers).json()["quota"]["evaluation"]["active"] is False
