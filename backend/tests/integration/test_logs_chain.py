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
