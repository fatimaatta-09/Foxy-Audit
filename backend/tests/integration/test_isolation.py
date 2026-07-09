"""Multi-tenant isolation: one org must never see another org's logs, stats,
keys, or anchors. This file exercises the app-level org_id scoping over the API.
The database-level guarantee — Postgres RLS under the confined `foxy_app` role —
is proven separately in test_rls.py (a scoped, unfiltered SELECT sees only its
own org's rows)."""

from __future__ import annotations

import hashlib


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _ingest(client, org, n, tag):
    rows = [{"prompt_hash": _h(f"{tag}p{i}"), "response_hash": _h(f"{tag}r{i}"),
             "token_count": 100 + i, "policy_tag": tag} for i in range(n)]
    assert client.post("/v1/logs/batch", json=rows, headers=org["auth"]).status_code == 202


def test_logs_isolated_between_orgs(make_org, client):
    a = make_org()
    b = make_org()
    _ingest(client, a, 3, "aaa")
    _ingest(client, b, 2, "bbb")

    la = client.get("/v1/logs?limit=50", headers=a["auth"]).json()
    lb = client.get("/v1/logs?limit=50", headers=b["auth"]).json()
    assert la["total"] == 3
    assert lb["total"] == 2
    assert all(it["policy_tag"] == "aaa" for it in la["items"])
    assert all(it["policy_tag"] == "bbb" for it in lb["items"])


def test_stats_isolated_between_orgs(make_org, client):
    a = make_org()
    b = make_org()
    _ingest(client, a, 4, "aaa")
    _ingest(client, b, 1, "bbb")
    assert client.get("/v1/stats", headers=a["auth"]).json()["total_logged"] == 4
    assert client.get("/v1/stats", headers=b["auth"]).json()["total_logged"] == 1


def test_verify_isolated_between_orgs(make_org, client):
    a = make_org()
    b = make_org()
    _ingest(client, a, 5, "aaa")
    _ingest(client, b, 2, "bbb")
    assert client.get("/v1/verify", headers=a["auth"]).json()["count"] == 5
    assert client.get("/v1/verify", headers=b["auth"]).json()["count"] == 2


def test_other_org_sees_zero_of_my_rows(make_org, client):
    """Direct isolation proof independent of counts: with only org A holding data,
    org B's key must see exactly zero rows (a missing org_id filter would leak them)."""
    a = make_org()
    b = make_org()
    _ingest(client, a, 4, "aaa")
    assert client.get("/v1/logs?limit=50", headers=b["auth"]).json()["total"] == 0
    assert client.get("/v1/verify", headers=b["auth"]).json()["count"] == 0
    assert client.get("/v1/logs?limit=50", headers=a["auth"]).json()["total"] == 4


def test_keys_isolated_between_orgs(make_org, login):
    a = make_org()
    b = make_org()
    ca = login(a["admin_email"], a["admin_password"])
    cb = login(b["admin_email"], b["admin_password"])
    a_key = ca.post("/v1/keys", json={"name": "a-only-key"}).json()

    keys_a = ca.get("/v1/keys").json()
    keys_b = cb.get("/v1/keys").json()
    # a: seeded 'primary' + 'a-only-key' = 2; b: just 'primary' = 1
    assert len(keys_a) == 2
    assert len(keys_b) == 1
    # a's key must not appear in b's list (by id, not just name).
    assert a_key["id"] in {k["id"] for k in keys_a}
    assert a_key["id"] not in {k["id"] for k in keys_b}
