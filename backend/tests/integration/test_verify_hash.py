"""GET /v1/verify/hash/{hash} — single-entry ledger lookup by chain hash (Phase 2).

Backs the dashboard "paste any hash to check status" card with real data instead of
the old client-side mockLedger. Covers hit / miss / org-isolation / tamper.
"""
from __future__ import annotations

import hashlib

from sqlalchemy import text

from app.db import engine

_h = lambda s: hashlib.sha256(s.encode()).hexdigest()  # noqa: E731


def _rows(n):
    return [{"prompt_hash": _h(f"p{i}"), "response_hash": _h(f"r{i}"),
             "token_count": 10 * i + 5, "policy_tag": "chat"} for i in range(1, n + 1)]


def _seed(client, org, n=3):
    assert client.post("/v1/logs/batch", json=_rows(n), headers=org["auth"]).status_code == 202
    return sorted(client.get("/v1/logs?limit=50", headers=org["auth"]).json()["items"],
                  key=lambda x: x["seq"])


def test_hash_lookup_hit(make_org, client):
    org = make_org()
    items = _seed(client, org, 3)
    h = items[1]["chain_hash"]
    r = client.get(f"/v1/verify/hash/{h}", headers=org["auth"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["found"] is True
    assert d["seq"] == items[1]["seq"]
    assert d["chain_hash"] == h
    assert d["verified"] is True
    assert d["status"] in ("safe", "pending", "breach", "flag")


def test_hash_lookup_miss(make_org, client):
    org = make_org()
    _seed(client, org, 2)
    r = client.get(f"/v1/verify/hash/{_h('does-not-exist')}", headers=org["auth"])
    assert r.status_code == 200, r.text
    assert r.json()["found"] is False


def test_hash_lookup_is_org_scoped(make_org, client):
    """Org B cannot resolve org A's hash (org filter + RLS)."""
    a = make_org()
    b = make_org()
    h = _seed(client, a, 2)[0]["chain_hash"]
    assert client.get(f"/v1/verify/hash/{h}", headers=a["auth"]).json()["found"] is True
    assert client.get(f"/v1/verify/hash/{h}", headers=b["auth"]).json()["found"] is False


def test_hash_lookup_reports_tampered(make_org, client):
    """A tampered row is still found by its (unchanged) hash but reports verified=False."""
    org = make_org()
    items = _seed(client, org, 3)
    h = items[1]["chain_hash"]
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE audit_logs SET token_count = 999 "
                 "WHERE org_id = :o AND chain_hash = :h"),
            {"o": org["org_id"], "h": h})
    d = client.get(f"/v1/verify/hash/{h}", headers=org["auth"]).json()
    assert d["found"] is True
    assert d["verified"] is False
    assert d["status"] == "tampered"
