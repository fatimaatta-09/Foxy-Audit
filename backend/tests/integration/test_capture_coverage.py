from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import text

from app.db import engine


def _event(client_id: str | None, seq: int | None):
    n = uuid.uuid4().hex
    return {
        "event_id": str(uuid.uuid4()),
        "client_id": client_id,
        "client_seq": seq,
        "prompt_hash": hashlib.sha256(f"prompt-{n}".encode()).hexdigest(),
        "response_hash": hashlib.sha256(f"response-{n}".encode()).hexdigest(),
        "token_count": 4,
        "policy_tag": "chat",
    }


def _coverage(client, org):
    return client.get("/v1/coverage", headers=org["auth"])


def test_empty_workspace_is_unknown_not_clean(make_org, client):
    org = make_org()

    response = _coverage(client, org)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "unknown"
    assert body["total_events"] == 0
    assert body["chain_verified"] is True
    assert "cannot be assessed" in body["message"]


def test_contiguous_sdk_sequences_are_verified(make_org, client):
    org = make_org()
    events = [_event("sdk-a", n) for n in range(1, 4)]
    assert client.post("/v1/logs/batch", json=events,
                       headers=org["auth"]).status_code == 202

    response = _coverage(client, org)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "verified"
    assert body["total_events"] == 3
    assert body["identified_events"] == 3
    assert body["events_without_client_identity"] == 0
    assert body["missing_events"] == 0
    assert body["duplicate_client_sequences"] == 0
    assert body["chain_verification"] == "verified"
    assert len(body["clients"]) == 1
    client_coverage = body["clients"][0]
    assert client_coverage["client_id"] == "sdk-a"
    assert client_coverage["events"] == 3
    assert client_coverage["first_client_seq"] == 1
    assert client_coverage["last_client_seq"] == 3
    assert client_coverage["server_seq_start"] == 1
    assert client_coverage["server_seq_end"] == 3
    assert client_coverage["last_seen_at"]
    assert client_coverage["missing_ranges"] == []
    assert client_coverage["duplicate_client_sequences"] == []


def test_partial_capture_reports_ranges_duplicates_and_unidentified_events(make_org, client):
    org = make_org()
    events = [_event("sdk-a", 1), _event("sdk-a", 3), _event(None, None)]
    assert client.post("/v1/logs/batch", json=events,
                       headers=org["auth"]).status_code == 202
    # Reusing a client sequence is a distinct continuity anomaly.
    assert client.post("/v1/logs/batch", json=[_event("sdk-a", 3)],
                       headers=org["auth"]).status_code == 202

    response = _coverage(client, org)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["status"] == "partial"
    assert body["events_without_client_identity"] == 1
    assert body["missing_events"] == 1
    assert body["duplicate_client_sequences"] == 1
    assert body["clients_with_anomalies"] == 1
    assert body["clients"][0]["missing_ranges"] == [{"start": 2, "end": 2, "count": 1}]
    assert body["clients"][0]["duplicate_client_sequences"] == [3]


def test_coverage_is_tenant_scoped(make_org, client):
    first = make_org()
    second = make_org()
    assert client.post("/v1/logs/batch", json=[_event("first-sdk", 1)],
                       headers=first["auth"]).status_code == 202
    assert client.post("/v1/logs/batch", json=[_event("second-sdk", 1)],
                       headers=second["auth"]).status_code == 202

    response = _coverage(client, first)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["total_events"] == 1
    assert body["instrumented_clients"] == 1
    assert body["clients"][0]["client_id"] == "first-sdk"


def test_coverage_requires_customer_auth(client):
    response = client.get("/v1/coverage")
    assert response.status_code == 401


def test_coverage_reports_chain_failure(make_org, client):
    org = make_org()
    assert client.post("/v1/logs/batch", json=[_event("sdk-a", 1), _event("sdk-a", 2)],
                       headers=org["auth"]).status_code == 202
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM audit_logs WHERE org_id = :org AND seq = 1"),
                     {"org": org["org_id"]})

    response = _coverage(client, org)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["status"] == "partial"
    assert body["chain_verified"] is False
    assert body["chain_verification"] == "failed"
    assert "sequence gap" in body["chain_detail"]
