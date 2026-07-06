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
        expected = compute_chain_hash(
            org_id=org["org_id"], prompt_hash=it["prompt_hash"],
            response_hash=it["response_hash"], token_count=it["token_count"],
            policy_tag=it["policy_tag"], seq=it["seq"], prev_hash=prev,
        )
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
    common = dict(org_id=org["org_id"], prompt_hash=items[0]["prompt_hash"],
                  response_hash=items[0]["response_hash"], token_count=items[0]["token_count"],
                  policy_tag=items[0]["policy_tag"], seq=1, prev_hash=GENESIS_HASH)
    assert items[0]["chain_hash"] == compute_chain_hash(**common, agent="gpt-4o")
    assert items[0]["chain_hash"] != compute_chain_hash(**common)
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
