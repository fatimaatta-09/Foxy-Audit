"""Ingest -> hash chain -> /v1/verify, including tamper detection through the
real HTTP path (test_chain.py covers the formula in isolation; this covers the
end-to-end write + recompute)."""

from __future__ import annotations

import hashlib

from sqlalchemy import text

from app.chain import GENESIS_HASH, compute_chain_hash
from app.db import engine


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _rows(n):
    return [{"prompt_hash": _h(f"p{i}"), "response_hash": _h(f"r{i}"),
             "token_count": 10 * i + 5, "policy_tag": "chat"} for i in range(1, n + 1)]


def _expected_hash(org, item, prev_hash):
    return compute_chain_hash(
        org_id=org["org_id"], prompt_hash=item["prompt_hash"],
        response_hash=item["response_hash"], token_count=item["token_count"],
        policy_tag=item["policy_tag"], seq=item["seq"], prev_hash=prev_hash,
        agent=item.get("agent"), chain_version=item.get("chain_version", 1),
        event_id=item.get("event_id"), client_id=item.get("client_id"),
        client_seq=item.get("client_seq"), event_type=item.get("event_type"),
        commitment_alg=item.get("commitment_alg"),
        event_metadata=item.get("event_metadata"), pii_signals=item.get("pii_signals"),
        occurred_at=item.get("occurred_at"),
    )


def test_ingest_then_verify_intact(make_org, client):
    org = make_org()
    assert client.post("/v1/logs/batch", json=_rows(5), headers=org["auth"]).status_code == 202
    v = client.get("/v1/verify", headers=org["auth"]).json()
    assert v["ok"] is True
    assert v["count"] == 5
    assert v["first_broken_seq"] is None


def test_stored_chain_hashes_link(make_org, client):
    org = make_org()
    client.post("/v1/logs/batch", json=_rows(3), headers=org["auth"])
    items = sorted(client.get("/v1/logs?limit=50", headers=org["auth"]).json()["items"],
                   key=lambda x: x["seq"])
    prev = GENESIS_HASH
    for it in items:
        expected = _expected_hash(org, it, prev)
        assert it["chain_hash"] == expected
        prev = it["chain_hash"]


def test_ingest_with_agent_stored_and_in_chain(make_org, client):
    """agent is stored on the row AND folded into that row's chain hash (6B)."""
    org = make_org()
    rows = _rows(2)
    rows[0]["agent"] = "gpt-4o"
    assert client.post("/v1/logs/batch", json=rows, headers=org["auth"]).status_code == 202
    items = sorted(client.get("/v1/logs?limit=50", headers=org["auth"]).json()["items"],
                   key=lambda x: x["seq"])
    assert items[0]["agent"] == "gpt-4o"        # stored + surfaced on the row
    assert not items[1].get("agent")            # seq 2 had no agent

    # seq 1's stored hash matches the recompute WITH agent, and NOT without it.
    assert items[0]["chain_hash"] == _expected_hash(org, items[0], GENESIS_HASH)
    no_agent = dict(items[0])
    no_agent["agent"] = None
    assert items[0]["chain_hash"] != _expected_hash(org, no_agent, GENESIS_HASH)
    # the whole chain still verifies intact
    assert client.get("/v1/verify", headers=org["auth"]).json()["ok"] is True


def test_ingest_rejects_html_in_agent(make_org, client):
    """agent is charset-locked — HTML/delimiters can't reach the chain or dashboard."""
    org = make_org()
    rows = _rows(1)
    rows[0]["agent"] = "<script>alert(1)</script>"
    assert client.post("/v1/logs/batch", json=rows, headers=org["auth"]).status_code == 422


def test_tamper_agent_detected_by_verify(make_org, client):
    """Rewriting the agent on a historical row breaks /v1/verify at that seq."""
    org = make_org()
    rows = _rows(3)
    rows[1]["agent"] = "claude-3-opus"
    client.post("/v1/logs/batch", json=rows, headers=org["auth"])
    assert client.get("/v1/verify", headers=org["auth"]).json()["ok"] is True
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE audit_logs SET agent = 'gpt-4o' WHERE org_id = :o AND seq = 2"),
            {"o": org["org_id"]})
    v = client.get("/v1/verify", headers=org["auth"]).json()
    assert v["ok"] is False
    assert v["first_broken_seq"] == 2


def test_tamper_detected_by_verify(make_org, client):
    org = make_org()
    client.post("/v1/logs/batch", json=_rows(3), headers=org["auth"])
    # Rewrite a historical row directly (as the superuser role, bypassing RLS).
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE audit_logs SET token_count = 999 WHERE org_id = :o AND seq = 2"),
            {"o": org["org_id"]},
        )
    v = client.get("/v1/verify", headers=org["auth"]).json()
    assert v["ok"] is False
    assert v["first_broken_seq"] == 2
