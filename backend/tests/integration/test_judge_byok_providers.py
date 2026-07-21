"""Both judges accept a per-call BYOK api_key and use it instead of the platform key.

Hermetic: every provider call is mocked — these tests never touch the network.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from app import gemini, openai_judge


def _settings(**overrides):
    values = {
        "gemini_api_key": "PLATFORM-gemini",
        "gemini_model": "gemini-2.5-flash",
        "gemini_timeout": 2.0,
        "openai_api_key": "PLATFORM-openai",
        "openai_model": "gpt-5.6",
        "openai_timeout": 2.0,
        "gemini_fail_closed": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


_VERDICT = {"policy_breach": False, "reason": "looks fine", "risk_score": 3,
            "decision": "clean", "rules": []}


# ── Gemini ───────────────────────────────────────────────────────────────────

class _FakeGenAI:
    """Stand-in for google.generativeai; records the key it was configured with."""

    def __init__(self):
        self.configured_with = None

    def configure(self, api_key=None, **_kw):
        self.configured_with = api_key

    def GenerativeModel(self, *_a, **_kw):  # noqa: N802 — mirrors the SDK's name
        return SimpleNamespace(
            generate_content=lambda *_args, **_kwargs:
                SimpleNamespace(text=json.dumps(_VERDICT)))


def _install_fake_genai(monkeypatch) -> _FakeGenAI:
    import sys
    fake = _FakeGenAI()
    monkeypatch.setitem(sys.modules, "google.generativeai", fake)
    return fake


def test_gemini_uses_the_supplied_byok_key(monkeypatch):
    monkeypatch.setattr(gemini, "get_settings", lambda: _settings())
    fake = _install_fake_genai(monkeypatch)
    verdict = gemini.evaluate({"token_count": 5}, api_key="TENANT-gemini")
    assert fake.configured_with == "TENANT-gemini"
    assert verdict.decision == "clean"


def test_gemini_falls_back_to_the_platform_key(monkeypatch):
    monkeypatch.setattr(gemini, "get_settings", lambda: _settings())
    fake = _install_fake_genai(monkeypatch)
    gemini.evaluate({"token_count": 5})
    assert fake.configured_with == "PLATFORM-gemini"


def test_gemini_byok_key_works_even_when_the_platform_key_is_absent(monkeypatch):
    """A BYOK tenant must grade on a deployment that has no platform key at all."""
    monkeypatch.setattr(gemini, "get_settings", lambda: _settings(gemini_api_key=""))
    fake = _install_fake_genai(monkeypatch)
    verdict = gemini.evaluate({"token_count": 5}, api_key="TENANT-gemini")
    assert fake.configured_with == "TENANT-gemini"
    assert verdict.decision == "clean"


def test_gemini_without_any_key_is_unavailable_not_a_crash(monkeypatch):
    monkeypatch.setattr(gemini, "get_settings", lambda: _settings(gemini_api_key=""))
    verdict = gemini.evaluate({"token_count": 5})
    assert verdict.decision == "unknown"
    assert verdict.reason == "evaluator_unavailable:no_api_key"


# ── OpenAI ───────────────────────────────────────────────────────────────────

class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _capture_openai(monkeypatch) -> dict:
    seen: dict = {}

    def fake_urlopen(request, timeout=None):
        seen["authorization"] = request.headers.get("Authorization")
        return _Response({"output_text": json.dumps(_VERDICT)})

    monkeypatch.setattr(openai_judge.urllib_request, "urlopen", fake_urlopen)
    return seen


def test_openai_uses_the_supplied_byok_key(monkeypatch):
    monkeypatch.setattr(openai_judge, "get_settings", lambda: _settings())
    seen = _capture_openai(monkeypatch)
    verdict = openai_judge.evaluate({"token_count": 5}, api_key="TENANT-openai")
    assert seen["authorization"] == "Bearer TENANT-openai"
    assert verdict.decision == "clean"


def test_openai_falls_back_to_the_platform_key(monkeypatch):
    monkeypatch.setattr(openai_judge, "get_settings", lambda: _settings())
    seen = _capture_openai(monkeypatch)
    openai_judge.evaluate({"token_count": 5})
    assert seen["authorization"] == "Bearer PLATFORM-openai"


def test_openai_byok_key_works_even_when_the_platform_key_is_absent(monkeypatch):
    monkeypatch.setattr(openai_judge, "get_settings", lambda: _settings(openai_api_key=""))
    seen = _capture_openai(monkeypatch)
    verdict = openai_judge.evaluate({"token_count": 5}, api_key="TENANT-openai")
    assert seen["authorization"] == "Bearer TENANT-openai"
    assert verdict.decision == "clean"


def test_openai_without_any_key_is_unavailable_not_a_crash(monkeypatch):
    monkeypatch.setattr(openai_judge, "get_settings", lambda: _settings(openai_api_key=""))

    def fail_if_called(*_a, **_kw):
        raise AssertionError("keyless provider made a network request")

    monkeypatch.setattr(openai_judge.urllib_request, "urlopen", fail_if_called)
    verdict = openai_judge.evaluate({"token_count": 5})
    assert verdict.decision == "unknown"
    assert verdict.reason == "evaluator_unavailable:no_api_key"
