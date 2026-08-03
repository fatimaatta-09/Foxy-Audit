"""The tamper-evident hash chain — the single source of truth.

Hn = SHA256( data_blob_n || H_{n-1} )

Both the ingest route (which writes new rows) and the verifier (which recomputes
the whole chain) import THIS function. The field order in `data_blob` is frozen
here; any divergence between writer and verifier is the classic hash-chain bug,
so there is exactly one implementation.
"""

from __future__ import annotations

import hashlib
import json

GENESIS_HASH = "0" * 64
CHAIN_VERSION_LEGACY = 1
CHAIN_VERSION_CAPTURE_V2 = 2
CHAIN_VERSION_POLICY_V3 = 3
CHAIN_VERSION_VERDICT_V4 = 4


def verdict_hash_hex(verdict: dict | None) -> str | None:
    """The digest V4 binds: SHA-256 over the canonical JSON of a LOCAL verdict.

    The same canonical form the chain itself uses, so key order can never move a
    hash. Returns None for None, which is what an un-evaluated row carries.

    Only the DIGEST goes into the chain, so the chain alone cannot notice an
    edited verdict body — a verifier has to re-derive this from the exported
    `local_verdict` and compare. verifier/foxy_verify.py does exactly that.
    """
    if verdict is None:
        return None
    canonical = json.dumps(verdict, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_chain_hash(
    *,
    org_id,
    prompt_hash: str,
    response_hash: str,
    token_count: int,
    policy_tag: str,
    seq: int,
    prev_hash: str,
    agent: str | None = None,
    chain_version: int = CHAIN_VERSION_LEGACY,
    event_id=None,
    client_id: str | None = None,
    client_seq: int | None = None,
    event_type: str | None = None,
    commitment_alg: str | None = None,
    event_metadata: dict | None = None,
    pii_signals: list[str] | None = None,
    occurred_at=None,
    verdict_hash: str | None = None,
) -> str:
    if chain_version >= CHAIN_VERSION_CAPTURE_V2:
        event = {
            "org_id": str(org_id), "event_id": str(event_id) if event_id else None,
            "client_id": client_id, "client_seq": client_seq,
            "event_type": event_type or "interaction",
            "commitment_alg": commitment_alg or "sha256-legacy",
            "prompt_hash": prompt_hash, "response_hash": response_hash,
            "token_count": token_count, "policy_tag": policy_tag, "agent": agent,
            "pii_signals": pii_signals, "event_metadata": event_metadata,
            "occurred_at": occurred_at.isoformat() if hasattr(occurred_at, "isoformat") else occurred_at,
            "seq": seq,
        }
        # V3 binds the declared chain format too. Earlier V2 rows remain exactly
        # byte-compatible so their historic hashes continue to verify.
        if chain_version >= CHAIN_VERSION_POLICY_V3:
            event["chain_version"] = chain_version
        # V4 binds the LOCAL, deterministic verdict — the one policy_engine
        # decided at ingest, so a verdict can no longer be edited in the database
        # without breaking the chain. NOT the AI judge's grade: that does not
        # exist yet when this runs (the worker adds it asynchronously), so
        # chaining it would mean re-hashing the row afterwards and invalidating
        # every block after it. See schemas.Verdict for the full line.
        # V1/V2/V3 stay byte-identical, exactly as V3 did for V2.
        if chain_version >= CHAIN_VERSION_VERDICT_V4:
            event["verdict_hash"] = verdict_hash
        data_blob = json.dumps(event, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256((data_blob + prev_hash).encode("utf-8")).hexdigest()
    data_blob = f"{org_id}|{prompt_hash}|{response_hash}|{token_count}|{policy_tag}|{seq}"
    # `agent` (which model produced the interaction) is appended ONLY when present,
    # so rows written before 6B hash byte-for-byte identically — the whole pre-6B
    # chain keeps verifying, while agent-bearing rows are tamper-evident. (6B)
    if agent:
        data_blob += f"|agent={agent}"
    return hashlib.sha256((data_blob + prev_hash).encode("utf-8")).hexdigest()
