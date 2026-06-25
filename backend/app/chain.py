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
) -> str:
    data_blob = f"{org_id}|{prompt_hash}|{response_hash}|{token_count}|{policy_tag}|{seq}"
    return hashlib.sha256((data_blob + prev_hash).encode("utf-8")).hexdigest()
