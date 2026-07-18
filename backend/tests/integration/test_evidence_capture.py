from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import text

from app.db import engine


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _event(client_id: str, seq: int, event_id: str | None = None, **extra):
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "client_id": client_id,
        "client_seq": seq,
        "prompt_hash": _hash(f"p-{seq}"),
        "response_hash": _hash(f"r-{seq}"),
        "token_count": 10,
        "policy_tag": "chat",
        **extra,
    }


def test_event_receipt_is_idempotent(make_org, client):
    org = make_org()
    event = _event("sdk-a", 1)
    first = client.post("/v1/logs/batch", json=[event], headers=org["auth"])
    assert first.status_code == 202, first.text
    receipt = first.json()["receipts"][0]
    again = client.post("/v1/logs/batch", json=[event], headers=org["auth"])
    assert again.status_code == 202, again.text
    assert again.json()["receipts"][0]["status"] == "duplicate"
    assert again.json()["receipts"][0]["chain_hash"] == receipt["chain_hash"]
    assert client.get("/v1/logs?limit=50", headers=org["auth"]).json()["total"] == 1


def test_client_sequence_gap_is_reported_and_verifiable(make_org, client):
    org = make_org()
    assert client.post("/v1/logs/batch", json=[_event("sdk-a", 1)], headers=org["auth"]).status_code == 202
    second = client.post("/v1/logs/batch", json=[_event("sdk-a", 3)], headers=org["auth"])
    assert second.status_code == 202, second.text
    assert second.json()["warnings"] == [{"client_id": "sdk-a", "expected": 2, "received": 3}]
    assert client.get("/v1/verify", headers=org["auth"]).json()["ok"] is True


def test_conflicting_event_id_is_rejected(make_org, client):
    org = make_org()
    event_id = str(uuid.uuid4())
    assert client.post("/v1/logs/batch", json=[_event("sdk-a", 1, event_id)], headers=org["auth"]).status_code == 202
    conflict = _event("sdk-a", 1, event_id, token_count=999)
    assert client.post("/v1/logs/batch", json=[conflict], headers=org["auth"]).status_code == 409


def test_verify_detects_missing_server_sequence(make_org, client):
    org = make_org()
    rows = [_event("sdk-a", i) for i in range(1, 4)]
    assert client.post("/v1/logs/batch", json=rows, headers=org["auth"]).status_code == 202
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM audit_logs WHERE org_id = :org AND seq = 2"),
                     {"org": org["org_id"]})
    result = client.get("/v1/verify", headers=org["auth"])
    assert result.status_code == 200
    assert result.json()["ok"] is False
    assert "sequence gap" in result.json()["detail"]


def test_unknown_is_not_reported_as_clean(make_org, client):
    org = make_org()
    assert client.post("/v1/logs/batch", json=[_event("sdk-a", 1)], headers=org["auth"]).status_code == 202
    stats = client.get("/v1/stats", headers=org["auth"]).json()
    assert stats["clean_rate"] is None


def test_local_policy_engine_and_immutable_verdict_event(make_org, client):
    org = make_org()
    event = _event("sdk-a", 1, pii_signals=["email"])
    assert client.post("/v1/logs/batch", json=[event], headers=org["auth"]).status_code == 202
    from app import worker
    from app.db import SessionLocal
    db = SessionLocal()
    try:
        rows = worker._claim_batch(db, 10, 300)
        worker._grade_one(db, rows[0])
        verdict = db.execute(text("SELECT gemini_verdict FROM audit_logs")).scalar_one()
        event_count = db.execute(text("SELECT count(*) FROM audit_events")).scalar_one()
    finally:
        db.close()
    assert verdict["decision"] == "breach"
    assert event_count == 1
