"""Hermetic coverage for the optional OpenAI judge and multi-judge merge."""

from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.error import URLError

from app import judge, openai_judge
from app.schemas import Verdict


def _settings(**overrides):
    values = {
        "openai_api_key": "test-openai-key",
        "openai_model": "gpt-5.6",
        "openai_timeout": 2.0,
        "gemini_fail_closed": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_openai_disabled_does_not_make_a_request(monkeypatch):
    monkeypatch.setattr(openai_judge, "get_settings",
                        lambda: _settings(openai_api_key=""))
    called = False

    def fail_if_called(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("disabled provider made a network request")

    monkeypatch.setattr(openai_judge.urllib_request, "urlopen", fail_if_called)
    result = openai_judge.evaluate({"token_count": 1})
    assert result.decision == "unknown"
    assert "no_api_key" in result.reason
    assert called is False


def test_openai_request_is_content_blind_and_parses_verdict(monkeypatch):
    monkeypatch.setattr(openai_judge, "get_settings", lambda: _settings())
    captured = {}
    response = {"output_text": json.dumps({
        "policy_breach": True,
        "reason": "metadata rule matched",
        "risk_score": 88,
        "decision": "breach",
        "rules": ["pii_signal"],
    })}

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _Response(response)

    monkeypatch.setattr(openai_judge.urllib_request, "urlopen", fake_urlopen)
    result = openai_judge.evaluate({
        "prompt_hash": "a" * 64,
        "response_hash": "b" * 64,
        "token_count": 12,
        "policy_tag": "chat",
        "event_metadata": {
            "provider": "test",
            "raw_prompt": "SECRET_RAW_PROMPT",
        },
        "raw_prompt": "SECRET_RAW_PROMPT",
    })

    body_text = json.dumps(captured["body"])
    assert "SECRET_RAW_PROMPT" not in body_text
    assert captured["timeout"] == 2.0
    assert result == Verdict(policy_breach=True, reason="metadata rule matched",
                             risk_score=88, decision="breach",
                             rules=["pii_signal"])


def test_openai_provider_failure_is_unknown(monkeypatch):
    monkeypatch.setattr(openai_judge, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        openai_judge.urllib_request, "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError("offline")),
    )
    result = openai_judge.evaluate({"token_count": 1})
    assert result.decision == "unknown"
    assert result.policy_breach is False
    assert result.reason.startswith("evaluator_unavailable:")


def test_multi_judge_merge_is_conservative():
    clean = Verdict(decision="clean", reason="gemini clean", risk_score=10,
                    rules=["baseline"])
    breach = Verdict(policy_breach=True, decision="breach", reason="openai breach",
                     risk_score=81, rules=["pii_signal"])
    result = judge.combine(clean, breach)
    assert result.decision == "breach"
    assert result.policy_breach is True
    assert result.risk_score == 81
    assert result.rules == ["baseline", "pii_signal"]
