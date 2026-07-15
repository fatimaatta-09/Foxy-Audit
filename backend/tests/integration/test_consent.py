"""POST /v1/consent — records one auditable, anonymized cookie-consent row."""
from app.db import SessionLocal
from app.models import ConsentEvent


def test_consent_records_choice_anonymized(client):
    r = client.post("/v1/consent", json={"analytics": True, "functional": False,
                                          "region": "eu", "regime": "gdpr", "policy_version": "1.0"})
    assert r.status_code == 200 and r.json()["status"] == "recorded"
    db = SessionLocal()
    row = db.query(ConsentEvent).order_by(ConsentEvent.created_at.desc()).first()
    assert row is not None
    assert row.analytics is True and row.functional is False
    assert row.regime == "gdpr" and row.region == "eu"
    assert row.ip_hash and len(row.ip_hash) == 64        # HMAC hash, never the raw IP
    db.close()


def test_consent_defaults_to_reject(client):
    assert client.post("/v1/consent", json={}).status_code == 200
    db = SessionLocal()
    row = db.query(ConsentEvent).order_by(ConsentEvent.created_at.desc()).first()
    assert row.analytics is False and row.functional is False
    db.close()
