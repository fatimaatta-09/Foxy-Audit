"""Enterprise AI-system registry: tenancy, RBAC, auditability, and evidence binding."""

from __future__ import annotations

import hashlib

from sqlalchemy import text

from app.db import engine


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _system_payload(**overrides):
    payload = {
        "name": "Claims assistant",
        "owner_email": "risk@example.test",
        "purpose": "Summarizes submitted insurance claims for staff review",
        "provider": "openai",
        "model_name": "gpt-5.6",
        "environment": "production",
        "data_classification": "regulated",
        "risk_tier": "high",
    }
    payload.update(overrides)
    return payload


def _event(system_id: str, suffix: str = "one"):
    return {
        "prompt_hash": _hash(f"prompt-{suffix}"),
        "response_hash": _hash(f"response-{suffix}"),
        "token_count": 12,
        "policy_tag": "claims",
        "system_id": system_id,
    }


def test_admin_creates_system_and_account_action(make_org, login):
    org = make_org()
    admin = login(org["admin_email"], org["admin_password"])
    created = admin.post("/v1/systems", json=_system_payload())
    assert created.status_code == 201, created.text
    system = created.json()
    assert system["name"] == "Claims assistant"
    assert system["owner_email"] == "risk@example.test"
    assert system["lifecycle_status"] == "active"

    actions = admin.get("/v1/account/audit").json()
    action = next(a for a in actions if a["action"] == "system.create")
    assert action["target"] == "Claims assistant"
    assert action["detail"]["system_id"] == system["id"]

    exported = admin.get("/v1/account/export").json()
    assert [item["id"] for item in exported["ai_systems"]] == [system["id"]]


def test_system_reads_are_tenant_scoped_and_member_reads_only(make_org, add_user, login):
    org_a = make_org()
    org_b = make_org()
    admin_a = login(org_a["admin_email"], org_a["admin_password"])
    system_id = admin_a.post("/v1/systems", json=_system_payload()).json()["id"]
    add_user(org_a["org_id"], "member@example.test", "memberpass1", role="member")

    member = login("member@example.test", "memberpass1")
    assert [s["id"] for s in member.get("/v1/systems").json()] == [system_id]
    assert member.post("/v1/systems", json=_system_payload(name="Member system")).status_code == 403

    admin_b = login(org_b["admin_email"], org_b["admin_password"])
    assert admin_b.get(f"/v1/systems/{system_id}").status_code == 404
    assert admin_b.put(f"/v1/systems/{system_id}", json={"risk_tier": "low"}).status_code == 404

    assert admin_a.put(f"/v1/systems/{system_id}", json={"risk_tier": None}).status_code == 422
    assert admin_a.put(f"/v1/systems/{system_id}", json={}).status_code == 422


def test_active_system_is_bound_into_the_verifiable_event(make_org, client, login):
    org = make_org()
    admin = login(org["admin_email"], org["admin_password"])
    system = admin.post("/v1/systems", json=_system_payload()).json()

    accepted = client.post("/v1/logs/batch", headers=org["auth"], json=[_event(system["id"])])
    assert accepted.status_code == 202, accepted.text
    logged = client.get("/v1/logs?limit=1", headers=org["auth"]).json()["items"][0]
    assert logged["chain_version"] == 3
    assert logged["event_metadata"]["system_id"] == system["id"]
    assert client.get("/v1/verify", headers=org["auth"]).json()["ok"] is True

    # Historical attribution is in V3 event metadata. A database-side change
    # breaks the recomputed chain rather than silently relabeling evidence.
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE audit_logs SET event_metadata = event_metadata || "
            "jsonb_build_object('system_id', CAST(:system_id AS text)) WHERE org_id = :org_id"
        ), {"org_id": org["org_id"], "system_id": "00000000-0000-0000-0000-000000000001"})
    verification = client.get("/v1/verify", headers=org["auth"]).json()
    assert verification["ok"] is False


def test_foreign_or_retired_system_cannot_receive_new_events(make_org, client, login):
    org_a = make_org()
    org_b = make_org()
    admin_a = login(org_a["admin_email"], org_a["admin_password"])
    system = admin_a.post("/v1/systems", json=_system_payload()).json()

    foreign = client.post("/v1/logs/batch", headers=org_b["auth"], json=[_event(system["id"], "foreign")])
    assert foreign.status_code == 409

    retired = admin_a.post(f"/v1/systems/{system['id']}/retire")
    assert retired.status_code == 200 and retired.json()["lifecycle_status"] == "retired"
    actions = admin_a.get("/v1/account/audit").json()
    assert any(a["action"] == "system.retire" for a in actions)

    blocked = client.post("/v1/logs/batch", headers=org_a["auth"], json=[_event(system["id"], "retired")])
    assert blocked.status_code == 409
