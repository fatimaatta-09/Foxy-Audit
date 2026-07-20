"""Host-side ENFORCEMENT events (blocked / redacted) as tamper-evident evidence.

The SDK emits a prevented-egress event as a normal POST /v1/logs/batch row with
event_type="blocked"|"redacted" and content-blind enforcement labels in
event_metadata (decision, blocked_reason, policy_rules). Only hashes + labels ever
leave the host — never raw text.

These tests prove such an event:
  * ingests and is folded into the tamper-evident chain (fails /v1/verify if altered),
  * is treated as terminal & locally decided — the Gemini/judge call is SKIPPED and a
    deterministic graded verdict is written instead,
  * is counted as a prevented egress, never as a model breach,
  * surfaces its enforcement counts in the compliance passport, and
  * that a malformed judge verdict is quarantined as evaluator_unknown, so a bad
    evaluator answer can never launder itself into the audit report.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import text

from app.db import engine

_h = lambda s: hashlib.sha256(s.encode()).hexdigest()  # noqa: E731


def _blocked_event(seed: str = "1", *, event_type: str = "blocked",
                   decision: str | None = None,
                   blocked_reason: str = "pii_ssn_detected",
                   policy_rules=("pii.ssn", "block.egress"),
                   pii_signals=("ssn",)):
    """One enforcement row exactly as the SDK wire contract emits it.

    prompt_hash commits the original prompt; response_hash commits "" for a block
    (nothing was produced) or the real (redacted) response for a redaction.
    """
    blocked = event_type == "blocked"
    return {
        "prompt_hash": _h(f"prompt-{seed}"),
        "response_hash": _h("") if blocked else _h(f"redacted-response-{seed}"),
        "token_count": 0 if blocked else 12,
        "policy_tag": "chat",
        "event_type": event_type,
        "pii_signals": list(pii_signals),
        "event_metadata": {
            "decision": decision or event_type,
            "blocked_reason": blocked_reason,
            "policy_rules": list(policy_rules),
        },
    }


def test_blocked_event_ingests_and_is_chained_tamper_evident(make_org, client):
    """A blocked event ingests (202), verifies intact, and — because event_type,
    pii_signals and event_metadata are folded into the hash — fails /v1/verify the
    moment its enforcement label is altered. No migration, no chain-version bump."""
    org = make_org()

    r = client.post("/v1/logs/batch", headers=org["auth"], json=[_blocked_event()])
    assert r.status_code == 202, r.text
    receipt = r.json()["receipts"][0]
    assert receipt["status"] == "accepted"

    # Intact chain verifies.
    v = client.get("/v1/verify", headers=org["auth"]).json()
    assert v["ok"] is True and v["count"] == 1

    # Tamper with the enforcement label stored on the row; the chain must break.
    with engine.begin() as c:
        c.execute(
            text("UPDATE audit_logs "
                 "SET event_metadata = jsonb_set(event_metadata, "
                 "'{blocked_reason}', '\"nothing_was_blocked\"') "
                 "WHERE org_id = :o AND seq = 1"),
            {"o": org["org_id"]},
        )

    v2 = client.get("/v1/verify", headers=org["auth"]).json()
    assert v2["ok"] is False
    assert v2["first_broken_seq"] == 1
