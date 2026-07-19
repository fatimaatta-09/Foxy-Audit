"""Tests for the standalone Foxy Audit verifier (Phase 6 · 6D).

Pure Python, stdlib only — no backend, no Postgres, no network. Runnable with
just `pytest verifier/`. The `test_matches_backend_recipe` case cross-checks the
verifier's independent hash recipe against the backend's real chain.py, proving
byte-for-byte parity (skipped if the backend tree isn't alongside)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import foxy_verify as fv  # noqa: E402

ORG = "11111111-2222-3333-4444-555555555555"


def _make_export(specs, anchor_at=None):
    """Build a VALID export from row specs, using the verifier's own recipe so the
    chain is self-consistent. Each spec: prompt_hash/response_hash/token_count/
    policy_tag/(agent)."""
    rows, prev = [], fv.GENESIS_HASH
    for i, s in enumerate(specs, start=1):
        ch = fv.compute_chain_hash(
            org_id=ORG, prompt_hash=s["prompt_hash"], response_hash=s["response_hash"],
            token_count=s["token_count"], policy_tag=s["policy_tag"], seq=i,
            prev_hash=prev, agent=s.get("agent"))
        rows.append({"seq": i, "prev_hash": prev, "chain_hash": ch, **s})
        prev = ch
    export = {"org_id": ORG, "count": len(rows), "logs": rows}
    if anchor_at is not None:
        export["anchor"] = {
            "chain": "stub", "status": "confirmed",
            "root_hash": rows[anchor_at - 1]["chain_hash"], "last_seq": anchor_at,
            "tx_hash": "0xabc", "block_number": 1, "anchored_at": None, "contract": None}
    return export


_SPECS = [
    {"prompt_hash": "a" * 64, "response_hash": "b" * 64, "token_count": 10, "policy_tag": "chat"},
    {"prompt_hash": "c" * 64, "response_hash": "d" * 64, "token_count": 25, "policy_tag": "hipaa_basic",
     "agent": "gpt-4o"},
    {"prompt_hash": "e" * 64, "response_hash": "f" * 64, "token_count": 40, "policy_tag": "soc2"},
]


def test_intact_export_verifies():
    res = fv.verify_export(_make_export(_SPECS))
    assert res["ok"] is True
    assert res["count"] == 3
    assert res["first_broken_seq"] is None


def test_tampered_row_caught_at_that_seq():
    export = _make_export(_SPECS)
    export["logs"][1]["token_count"] = 999          # edit seq 2 after the fact
    res = fv.verify_export(export)
    assert res["ok"] is False
    assert res["first_broken_seq"] == 2


def test_tampered_agent_caught():
    export = _make_export(_SPECS)
    export["logs"][1]["agent"] = "claude-3-opus"     # seq 2's agent was bound into the hash
    res = fv.verify_export(export)
    assert res["ok"] is False
    assert res["first_broken_seq"] == 2


def test_offline_anchor_receipt_matches():
    export = _make_export(_SPECS, anchor_at=3)
    res = fv.verify_export(export)
    anc = fv.check_anchor_offline(export, res)
    assert anc is not None
    assert anc["matches"] is True
    assert anc["last_seq"] == 3


def test_offline_anchor_receipt_detects_forged_root():
    export = _make_export(_SPECS, anchor_at=3)
    export["anchor"]["root_hash"] = "0" * 64          # receipt doesn't match the chain
    anc = fv.check_anchor_offline(export, fv.verify_export(export))
    assert anc["matches"] is False


def test_matches_backend_recipe():
    """The verifier's independent recipe must equal the backend's chain.py exactly
    (incl. the 6B agent rule) — else a real export would falsely fail."""
    import importlib.util
    backend_chain = os.path.join(os.path.dirname(__file__), "..", "backend", "app", "chain.py")
    if not os.path.isfile(backend_chain):
        import pytest
        pytest.skip("backend tree not alongside the verifier")
    spec = importlib.util.spec_from_file_location("backend_chain", backend_chain)
    bc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bc)
    common = dict(org_id=ORG, prompt_hash="a" * 64, response_hash="b" * 64,
                  token_count=7, policy_tag="chat", seq=1, prev_hash=fv.GENESIS_HASH)
    assert fv.compute_chain_hash(**common) == bc.compute_chain_hash(**common)
    assert (fv.compute_chain_hash(**common, agent="gpt-4o")
            == bc.compute_chain_hash(**common, agent="gpt-4o"))
    assert fv.GENESIS_HASH == bc.GENESIS_HASH


def test_capture_v2_export_includes_all_chain_fields():
    fields = {
        "event_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "client_id": "sdk-a", "client_seq": 1,
        "event_type": "interaction", "commitment_alg": "hmac-sha256",
        "event_metadata": {"request_id": "req-1"},
        "pii_signals": ["email"], "occurred_at": "2026-07-18T18:00:00+00:00",
        "chain_version": 2,
    }
    row = {"seq": 1, "prev_hash": fv.GENESIS_HASH,
           "prompt_hash": "a" * 64, "response_hash": "b" * 64,
           "token_count": 10, "policy_tag": "chat", **fields}
    row["chain_hash"] = fv.compute_chain_hash(
        org_id=ORG, prev_hash=fv.GENESIS_HASH, **{k: row[k] for k in (
            "prompt_hash", "response_hash", "token_count", "policy_tag", "seq",
            "event_id", "client_id", "client_seq", "event_type", "commitment_alg",
            "event_metadata", "pii_signals", "occurred_at", "chain_version")})
    assert fv.verify_export({"org_id": ORG, "count": 1, "logs": [row]})["ok"] is True


def test_policy_v3_export_binds_the_chain_version_and_snapshot_metadata():
    fields = {
        "event_id": None, "client_id": None, "client_seq": None,
        "event_type": "interaction", "commitment_alg": "sha256-legacy",
        "event_metadata": {
            "policy_snapshot": {
                "schema": "foxy-policy-v1", "pii_detection": True,
                "prompt_injection": True, "regulated_data_mode": False,
                "max_token_threshold": 50000,
            },
            "policy_snapshot_hash": "c" * 64,
        },
        "pii_signals": None, "occurred_at": None, "chain_version": 3,
    }
    row = {"seq": 1, "prev_hash": fv.GENESIS_HASH,
           "prompt_hash": "a" * 64, "response_hash": "b" * 64,
           "token_count": 10, "policy_tag": "chat", **fields}
    row["chain_hash"] = fv.compute_chain_hash(
        org_id=ORG, prev_hash=fv.GENESIS_HASH, **{key: row[key] for key in (
            "prompt_hash", "response_hash", "token_count", "policy_tag", "seq",
            "event_id", "client_id", "client_seq", "event_type", "commitment_alg",
            "event_metadata", "pii_signals", "occurred_at", "chain_version")})
    assert fv.verify_export({"org_id": ORG, "count": 1, "logs": [row]})["ok"] is True

    row["chain_version"] = 2
    assert fv.verify_export({"org_id": ORG, "count": 1, "logs": [row]})["ok"] is False


def test_customer_key_verifies_known_event_sidecar():
    key = "customer-secret"
    event_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    row = {
        "seq": 1, "prev_hash": fv.GENESIS_HASH, "event_id": event_id,
        "client_id": "sdk-a", "client_seq": 1, "event_type": "interaction",
        "commitment_alg": "hmac-sha256", "event_metadata": None,
        "pii_signals": None, "occurred_at": None, "chain_version": 2,
        "prompt_hash": fv.commitment_hex("prompt", key),
        "response_hash": fv.commitment_hex("response", key),
        "token_count": 2, "policy_tag": "chat",
    }
    row["chain_hash"] = fv.compute_chain_hash(
        org_id=ORG, prev_hash=fv.GENESIS_HASH, **{k: row[k] for k in (
            "prompt_hash", "response_hash", "token_count", "policy_tag", "seq",
            "event_id", "client_id", "client_seq", "event_type", "commitment_alg",
            "event_metadata", "pii_signals", "occurred_at", "chain_version")})
    data = {"org_id": ORG, "logs": [row]}
    assert fv.verify_known_events(data, {event_id: {"prompt": "prompt", "response": "response"}}, key) == {
        "ok": True, "checked": 1}
