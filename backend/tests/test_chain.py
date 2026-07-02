"""Regression tests for the tamper-evident hash chain (backend/app/chain.py).

The chain hash is the product's core trust claim: if the formula or field order
in ``compute_chain_hash`` ever changes silently, every previously-stored chain
stops verifying. These tests pin the formula and prove tamper-evidence WITHOUT
needing Postgres — ``chain.py`` only depends on ``hashlib``, so we load it
directly by path and CI needs nothing but pytest.
"""

from __future__ import annotations

import importlib.util
import os

# Load app/chain.py directly, bypassing the `app` package __init__ (which pulls
# in FastAPI/SQLAlchemy) so this test has zero backend dependencies.
_CHAIN_PATH = os.path.join(os.path.dirname(__file__), "..", "app", "chain.py")
_spec = importlib.util.spec_from_file_location("foxy_chain", _CHAIN_PATH)
chain = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(chain)

GENESIS_HASH = chain.GENESIS_HASH
compute_chain_hash = chain.compute_chain_hash

# A fixed multi-row payload set (metadata only — never raw text).
ROWS = [
    dict(org_id="org-1", prompt_hash="a" * 64, response_hash="b" * 64,
         token_count=100, policy_tag="hipaa_basic", seq=1),
    dict(org_id="org-1", prompt_hash="c" * 64, response_hash="d" * 64,
         token_count=250, policy_tag="soc2", seq=2),
    dict(org_id="org-1", prompt_hash="e" * 64, response_hash="f" * 64,
         token_count=40, policy_tag="default", seq=3),
]


def _build_chain(rows):
    prev = GENESIS_HASH
    out = []
    for r in rows:
        h = compute_chain_hash(prev_hash=prev, **r)
        out.append(h)
        prev = h
    return out


def test_known_vector_is_stable():
    """Golden pin: this exact hash encodes the frozen field order + separator.
    If it changes, the hashing formula changed and old chains would fail to
    verify — the change must be deliberate (and this vector updated on purpose)."""
    h = compute_chain_hash(
        org_id="org-1", prompt_hash="a" * 64, response_hash="b" * 64,
        token_count=100, policy_tag="hipaa_basic", seq=1, prev_hash=GENESIS_HASH,
    )
    assert h == "872eb2c206bcb995773ab1b9a43a031c6d8488761c976ce3d341921a81aa2f79"


def test_chain_recomputes_intact():
    """An untampered chain re-verifies exactly like the /v1/verify route does."""
    stored = _build_chain(ROWS)
    prev = GENESIS_HASH
    for row, expect in zip(ROWS, stored):
        assert compute_chain_hash(prev_hash=prev, **row) == expect
        prev = expect


def test_tamper_is_detected_at_that_seq():
    """Editing any row's metadata breaks its hash (and, via the previous-hash
    dependency, every row after it) — the avalanche effect verify relies on."""
    stored = _build_chain(ROWS)

    tampered = dict(ROWS[1])
    tampered["token_count"] = 999                      # edit seq 2 after the fact
    prev_ok = stored[0]                                # hash of seq 1 (still valid)
    recomputed_seq2 = compute_chain_hash(prev_hash=prev_ok, **tampered)

    assert recomputed_seq2 != stored[1]                # verify would flag seq 2

    # seq 3's stored hash was chained off the ORIGINAL seq 2 hash, so recomputing
    # it from the tampered seq 2 also diverges (tampering cascades forward).
    recomputed_seq3 = compute_chain_hash(prev_hash=recomputed_seq2, **ROWS[2])
    assert recomputed_seq3 != stored[2]
