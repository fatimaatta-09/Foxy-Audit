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


def test_no_agent_is_backward_compatible():
    """A row with no agent must hash EXACTLY as before 6B — otherwise every
    pre-6B chain would stop verifying. agent=None ≡ agent absent ≡ the frozen
    golden vector from test_known_vector_is_stable."""
    args = dict(org_id="org-1", prompt_hash="a" * 64, response_hash="b" * 64,
                token_count=100, policy_tag="hipaa_basic", seq=1, prev_hash=GENESIS_HASH)
    golden = "872eb2c206bcb995773ab1b9a43a031c6d8488761c976ce3d341921a81aa2f79"
    assert compute_chain_hash(**args) == golden
    assert compute_chain_hash(**args, agent=None) == golden


def test_agent_is_bound_into_the_hash():
    """When an agent is present it is folded into the chain hash (tamper-evident):
    changing the agent changes the hash, and the recipe appends `|agent=<agent>`."""
    import hashlib
    args = dict(org_id="org-1", prompt_hash="a" * 64, response_hash="b" * 64,
                token_count=100, policy_tag="hipaa_basic", seq=1, prev_hash=GENESIS_HASH)
    with_agent = compute_chain_hash(**args, agent="gpt-4o")
    assert with_agent != compute_chain_hash(**args)          # agent alters the hash
    blob = "org-1|" + "a" * 64 + "|" + "b" * 64 + "|100|hipaa_basic|1|agent=gpt-4o"
    assert with_agent == hashlib.sha256((blob + GENESIS_HASH).encode()).hexdigest()


# ── golden vectors: the whole history, pinned ────────────────────────────────
# One fixed input set, hashed at every version the product has ever written. The
# values below were computed from the chain.py at origin/main BEFORE V4 was added
# — they are what customers already hold in exports, not what this file happens
# to produce today. A new version must extend the event dict, never reorder it;
# if any of these move, every historical export stops verifying and the change is
# a breaking one, whatever it looked like in the diff.

_V2_ARGS = dict(
    org_id="org-1", prompt_hash="a" * 64, response_hash="b" * 64,
    token_count=100, policy_tag="hipaa_basic", seq=1, prev_hash=GENESIS_HASH,
    agent="gpt-4o", event_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    client_id="sdk-a", client_seq=7, event_type="interaction",
    commitment_alg="hmac-sha256",
    event_metadata={"policy_snapshot_hash": "c" * 64, "request_id": "req-1"},
    pii_signals=["email"], occurred_at="2026-07-18T18:00:00+00:00",
)

GOLDEN = {
    1: "872eb2c206bcb995773ab1b9a43a031c6d8488761c976ce3d341921a81aa2f79",
    2: "f482c1b75554b3fdbe286d849906418ca5f0939365f2ed5155d542909ce31157",
    3: "b2201ad5e70f74b8a2bd266c8a25fd7b04de073ed181ee2c5ceb6ea648dc22dc",
}


def test_golden_v1_legacy_string_is_frozen():
    """The pre-V2 pipe-delimited blob, with and without the 6B agent segment."""
    args = dict(org_id="org-1", prompt_hash="a" * 64, response_hash="b" * 64,
                token_count=100, policy_tag="hipaa_basic", seq=1, prev_hash=GENESIS_HASH)
    assert compute_chain_hash(**args) == GOLDEN[1]
    assert (compute_chain_hash(**args, agent="gpt-4o")
            == "460227f579683edaaca8edb80cecb455e9c8fa523a17ce1336d8f3118535cdd8")


def test_golden_v2_capture_dict_is_frozen():
    assert compute_chain_hash(chain_version=2, **_V2_ARGS) == GOLDEN[2]


def test_golden_v3_policy_dict_is_frozen():
    assert compute_chain_hash(chain_version=3, **_V2_ARGS) == GOLDEN[3]


def test_v4_does_not_disturb_the_older_versions():
    """V4 adds a key to the event dict. Adding it must not leak into V1-V3, and
    passing a verdict_hash to an older version must be inert — otherwise a
    recompute of a historical row could be poisoned by a column added later."""
    poison = dict(_V2_ARGS, verdict_hash="d" * 64)
    assert compute_chain_hash(chain_version=2, **poison) == GOLDEN[2]
    assert compute_chain_hash(chain_version=3, **poison) == GOLDEN[3]
    assert compute_chain_hash(
        org_id="org-1", prompt_hash="a" * 64, response_hash="b" * 64,
        token_count=100, policy_tag="hipaa_basic", seq=1, prev_hash=GENESIS_HASH,
        verdict_hash="d" * 64) == GOLDEN[1]


# ── V4: the verdict is bound ─────────────────────────────────────────────────

def test_v4_binds_the_verdict_hash():
    """Same inputs, different verdict → different chain hash. This is the whole
    point of V4: a verdict can no longer be rewritten in the database without
    breaking the chain."""
    base = dict(_V2_ARGS, chain_version=4)
    clean = compute_chain_hash(**base, verdict_hash="1" * 64)
    breach = compute_chain_hash(**base, verdict_hash="2" * 64)
    assert clean != breach
    # …and V4 with a verdict is not V3 with the same fields.
    assert clean != compute_chain_hash(chain_version=3, **_V2_ARGS)


def test_verdict_hash_hex_is_canonical_and_order_blind():
    """Key order in the stored JSONB must never move the digest — Postgres does
    not promise to give a dict back in the order it went in."""
    a = {"decision": "clean", "policy_breach": False, "risk_score": 0, "rules": []}
    b = {"rules": [], "risk_score": 0, "policy_breach": False, "decision": "clean"}
    assert chain.verdict_hash_hex(a) == chain.verdict_hash_hex(b)
    assert chain.verdict_hash_hex(a) != chain.verdict_hash_hex(dict(a, decision="breach"))
    assert chain.verdict_hash_hex(None) is None
    assert len(chain.verdict_hash_hex(a)) == 64


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
