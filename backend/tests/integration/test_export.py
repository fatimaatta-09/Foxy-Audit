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


def test_export_verifies_with_standalone_verifier(make_org, client):
    """The real backend export must verify under the INDEPENDENT verifier/ tool
    (byte-for-byte recipe parity, incl. the 6B agent field) and its embedded anchor
    receipt must match — then tampering must be caught at the right seq. This is the
    whole trust claim of the open-source verifier (Phase 6 · 6D)."""
    import importlib.util
    import os
    import uuid

    from app.anchor import anchor_org
    from app.db import SessionLocal

    org = make_org()
    rows = [{"prompt_hash": _h(f"p{i}"), "response_hash": _h(f"r{i}"),
             "token_count": 10 * i + 5, "policy_tag": "chat"} for i in range(3)]
    rows[1]["agent"] = "gpt-4o"                              # an agent-bearing row
    assert client.post("/v1/logs/batch", json=rows, headers=org["auth"]).status_code == 202
    db = SessionLocal()
    try:
        anchor_org(db, uuid.UUID(org["org_id"]), force=True)   # embed a real anchor receipt
    finally:
        db.close()

    export = client.get("/v1/logs/export", headers=org["auth"]).json()
    assert export["anchor"] is not None and export["anchor"]["last_seq"] == 3

    vpath = os.path.join(os.path.dirname(__file__), "..", "..", "..", "verifier", "foxy_verify.py")
    spec = importlib.util.spec_from_file_location("foxy_verify", vpath)
    fv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fv)

    res = fv.verify_export(export)
    assert res["ok"] is True and res["count"] == 3          # backend export verifies clean
    assert fv.check_anchor_offline(export, res)["matches"] is True   # anchor root matches

    export["logs"][1]["token_count"] = 999                  # tamper the agent row
    assert fv.verify_export(export)["first_broken_seq"] == 2


def test_export_requires_auth(client):
    assert client.get("/v1/logs/export").status_code == 401


def test_export_over_session(make_org, login, client):
    org = make_org()
    _ingest(client, org, 1)
    c = login(org["admin_email"], org["admin_password"])
    r = c.get("/v1/logs/export")
    assert r.status_code == 200 and r.json()["count"] == 1
