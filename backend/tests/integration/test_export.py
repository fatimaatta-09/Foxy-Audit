"""Customer data-export — a tenant pulls its OWN audit logs (Phase 5 · 5D.4).

GET /v1/logs/export returns every audit_logs row for the caller's org (and only
that org), as a downloadable JSON (default) or CSV. Works over the SDK Bearer key
or the dashboard session; unauthenticated is 401.
"""

from __future__ import annotations

import hashlib


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _ingest(client, org, n: int) -> None:
    rows = [{"prompt_hash": _h(f"{org['org_id']}p{i}"),
             "response_hash": _h(f"{org['org_id']}r{i}"),
             "token_count": 100, "policy_tag": "chat"} for i in range(n)]
    assert client.post("/v1/logs/batch", json=rows,
                       headers=org["auth"]).status_code == 202


def test_export_json_is_tenant_scoped(make_org, client):
    a, b = make_org(), make_org()
    _ingest(client, a, 3)
    _ingest(client, b, 2)

    r = client.get("/v1/logs/export", headers=a["auth"])
    assert r.status_code == 200
    assert "attachment" in r.headers.get("content-disposition", "")
    data = r.json()
    assert data["count"] == 3                       # only org A's rows
    assert len(data["logs"]) == 3
    assert all("chain_hash" in row for row in data["logs"])


def test_export_csv(make_org, client):
    a = make_org()
    _ingest(client, a, 2)
    r = client.get("/v1/logs/export?format=csv", headers=a["auth"])
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("seq,")              # header row
    assert len(lines) == 3                          # header + 2 data rows


def test_export_requires_auth(client):
    assert client.get("/v1/logs/export").status_code == 401


def test_export_over_session(make_org, login, client):
    org = make_org()
    _ingest(client, org, 1)
    c = login(org["admin_email"], org["admin_password"])
    r = c.get("/v1/logs/export")
    assert r.status_code == 200 and r.json()["count"] == 1
