"""P2 §8.5 — the export is readable now, and that must not cost verifiability.

`GET /v1/logs/export?format=json` used to serialise the whole ledger with a bare
`json.dumps`, which puts every row on ONE line. Valid JSON, and useless to the
auditor who opens it — the owner's "the JSON export emits a single line of
info". It now ships `indent=2`.

Changing the bytes of an evidence file deserves a test rather than an argument.
`verify_export` is a pure function of the PARSED object, so the invariant is:
the same payload serialised either way must parse to the same object and produce
the same verdict. This file pins that, and pins that the check can still fail —
a verifier that returns ok for everything would satisfy the first half on its
own.

Needs no database and no server, which is the point: the backend integration
suite needs Postgres, and this is the part that concerns the trust claim.
"""

from __future__ import annotations

import json
import uuid

import pytest

from foxy_verify import GENESIS_HASH, compute_chain_hash, verify_export


def _chain(rows: int = 3) -> dict:
    """A small, genuinely valid export — hashes from the verifier's own recipe."""
    org = uuid.uuid4()
    logs, prev = [], GENESIS_HASH
    for seq in range(1, rows + 1):
        prompt_hash, response_hash = "p" * 64, "r" * 64
        chain_hash = compute_chain_hash(
            org_id=org, prompt_hash=prompt_hash, response_hash=response_hash,
            token_count=100 * seq, policy_tag="hipaa", seq=seq,
            prev_hash=prev, agent="agent-a")
        logs.append({"seq": seq, "prompt_hash": prompt_hash,
                     "response_hash": response_hash, "token_count": 100 * seq,
                     "policy_tag": "hipaa", "agent": "agent-a",
                     "prev_hash": prev, "chain_hash": chain_hash,
                     "chain_version": 1})
        prev = chain_hash
    return {"org_id": str(org), "count": len(logs), "anchor": None, "logs": logs}


@pytest.fixture(scope="module")
def payload() -> dict:
    return _chain()


def test_the_fixture_chain_actually_verifies(payload):
    """Pinned first. If the fixture did not verify, every assertion below would
    be comparing two identical failures and would pass while proving nothing."""
    result = verify_export(payload)
    assert result["ok"] is True, result
    assert result["count"] == 3


def test_indenting_the_export_cannot_change_the_verdict(payload):
    one_line = json.dumps(payload, default=str)
    indented = json.dumps(payload, default=str, indent=2)

    assert one_line.count("\n") == 0, "the unindented form is the one-line case"
    assert indented.count("\n") > 10, "indent=2 should actually break lines"

    assert json.loads(one_line) == json.loads(indented), \
        "the two serialisations do not parse to the same object"
    assert verify_export(json.loads(one_line)) == verify_export(json.loads(indented)), \
        "formatting moved the verdict — the export format is not free after all"


def test_the_verifier_still_catches_a_tampered_row(payload):
    """The other half. A verifier that returned ok unconditionally would satisfy
    the equality above, so the failing case is pinned too."""
    tampered = json.loads(json.dumps(payload, default=str, indent=2))
    tampered["logs"][1]["token_count"] += 1

    result = verify_export(tampered)
    assert result["ok"] is False
    assert result["first_broken_seq"] == 2, result


def test_a_sequence_gap_is_caught_whatever_the_formatting(payload):
    for indent in (None, 2):
        gapped = json.loads(json.dumps(payload, default=str, indent=indent))
        del gapped["logs"][1]
        result = verify_export(gapped)
        assert result["ok"] is False, f"indent={indent} let a gap through"
        assert "gap" in result["detail"]
