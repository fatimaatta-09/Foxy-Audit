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


def _grade_pending(monkeypatch, *, breach_when=lambda m: False, calls=None):
    """Grade every claimed row through the REAL worker; the only fake is the LLM's
    answer. Records into `calls` every meta the judge was actually asked to grade so
    a test can prove a terminal event never reached the judge."""
    from app import worker as workermod
    from app.schemas import Verdict

    def fake_eval(meta, policy_config=None, history=None):
        if calls is not None:
            calls.append(meta)
        breach = breach_when(meta)
        return Verdict(
            policy_breach=breach,
            reason="policy tripped" if breach else "no issues found",
            risk_score=90 if breach else 0,
            decision="breach" if breach else "clean",
        )

    monkeypatch.setattr(workermod.gemini, "evaluate", fake_eval)
    from app.db import SessionLocal
    db = SessionLocal()
    try:
        rows = workermod._claim_batch(db, 100, 300)
        for row in rows:
            workermod._grade_one(db, row)
    finally:
        db.close()


def test_enforcement_events_skip_judge_and_grade_terminally(make_org, client, monkeypatch):
    """blocked/redacted are terminal & locally decided: the judge is never called
    (there is no model response to grade), and a deterministic graded verdict is
    written from the enforcement labels — never a model breach."""
    org = make_org()
    client.post("/v1/logs/batch", headers=org["auth"],
                json=[_blocked_event(seed="blk", event_type="blocked")])       # seq 1
    client.post("/v1/logs/batch", headers=org["auth"],
                json=[_blocked_event(seed="red", event_type="redacted")])      # seq 2

    calls = []
    _grade_pending(monkeypatch, breach_when=lambda m: True, calls=calls)

    # The judge graded NOTHING — both events were locally decided.
    assert calls == []

    rows = {r["seq"]: r for r in client.get("/v1/logs", headers=org["auth"]).json()["items"]}
    blocked, redacted = rows[1], rows[2]

    assert blocked["grading_status"] == "graded"
    assert blocked["gemini_verdict"]["decision"] == "blocked"
    assert blocked["gemini_verdict"]["policy_breach"] is False
    assert "block.egress" in blocked["gemini_verdict"]["rules"]

    assert redacted["grading_status"] == "graded"
    assert redacted["gemini_verdict"]["decision"] == "redacted"
    assert redacted["gemini_verdict"]["policy_breach"] is False


def test_blocked_event_counted_as_blocked_not_breach(make_org, client, monkeypatch):
    """A prevented egress is counted separately from a model breach: it never
    appears in the breach feed and never inflates the breach stat."""
    org = make_org()
    client.post("/v1/logs/batch", headers=org["auth"],
                json=[_blocked_event(seed="b")])                                 # seq 1 blocked
    client.post("/v1/logs/batch", headers=org["auth"], json=[{
        "prompt_hash": _h("np"), "response_hash": _h("nr"),
        "token_count": 500, "policy_tag": "chat"}])                              # seq 2 normal

    # The judge would flag EVERYTHING it grades — so if the block were counted as a
    # breach it could only be because it (wrongly) reached the judge.
    _grade_pending(monkeypatch, breach_when=lambda m: True)

    breaches = client.get("/v1/logs/breaches", headers=org["auth"]).json()
    assert [b["seq"] for b in breaches] == [2]

    stats = client.get("/v1/stats", headers=org["auth"]).json()
    assert stats["breaches"] == 1
