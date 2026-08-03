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


def _backend_chain():
    """Load backend/app/chain.py by path, or skip. It imports only hashlib+json,
    so this needs no FastAPI, no SQLAlchemy and no database."""
    import importlib.util

    import pytest
    path = os.path.join(os.path.dirname(__file__), "..", "backend", "app", "chain.py")
    if not os.path.isfile(path):
        pytest.skip("backend tree not alongside the verifier")
    spec = importlib.util.spec_from_file_location("backend_chain", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_PARITY_ARGS = dict(
    org_id=ORG, prompt_hash="a" * 64, response_hash="b" * 64, token_count=100,
    policy_tag="hipaa_basic", seq=1, prev_hash=fv.GENESIS_HASH, agent="gpt-4o",
    event_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", client_id="sdk-a",
    client_seq=7, event_type="interaction", commitment_alg="hmac-sha256",
    event_metadata={"policy_snapshot_hash": "c" * 64, "request_id": "req-1"},
    pii_signals=["email"], occurred_at="2026-07-18T18:00:00+00:00",
)


def test_writer_and_verifier_agree_at_every_version():
    """The chain has two implementations by design — this one, and the backend's.
    They drift silently, and when they do a genuine export fails to verify at a
    customer's desk with no test having said anything. So: identical inputs into
    both, at every version the product has ever written, including V4 with and
    without a verdict."""
    bc = _backend_chain()
    for version in (1, 2, 3, 4):
        for verdict_hash in (None, "d" * 64):
            args = dict(_PARITY_ARGS, chain_version=version, verdict_hash=verdict_hash)
            assert fv.compute_chain_hash(**args) == bc.compute_chain_hash(**args), (
                f"writer/verifier disagree at chain_version {version} "
                f"(verdict_hash={'set' if verdict_hash else 'None'})")


def test_the_two_verdict_digests_agree():
    """`verdict_hash` is only meaningful if both sides derive it the same way."""
    bc = _backend_chain()
    verdict = {"policy_breach": False, "reason": "checks passed", "risk_score": 0,
               "decision": "clean", "rules": [], "judge_provider": None,
               "judge_model": None}
    assert fv.verdict_hash_hex(verdict) == bc.verdict_hash_hex(verdict)


# ── V4: the verdict is inside the chain ──────────────────────────────────────

_V4_VERDICT = {"policy_breach": False, "reason": "deterministic metadata checks passed",
               "risk_score": 0, "decision": "clean", "rules": []}


def _v4_export(verdict=None):
    verdict = _V4_VERDICT if verdict is None else verdict
    row = {
        "seq": 1, "prev_hash": fv.GENESIS_HASH,
        "prompt_hash": "a" * 64, "response_hash": "b" * 64,
        "token_count": 10, "policy_tag": "chat", "agent": None,
        "event_id": None, "client_id": None, "client_seq": None,
        "event_type": "interaction", "commitment_alg": "hmac-sha256",
        "event_metadata": None, "pii_signals": None, "occurred_at": None,
        "chain_version": 4,
        "local_verdict": verdict, "verdict_hash": fv.verdict_hash_hex(verdict),
    }
    row["chain_hash"] = fv.compute_chain_hash(
        org_id=ORG, prev_hash=fv.GENESIS_HASH, **{k: row[k] for k in (
            "prompt_hash", "response_hash", "token_count", "policy_tag", "seq",
            "agent", "event_id", "client_id", "client_seq", "event_type",
            "commitment_alg", "event_metadata", "pii_signals", "occurred_at",
            "chain_version", "verdict_hash")})
    return {"org_id": ORG, "count": 1, "logs": [row]}


def test_v4_export_verifies():
    assert fv.verify_export(_v4_export())["ok"] is True


def test_v4_catches_a_swapped_verdict_hash():
    """Editing the bound digest breaks the chain hash itself."""
    export = _v4_export()
    export["logs"][0]["verdict_hash"] = "d" * 64
    res = fv.verify_export(export)
    assert res["ok"] is False
    assert res["first_broken_seq"] == 1


def test_v4_catches_a_rewritten_verdict_body():
    """The chain binds the DIGEST, so rewriting the verdict alone leaves the chain
    hash valid. Re-deriving the digest from the exported body is what catches it —
    without that check this tampering would pass, and V4 would be decorative."""
    export = _v4_export()
    export["logs"][0]["local_verdict"] = dict(_V4_VERDICT, decision="breach",
                                              policy_breach=True)
    res = fv.verify_export(export)
    assert res["ok"] is False
    assert res["first_broken_seq"] == 1
    assert "verdict" in res["detail"]


def test_a_v3_row_is_unaffected_by_the_verdict_columns():
    """A pre-V4 row carries no verdict, and must verify exactly as it always did
    even sitting in an export whose schema now has the columns."""
    row = {"seq": 1, "prev_hash": fv.GENESIS_HASH, "prompt_hash": "a" * 64,
           "response_hash": "b" * 64, "token_count": 10, "policy_tag": "chat",
           "event_id": None, "client_id": None, "client_seq": None,
           "event_type": "interaction", "commitment_alg": "sha256-legacy",
           "event_metadata": None, "pii_signals": None, "occurred_at": None,
           "chain_version": 3, "local_verdict": None, "verdict_hash": None}
    row["chain_hash"] = fv.compute_chain_hash(
        org_id=ORG, prev_hash=fv.GENESIS_HASH, **{k: row[k] for k in (
            "prompt_hash", "response_hash", "token_count", "policy_tag", "seq",
            "event_id", "client_id", "client_seq", "event_type", "commitment_alg",
            "event_metadata", "pii_signals", "occurred_at", "chain_version")})
    assert fv.verify_export({"org_id": ORG, "count": 1, "logs": [row]})["ok"] is True


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
