"""Public-chain anchoring (stub provider): anchor now, list, idempotency,
verify-surfacing + tamper cross-check, RBAC, and tenant isolation."""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import select, text

from app.db import SessionLocal, engine
from app.models import ChainAnchor


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _ingest(client, org, n):
    rows = [{"prompt_hash": _h(f"p{i}"), "response_hash": _h(f"r{i}"),
             "token_count": 100, "policy_tag": "chat"} for i in range(n)]
    assert client.post("/v1/logs/batch", json=rows, headers=org["auth"]).status_code == 202


def test_anchor_now_and_list(make_org, login, client):
    org = make_org()
    _ingest(client, org, 2)
    c = login(org["admin_email"], org["admin_password"])
    a = c.post("/v1/anchors").json()
    assert a["chain"] == "stub"
    assert a["status"] == "confirmed"
    assert a["last_seq"] == 2
    assert a["tx_hash"].startswith("0x")

    listed = c.get("/v1/anchors").json()
    assert len(listed) == 1
    assert listed[0]["last_seq"] == 2


def test_anchor_root_hash_is_the_chain_head(make_org, login, client):
    """The anchored root must equal the stored chain_hash of the highest-seq row —
    proves head_of() anchors the actual head, not some other row."""
    org = make_org()
    _ingest(client, org, 3)
    items = sorted(client.get("/v1/logs?limit=50", headers=org["auth"]).json()["items"],
                   key=lambda x: x["seq"])
    head_hash = items[-1]["chain_hash"]
    a = login(org["admin_email"], org["admin_password"]).post("/v1/anchors").json()
    assert a["root_hash"] == head_hash
    assert a["last_seq"] == 3


def test_anchor_writes_exactly_one_row(make_org, login, client):
    """Double 'anchor now' on an unchanged head must not persist a duplicate row."""
    org = make_org()
    _ingest(client, org, 2)
    c = login(org["admin_email"], org["admin_password"])
    first_id = c.post("/v1/anchors").json()["id"]
    assert c.post("/v1/anchors").status_code == 409
    db = SessionLocal()
    try:
        rows = db.execute(
            select(ChainAnchor).where(ChainAnchor.org_id == uuid.UUID(org["org_id"]))
        ).scalars().all()
        assert len(rows) == 1
        assert str(rows[0].id) == first_id
    finally:
        db.close()


def test_nothing_to_anchor_409(make_org, login):
    org = make_org()             # no logs yet
    c = login(org["admin_email"], org["admin_password"])
    assert c.post("/v1/anchors").status_code == 409


def test_anchor_idempotent_409_when_head_unchanged(make_org, login, client):
    org = make_org()
    _ingest(client, org, 2)
    c = login(org["admin_email"], org["admin_password"])
    assert c.post("/v1/anchors").status_code == 200
    assert c.post("/v1/anchors").status_code == 409


def test_anchor_advances_on_new_logs(make_org, login, client):
    org = make_org()
    _ingest(client, org, 2)
    c = login(org["admin_email"], org["admin_password"])
    assert c.post("/v1/anchors").json()["last_seq"] == 2
    _ingest(client, org, 1)      # seq advances to 3
    assert c.post("/v1/anchors").json()["last_seq"] == 3


def test_verify_surfaces_anchor_matches_true(make_org, login, client):
    org = make_org()
    _ingest(client, org, 3)
    login(org["admin_email"], org["admin_password"]).post("/v1/anchors")
    v = client.get("/v1/verify", headers=org["auth"]).json()
    assert v["last_anchor"] is not None
    assert v["last_anchor"]["matches_current_chain"] is True


def test_tamper_flips_anchor_match_false(make_org, login, client):
    org = make_org()
    _ingest(client, org, 3)
    login(org["admin_email"], org["admin_password"]).post("/v1/anchors")
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE audit_logs SET token_count = 999 WHERE org_id = :o AND seq = 1"),
            {"o": org["org_id"]},
        )
    v = client.get("/v1/verify", headers=org["auth"]).json()
    assert v["ok"] is False
    assert v["last_anchor"]["matches_current_chain"] is False


def test_tamper_after_anchor_point_keeps_anchor_match_true(make_org, login, client):
    """Tampering a row AFTER the anchored seq must break /v1/verify but leave the
    anchor's matches_current_chain TRUE — proves recompute_head bounds at last_seq
    (the anchor of rows 1..3 is unaffected by a later row's tampering)."""
    org = make_org()
    _ingest(client, org, 3)
    login(org["admin_email"], org["admin_password"]).post("/v1/anchors")   # anchors seq 1..3
    _ingest(client, org, 1)                                                 # seq 4, not yet anchored
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE audit_logs SET token_count = 999 WHERE org_id = :o AND seq = 4"),
            {"o": org["org_id"]},
        )
    v = client.get("/v1/verify", headers=org["auth"]).json()
    assert v["ok"] is False
    assert v["first_broken_seq"] == 4
    assert v["last_anchor"]["last_seq"] == 3
    assert v["last_anchor"]["matches_current_chain"] is True


def test_member_cannot_anchor(make_org, add_user, login, client):
    org = make_org()
    _ingest(client, org, 1)
    add_user(org["org_id"], "m@test.dev", "memberpass1", role="member")
    c = login("m@test.dev", "memberpass1")
    assert c.post("/v1/anchors").status_code == 403


def test_anchors_isolated_between_orgs(make_org, login, client):
    a = make_org()
    b = make_org()
    _ingest(client, a, 2)
    login(a["admin_email"], a["admin_password"]).post("/v1/anchors")
    cb = login(b["admin_email"], b["admin_password"])
    assert cb.get("/v1/anchors").json() == []
