"""The tamper-evident hash chain — the single source of truth.

Hn = SHA256( data_blob_n || H_{n-1} )

Both the ingest route (which writes new rows) and the verifier (which recomputes
the whole chain) import THIS function. The field order in `data_blob` is frozen
here; any divergence between writer and verifier is the classic hash-chain bug,
so there is exactly one implementation.
"""

from __future__ import annotations

import hashlib

GENESIS_HASH = "0" * 64


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
) -> str:
    data_blob = f"{org_id}|{prompt_hash}|{response_hash}|{token_count}|{policy_tag}|{seq}"
    # `agent` (which model produced the interaction) is appended ONLY when present,
    # so rows written before 6B hash byte-for-byte identically — the whole pre-6B
    # chain keeps verifying, while agent-bearing rows are tamper-evident. (6B)
    if agent:
        data_blob += f"|agent={agent}"
    return hashlib.sha256((data_blob + prev_hash).encode("utf-8")).hexdigest()
