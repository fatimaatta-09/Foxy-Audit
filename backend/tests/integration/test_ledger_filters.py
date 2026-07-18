"""P1 · §C — server-side ledger search/filter on GET /v1/logs.

Filters compose (AND) and apply to both the count and the page. Tenant-isolated.
"""
from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import text

from app.db import engine


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _ingest(client, auth, *, agent, policy="chat", tag_seed="x"):
    ev = {
        "event_id": str(uuid.uuid4()),
        "prompt_hash": _h("p-" + tag_seed), "response_hash": _h("r-" + tag_seed),
        "token_count": 10, "policy_tag": policy, "agent": agent,
    }
    r = client.post("/v1/logs/batch", json=[ev], headers=auth)
    assert r.status_code == 202, r.text
    return r.json()["receipts"][0]


def _set_verdict(org_id, seq, *, decision, breach):
    with engine.begin() as c:
        c.execute(text(
            "UPDATE audit_logs SET grading_status='graded', "
            "gemini_verdict = cast(:v as jsonb) WHERE org_id=:o AND seq=:s"),
            {"v": '{"policy_breach": %s, "decision": "%s", "risk_score": %d}'
                  % ("true" if breach else "false", decision, 80 if breach else 0),
             "o": str(org_id), "s": seq})


def test_filter_by_policy_and_agent(make_org, client):
    org = make_org()
    _ingest(client, org["auth"], agent="gpt-4o", policy="chat", tag_seed="a")
    _ingest(client, org["auth"], agent="claude-3", policy="rag", tag_seed="b")
    byp = client.get("/v1/logs?policy_tag=rag", headers=org["auth"]).json()
    assert byp["total"] == 1 and byp["items"][0]["policy_tag"] == "rag"
    bya = client.get("/v1/logs?agent=gpt-4o", headers=org["auth"]).json()
    assert bya["total"] == 1 and bya["items"][0]["agent"] == "gpt-4o"


def test_filter_by_hash_query(make_org, client):
    org = make_org()
    _ingest(client, org["auth"], agent="gpt-4o", tag_seed="unique-seed-1")
    _ingest(client, org["auth"], agent="gpt-4o", tag_seed="unique-seed-2")
    all_rows = client.get("/v1/logs", headers=org["auth"]).json()["items"]
    target = all_rows[0]["chain_hash"]
    r = client.get(f"/v1/logs?q={target[:16]}", headers=org["auth"]).json()
    assert r["total"] == 1 and r["items"][0]["chain_hash"] == target


def test_filter_by_verdict(make_org, client):
    org = make_org()
    _ingest(client, org["auth"], agent="a1", tag_seed="v1")   # seq 1
    _ingest(client, org["auth"], agent="a2", tag_seed="v2")   # seq 2
    _ingest(client, org["auth"], agent="a3", tag_seed="v3")   # seq 3 stays pending
    _set_verdict(org["org_id"], 1, decision="breach", breach=True)
    _set_verdict(org["org_id"], 2, decision="clean", breach=False)
    assert client.get("/v1/logs?verdict=breach", headers=org["auth"]).json()["total"] == 1
    assert client.get("/v1/logs?verdict=clean", headers=org["auth"]).json()["total"] == 1
    assert client.get("/v1/logs?verdict=pending", headers=org["auth"]).json()["total"] == 1


def test_filters_compose_and_paginate_totals(make_org, client):
    org = make_org()
    for i in range(3):
        _ingest(client, org["auth"], agent="gpt-4o", policy="chat", tag_seed=f"c{i}")
    _ingest(client, org["auth"], agent="gpt-4o", policy="rag", tag_seed="c9")
    r = client.get("/v1/logs?agent=gpt-4o&policy_tag=chat&limit=2", headers=org["auth"]).json()
    assert r["total"] == 3 and len(r["items"]) == 2   # total honest, page capped


def test_filter_is_tenant_isolated(make_org, client):
    org_a = make_org()
    org_b = make_org()
    _ingest(client, org_b["auth"], agent="secret-agent", tag_seed="b-only")
    b_hash = client.get("/v1/logs", headers=org_b["auth"]).json()["items"][0]["chain_hash"]
    # org A searching for org B's exact hash / agent sees nothing
    assert client.get(f"/v1/logs?q={b_hash}", headers=org_a["auth"]).json()["total"] == 0
    assert client.get("/v1/logs?agent=secret-agent", headers=org_a["auth"]).json()["total"] == 0
