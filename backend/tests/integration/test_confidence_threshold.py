"""confidence_threshold actually changes how the judge grades (P4 Phase A).

The setting was stored on OrgPolicy, copied into the tamper-evident evidence
snapshot, and shown in the dashboard — and read by nothing. The owner could not
tell whether changing it did anything, because it did not. The evidence record
asserted a control that was not being applied, which is the worst kind of untrue.

The trap in testing this is that grepping a built prompt for the word "high"
passes even if the model ignores the instruction entirely. So there are two
layers here:

  · the prompt genuinely DIFFERS across all three settings, and
  · a judge that follows its instructions returns DIFFERENT VERDICTS on the same
    borderline event, through the real evaluate() path.

The second uses a stub that stands in for an instruction-following model: it
holds one weak internal confidence and applies whichever conservatism directive
the system prompt carries. It cannot prove a real model obeys — nothing offline
can — but it does prove the directive reaches the model layer and that honouring
it changes the verdict rather than only the text.
"""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from app import gemini, openai_judge
from app.policy_snapshot import POLICY_SNAPSHOT_SCHEMA, judge_policy_config

LEVELS = ("high", "balanced", "low")


def _config(level=None, **extra):
    cfg = {"pii_detection": True, "prompt_injection": True,
           "regulated_data_mode": False, "max_token_threshold": 50_000}
    if level is not None:
        cfg["confidence_threshold"] = level
    cfg.update(extra)
    return cfg


# ══ 1 · the value reaches the judge at all ═════════════════════════════════
# It never used to: judge_policy_config projected four keys and dropped this one,
# so wiring the prompt builders alone would have changed precisely nothing.

@pytest.mark.parametrize("level", LEVELS)
def test_the_snapshot_projection_carries_the_setting(level):
    snapshot = {"schema": POLICY_SNAPSHOT_SCHEMA, "pii_detection": True,
                "prompt_injection": True, "regulated_data_mode": False,
                "max_token_threshold": 50_000, "confidence_threshold": level,
                "enforcement_mode": "monitor", "notify_on_breach": "immediate"}
    assert judge_policy_config(snapshot)["confidence_threshold"] == level


def test_an_older_snapshot_without_the_key_still_grades_as_balanced():
    """Snapshots predating the field must keep grading, under the behaviour they
    were originally graded with. Rejecting them would stop grading historical
    evidence outright."""
    snapshot = {"schema": POLICY_SNAPSHOT_SCHEMA, "pii_detection": True,
                "prompt_injection": True, "regulated_data_mode": False,
                "max_token_threshold": 50_000}
    config = judge_policy_config(snapshot)
    assert config is not None, "an old snapshot must not be rejected"
    assert config["confidence_threshold"] == "balanced"


def test_the_legacy_worker_path_also_carries_it(make_org):
    """Rows written before chain V3 have no snapshot, so the worker reads the
    org's current policy. That path projected four keys too."""
    import uuid

    from app.db import SessionLocal
    from app.models import OrgPolicy
    from app.worker import _policy_config

    org = make_org()
    oid = uuid.UUID(org["org_id"])
    with SessionLocal() as db:
        row = db.get(OrgPolicy, oid) or OrgPolicy(org_id=oid)
        row.confidence_threshold = "high"
        db.add(row)
        db.commit()
        cfg = _policy_config(db, oid, event_metadata=None)
    assert cfg["confidence_threshold"] == "high"


# ══ 2 · the prompt genuinely differs ═══════════════════════════════════════

@pytest.mark.parametrize("builder", [gemini._build_system_prompt,
                                     openai_judge._build_system_prompt])
def test_all_three_settings_build_different_prompts(builder):
    prompts = {level: builder(_config(level), None) for level in LEVELS}
    assert len(set(prompts.values())) == 3, (
        "two confidence settings produce the same prompt — at least one of them "
        "cannot be doing anything")


@pytest.mark.parametrize("builder", [gemini._build_system_prompt,
                                     openai_judge._build_system_prompt])
def test_balanced_is_byte_identical_to_an_unset_threshold(builder):
    assert builder(_config("balanced"), None) == builder(_config(), None)


@pytest.mark.parametrize("module", [gemini, openai_judge])
def test_balanced_adds_no_directive_whatsoever(module):
    """THE SAFETY PROPERTY, stated directly. Every existing org sits on balanced,
    so turning this on must not add a single word to their prompt — otherwise the
    change silently re-grades every tenant who never touched the setting.

    Comparing balanced against "unset" is not enough: both resolve to balanced, so
    that comparison stays true even if balanced starts emitting a sentence. This
    asserts the absence itself."""
    assert module._confidence_rule(_config("balanced")) is None
    assert module._confidence_rule(_config()) is None
    assert module._confidence_rule(None) is None
    prompt = module._build_system_prompt(_config("balanced"), None)
    for directive in module._CONFIDENCE_RULES.values():
        assert directive not in prompt
    assert "confidence threshold" not in prompt.lower()


@pytest.mark.parametrize("builder", [gemini._build_system_prompt,
                                     openai_judge._build_system_prompt])
def test_high_and_low_pull_in_opposite_directions(builder):
    high = builder(_config("high"), None).lower()
    low = builder(_config("low"), None).lower()
    assert "only when" in high or "do not flag" in high
    assert "every plausible concern" in low
    assert "false positive" in high and "false positive" in low


@pytest.mark.parametrize("builder", [gemini._build_system_prompt,
                                     openai_judge._build_system_prompt])
@pytest.mark.parametrize("junk", ["", "  ", "HIGHEST", "medium", None, 7])
def test_an_unrecognised_value_falls_back_to_balanced(builder, junk):
    """A bad row in the database must not take grading down for that tenant."""
    assert builder(_config(junk), None) == builder(_config(), None)


@pytest.mark.parametrize("builder", [gemini._build_system_prompt,
                                     openai_judge._build_system_prompt])
def test_case_and_padding_are_tolerated(builder):
    assert builder(_config("  HIGH  "), None) == builder(_config("high"), None)


# ══ 3 · the verdict differs — behaviour, not text ══════════════════════════

class _BorderlineJudge:
    """Stands in for a model grading a genuinely ambiguous event.

    It holds ONE weak internal confidence that the event is a breach (0.55 — real
    but not convincing) and applies whichever conservatism directive it was given.
    A model that ignores the directive returns the same verdict three times, which
    is exactly what these tests are built to catch.
    """

    CONFIDENCE = 0.55

    @classmethod
    def verdict_for(cls, system_prompt: str) -> dict:
        p = system_prompt.lower()
        if "do not flag on suspicion" in p:
            needed, risk = 0.80, int(cls.CONFIDENCE * 100) - 25
        elif "every plausible concern" in p:
            needed, risk = 0.30, int(cls.CONFIDENCE * 100) + 25
        else:
            needed, risk = 0.50, int(cls.CONFIDENCE * 100)
        breach = cls.CONFIDENCE >= needed
        return {"policy_breach": breach, "decision": "breach" if breach else "clean",
                "reason": "borderline token pattern", "risk_score": risk, "rules": []}


def _gemini_settings():
    return SimpleNamespace(gemini_api_key="k", gemini_model="m", gemini_timeout=2.0,
                           gemini_fail_closed=False)


def _openai_settings():
    return SimpleNamespace(openai_api_key="k", openai_model="m", openai_timeout=2.0,
                           gemini_fail_closed=False)


def _gemini_verdict(monkeypatch, level):
    monkeypatch.setattr(gemini, "get_settings", _gemini_settings)

    class _FakeGenAI:
        def configure(self, api_key=None, **_kw):
            pass

        def GenerativeModel(self, *_a, **kw):      # noqa: N802 — mirrors the SDK
            prompt = kw.get("system_instruction", "")
            return SimpleNamespace(
                generate_content=lambda *_args, **_kwargs: SimpleNamespace(
                    text=json.dumps(_BorderlineJudge.verdict_for(prompt))))

    monkeypatch.setitem(sys.modules, "google.generativeai", _FakeGenAI())
    return gemini.evaluate({"token_count": 900, "policy_tag": "chat"}, _config(level))


def _openai_verdict(monkeypatch, level):
    monkeypatch.setattr(openai_judge, "get_settings", _openai_settings)

    class _Resp:
        def __init__(self, payload):
            self._p = payload

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def read(self):
            return json.dumps(self._p).encode()

    def _urlopen(request, timeout=None):
        body = json.loads(request.data.decode())
        prompt = body["input"][0]["content"][0]["text"]
        return _Resp({"output_text": json.dumps(_BorderlineJudge.verdict_for(prompt))})

    monkeypatch.setattr(openai_judge.urllib_request, "urlopen", _urlopen)
    return openai_judge.evaluate({"token_count": 900, "policy_tag": "chat"}, _config(level))


@pytest.mark.parametrize("run", [_gemini_verdict, _openai_verdict])
def test_the_same_event_grades_differently_at_each_setting(run, monkeypatch):
    """THE POINT OF PHASE A. One borderline event, three settings, three verdicts."""
    got = {level: run(monkeypatch, level) for level in LEVELS}
    shapes = {level: (v.policy_breach, v.risk_score) for level, v in got.items()}
    assert len(set(shapes.values())) == 3, f"the setting changed nothing: {shapes}"
    # High refuses to flag on weak evidence; low flags and scores it higher.
    assert got["high"].policy_breach is False
    assert got["low"].policy_breach is True
    assert got["high"].risk_score < got["balanced"].risk_score < got["low"].risk_score


@pytest.mark.parametrize("run", [_gemini_verdict, _openai_verdict])
def test_balanced_matches_an_unset_threshold_end_to_end(run, monkeypatch):
    balanced = run(monkeypatch, "balanced")
    unset = run(monkeypatch, None)
    assert (balanced.policy_breach, balanced.risk_score) == (unset.policy_breach,
                                                             unset.risk_score)


# ══ 4 · the line that must survive ═════════════════════════════════════════

def test_gemini_never_logs_the_exception_message(monkeypatch, caplog):
    """Google puts the API key in the URL as a query param, and several exception
    types embed the request URL. Logging str(exc) writes tenant keys to the logs.
    This is a security guard, not a style preference."""
    monkeypatch.setattr(gemini, "get_settings", _gemini_settings)
    secret = "https://generativelanguage.googleapis.com/v1/models?key=AIzaLEAKED123"

    class _Boom:
        def configure(self, api_key=None, **_kw):
            raise RuntimeError(secret)

    monkeypatch.setitem(sys.modules, "google.generativeai", _Boom())
    with caplog.at_level("WARNING"):
        verdict = gemini.evaluate({"token_count": 1}, _config("high"))
    assert verdict.decision == "unknown"
    assert "AIzaLEAKED123" not in caplog.text
    assert "AIzaLEAKED123" not in verdict.reason
    assert "RuntimeError" in caplog.text


def test_the_prompt_never_carries_content_or_secrets():
    """The directive is about grading posture. It must not smuggle anything else."""
    for builder in (gemini._build_system_prompt, openai_judge._build_system_prompt):
        for level in LEVELS:
            prompt = builder(_config(level), None)
            for forbidden in ("api_key", "key=", "prompt_text", "response_text"):
                assert forbidden not in prompt
