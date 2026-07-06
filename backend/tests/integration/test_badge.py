"""Embeddable public trust badge (Phase 6 · 6C).

Opt-in per org: an admin mints a public token; GET /v1/badge/{token}.svg returns a
live SVG (verified status + audited count + anchor freshness) exposing NO tenant
details (never the org_id, name, or any log)."""

from __future__ import annotations

import hashlib
import json
import uuid

from app.db import SessionLocal


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _rows(n):
    return [{"prompt_hash": _h(f"p{i}"), "response_hash": _h(f"r{i}"),
             "token_count": 10, "policy_tag": "test"} for i in range(n)]


def _mint(login, org):
    ca = login(org["admin_email"], org["admin_password"])
    r = ca.post("/v1/account/badge")
    assert r.status_code == 200, r.text
    return ca, r.json()


def test_mint_badge_requires_admin(client):
    assert client.post("/v1/account/badge").status_code in (401, 403)


def test_mint_returns_token_without_leaking_org(make_org, login):
    org = make_org()
    _, body = _mint(login, org)
    assert body.get("token")
    assert org["org_id"] not in json.dumps(body)     # no tenant identity in the response


def test_badge_svg_shows_audited_count(make_org, login, client):
    org = make_org()
    client.post("/v1/logs/batch", json=_rows(3), headers=org["auth"])
    _, body = _mint(login, org)
    r = client.get(f"/v1/badge/{body['token']}.svg")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert "Foxy Audit" in r.text
    assert "3 audited" in r.text                      # aggregate count
    assert org["org_id"] not in r.text               # never the tenant id
    assert "Pending" in r.text                        # not yet anchored


def test_badge_verified_after_anchor(make_org, login, client):
    from app.anchor import anchor_org
    org = make_org()
    client.post("/v1/logs/batch", json=_rows(2), headers=org["auth"])
    _, body = _mint(login, org)
    db = SessionLocal()
    try:
        anchor_org(db, uuid.UUID(org["org_id"]), force=True)   # stub provider → confirmed
    finally:
        db.close()
    r = client.get(f"/v1/badge/{body['token']}.svg")
    assert r.status_code == 200
    assert "Verified" in r.text


def test_unknown_badge_token_404(client):
    assert client.get("/v1/badge/nope-nope-nope.svg").status_code == 404


def test_revoke_badge_disables_it(make_org, login, client):
    org = make_org()
    ca, body = _mint(login, org)
    assert client.get(f"/v1/badge/{body['token']}.svg").status_code == 200
    assert ca.delete("/v1/account/badge").status_code == 200
    assert client.get(f"/v1/badge/{body['token']}.svg").status_code == 404
