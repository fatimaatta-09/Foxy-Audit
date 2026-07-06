"""Per-tier anchor-frequency SLA (Phase 6 · 6E).

The automatic worker sweep anchors an org only if its head advanced AND the last
anchor is older than the org's plan-tier cadence. Manual 'anchor now' is NOT
cadence-gated (that path is covered by test_anchors.py). Cadences are env-overridable."""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import text

from app.anchor import anchor_all_due, anchor_org
from app.config import get_settings
from app.db import SessionLocal, engine


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _ingest(client, org, n):
    rows = [{"prompt_hash": _h(f"p{i}"), "response_hash": _h(f"r{i}"),
             "token_count": 100, "policy_tag": "chat"} for i in range(n)]
    assert client.post("/v1/logs/batch", json=rows, headers=org["auth"]).status_code == 202


def _set_tier(org_id, tier):
    with engine.begin() as c:
        c.execute(text("UPDATE organizations SET plan_tier=:t WHERE id=:o"),
                  {"t": tier, "o": org_id})


def _backdate_anchor(org_id, interval):
    with engine.begin() as c:
        c.execute(text(f"UPDATE chain_anchors SET anchored_at = now() - interval '{interval}'"
                       " WHERE org_id=:o"), {"o": org_id})


def _first_anchor(oid):
    db = SessionLocal()
    try:
        anchor_org(db, oid, force=True)
    finally:
        db.close()


def test_anchor_cadence_for_maps_tiers():
    s = get_settings()
    assert s.anchor_cadence_for("free") == s.anchor_cadence_free
    assert s.anchor_cadence_for("pro") == s.anchor_cadence_pro
    assert s.anchor_cadence_for("enterprise") == s.anchor_cadence_enterprise
    assert s.anchor_cadence_for(None) == s.anchor_cadence_default
    assert s.anchor_cadence_for("mystery-tier") == s.anchor_cadence_default


def test_worker_skips_within_cadence(make_org, client, monkeypatch):
    org = make_org(); _set_tier(org["org_id"], "free")
    _ingest(client, org, 2)
    oid = uuid.UUID(org["org_id"])
    _first_anchor(oid)                              # anchor @ seq 2
    _ingest(client, org, 1)                         # head advances to 3
    _backdate_anchor(org["org_id"], "2 hours")      # last anchor 2h ago

    s = get_settings()
    monkeypatch.setattr(s, "anchor_cadence_free", 86400)     # 24h
    db = SessionLocal()
    try:
        assert anchor_org(db, oid, s, respect_cadence=True) is None   # 2h < 24h → skip
    finally:
        db.close()


def test_worker_anchors_past_cadence(make_org, client, monkeypatch):
    org = make_org(); _set_tier(org["org_id"], "enterprise")
    _ingest(client, org, 2)
    oid = uuid.UUID(org["org_id"])
    _first_anchor(oid)
    _ingest(client, org, 1)
    _backdate_anchor(org["org_id"], "2 hours")

    s = get_settings()
    monkeypatch.setattr(s, "anchor_cadence_enterprise", 3600)   # 1h
    db = SessionLocal()
    try:
        row = anchor_org(db, oid, s, respect_cadence=True)      # 2h > 1h → anchor
        assert row is not None and row.last_seq == 3
    finally:
        db.close()


def test_anchor_all_due_respects_cadence(make_org, client, monkeypatch):
    org = make_org(); _set_tier(org["org_id"], "free")
    _ingest(client, org, 2)
    oid = uuid.UUID(org["org_id"])
    _first_anchor(oid)
    _ingest(client, org, 1)
    _backdate_anchor(org["org_id"], "2 hours")

    s = get_settings()
    monkeypatch.setattr(s, "anchor_cadence_free", 86400)
    db = SessionLocal()
    try:
        assert anchor_all_due(db, s) == 0            # within cadence → nothing anchored
    finally:
        db.close()


def test_anchor_sla_endpoint_reflects_tier(make_org, login, client):
    org = make_org(); _set_tier(org["org_id"], "pro")
    c = login(org["admin_email"], org["admin_password"])
    sla = c.get("/v1/anchors/sla").json()
    assert sla["plan_tier"] == "pro"
    assert sla["cadence_seconds"] == get_settings().anchor_cadence_pro
    assert "hour" in sla["cadence_human"]
