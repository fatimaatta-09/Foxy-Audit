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
import sys

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

    def fake_eval(meta, policy_config=None, history=None, api_key=None, model=None):
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


def test_blocked_event_counted_as_blocked_not_breach(make_org, client, monkeypatch,
                                                     configure_judge):
    """A prevented egress is counted separately from a model breach: it never
    appears in the breach feed and never inflates the breach stat."""
    org = make_org()
    configure_judge(org["org_id"])          # a judge key, so the non-terminal row is graded
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


def _grade_with_verdict(monkeypatch, verdict):
    """Grade all pending rows with a fixed (possibly malformed) judge verdict."""
    from app import worker as workermod
    monkeypatch.setattr(workermod.gemini, "evaluate",
                        lambda meta, policy_config=None, history=None, api_key=None, model=None: verdict)
    from app.db import SessionLocal
    db = SessionLocal()
    try:
        for row in workermod._claim_batch(db, 100, 300):
            workermod._grade_one(db, row)
    finally:
        db.close()


def _one_normal_event(client, org, seed="j"):
    """One ordinary (non-terminal) row. Its org must have a judge key configured
    (fixture `configure_judge`) or the per-tenant router skips the judge entirely."""
    assert client.post("/v1/logs/batch", headers=org["auth"], json=[{
        "prompt_hash": _h(f"p-{seed}"), "response_hash": _h(f"r-{seed}"),
        "token_count": 10, "policy_tag": "chat"}]).status_code == 202


def test_contradictory_judge_verdict_is_quarantined(make_org, client, monkeypatch,
                                                    configure_judge):
    """A judge answer that flags a breach yet decides "clean" is self-contradictory.
    It must NOT be trusted as a breach OR laundered into a clean pass — it is
    quarantined as evaluator_unknown so the audit report stays honest."""
    from app.schemas import Verdict
    org = make_org()
    configure_judge(org["org_id"])
    _one_normal_event(client, org)

    bad = Verdict(policy_breach=True, reason="all good, nothing to see",
                  risk_score=80, decision="clean")
    _grade_with_verdict(monkeypatch, bad)

    v = client.get("/v1/logs", headers=org["auth"]).json()["items"][0]["gemini_verdict"]
    assert v["decision"] == "unknown"
    assert v["policy_breach"] is False
    assert v["reason"].startswith("evaluator_unknown")

    stats = client.get("/v1/stats", headers=org["auth"]).json()
    assert stats["breaches"] == 0
    assert stats["evaluator_unknown"] == 1


def test_empty_reason_judge_verdict_is_quarantined(make_org, client, monkeypatch,
                                                   configure_judge):
    """An affirmative breach claim with no usable reason is low-confidence noise,
    not audit evidence — quarantine it as evaluator_unknown."""
    from app.schemas import Verdict
    org = make_org()
    configure_judge(org["org_id"])
    _one_normal_event(client, org)

    bad = Verdict(policy_breach=True, reason="   ", risk_score=70, decision="breach")
    _grade_with_verdict(monkeypatch, bad)

    v = client.get("/v1/logs", headers=org["auth"]).json()["items"][0]["gemini_verdict"]
    assert v["decision"] == "unknown"
    assert v["reason"].startswith("evaluator_unknown")
    assert client.get("/v1/stats", headers=org["auth"]).json()["breaches"] == 0


def test_valid_judge_verdicts_pass_validation(make_org, client, monkeypatch,
                                              configure_judge):
    """A clean, self-consistent judge verdict is persisted unchanged — validation
    only quarantines the malformed ones, it does not swallow honest grades."""
    from app.schemas import Verdict
    org = make_org()
    configure_judge(org["org_id"])
    _one_normal_event(client, org)

    good = Verdict(policy_breach=True, reason="pii exfiltration risk detected",
                   risk_score=88, decision="breach", rules=["pii.exfil"])
    _grade_with_verdict(monkeypatch, good)

    v = client.get("/v1/logs", headers=org["auth"]).json()["items"][0]["gemini_verdict"]
    assert v["decision"] == "breach"
    assert v["policy_breach"] is True
    assert client.get("/v1/stats", headers=org["auth"]).json()["breaches"] == 1


def _grade_each(monkeypatch, verdict_for):
    """Grade all pending rows; the judge answer for a non-terminal row comes from
    verdict_for(meta). Terminal enforcement rows never reach verdict_for."""
    from app import worker as workermod
    monkeypatch.setattr(
        workermod.gemini, "evaluate",
        lambda meta, policy_config=None, history=None, api_key=None, model=None: verdict_for(meta))
    from app.db import SessionLocal
    db = SessionLocal()
    try:
        for row in workermod._claim_batch(db, 100, 300):
            workermod._grade_one(db, row)
    finally:
        db.close()


def test_passport_shows_host_side_enforcement_counts(make_org, client, monkeypatch,
                                                     configure_judge):
    """The compliance passport carries an honest Host-Side Enforcement section:
    allowed / blocked / redacted counts, the policies actually enforced, and
    evaluator-unknown as its own honest non-pass state — never folded into a pass."""
    from app.schemas import Verdict
    # /v1/passport returns a PDF and nothing else — the HTML degrade this used to
    # read from was removed. Capture the document from the renderer's input.
    _captured: dict = {}

    class _FakeHTML:
        def __init__(self, string=None, **kw):
            _captured["html"] = string

        def write_pdf(self):
            return b"%PDF-1.7\nstub\n%%EOF"

    _fake = type(sys)("weasyprint")
    _fake.HTML = _FakeHTML
    monkeypatch.setitem(sys.modules, "weasyprint", _fake)
    org = make_org()
    configure_judge(org["org_id"])

    client.post("/v1/logs/batch", headers=org["auth"],
                json=[_blocked_event(seed="p1", event_type="blocked")])         # seq 1
    client.post("/v1/logs/batch", headers=org["auth"],
                json=[_blocked_event(seed="p2", event_type="redacted")])        # seq 2
    client.post("/v1/logs/batch", headers=org["auth"], json=[{
        "prompt_hash": _h("ok"), "response_hash": _h("okr"),
        "token_count": 10, "policy_tag": "chat"}])                              # seq 3 clean
    client.post("/v1/logs/batch", headers=org["auth"], json=[{
        "prompt_hash": _h("uk"), "response_hash": _h("ukr"),
        "token_count": 10, "policy_tag": "murky"}])                            # seq 4 -> unknown

    def verdict_for(meta):
        if meta["policy_tag"] == "murky":
            # self-contradictory -> quarantined to evaluator_unknown by the worker
            return Verdict(policy_breach=True, reason="unsure", risk_score=40, decision="clean")
        return Verdict(policy_breach=False, reason="no issues found", risk_score=0, decision="clean")

    _grade_each(monkeypatch, verdict_for)

    r = client.post("/v1/passport", headers=org["auth"])
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    body = _captured["html"]

    assert "Host-Side Enforcement" in body
    assert "prevented from leaving the host" in body
    # Honest per-state counts (blocked/redacted/allowed/evaluator-unknown).
    assert "Prompts Blocked (prevented egress)</dt><dd>1</dd>" in body
    assert "Responses Redacted</dt><dd>1</dd>" in body
    assert "Prompts Allowed to Proceed</dt><dd>2</dd>" in body
    assert "Evaluator Could Not Determine</dt><dd>1</dd>" in body
    # Policies actually enforced, aggregated from event_metadata.policy_rules.
    assert "block.egress" in body
    # Evaluator-unknown must read as an honest non-pass, not a silent success.
    assert "never counted as a compliant pass" in body


def test_ledger_filter_surfaces_enforcement_rows_distinctly(make_org, client):
    """The ledger can be filtered to host-side enforcement rows so the record shows
    enforcement, not just observations. Each row carries event_type so the dashboard
    can badge blocked/redacted distinctly."""
    org = make_org()
    client.post("/v1/logs/batch", headers=org["auth"],
                json=[_blocked_event(seed="f1", event_type="blocked")])         # seq 1
    client.post("/v1/logs/batch", headers=org["auth"],
                json=[_blocked_event(seed="f2", event_type="redacted")])        # seq 2
    client.post("/v1/logs/batch", headers=org["auth"], json=[{
        "prompt_hash": _h("n"), "response_hash": _h("nr"),
        "token_count": 10, "policy_tag": "chat"}])                              # seq 3

    b = client.get("/v1/logs?verdict=blocked", headers=org["auth"]).json()
    assert [r["seq"] for r in b["items"]] == [1]
    assert b["items"][0]["event_type"] == "blocked"

    rd = client.get("/v1/logs?verdict=redacted", headers=org["auth"]).json()
    assert [r["seq"] for r in rd["items"]] == [2]
    assert rd["items"][0]["event_type"] == "redacted"

    # The unfiltered ledger still returns every row, enforcement rows included.
    assert client.get("/v1/logs", headers=org["auth"]).json()["total"] == 3

    # /v1/stats surfaces the enforcement counts so the dashboard never mislabels a
    # blocked/redacted event as a clean pass.
    stats = client.get("/v1/stats", headers=org["auth"]).json()
    assert stats["blocked"] == 1
    assert stats["redacted"] == 1
