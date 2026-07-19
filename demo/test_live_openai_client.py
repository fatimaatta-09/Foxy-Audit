"""Hermetic checks for the real-client demo's Responses API boundary."""

from __future__ import annotations

import json

from demo import live_openai_client


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_call_openai_uses_responses_api(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _Response({"output_text": "real-provider-shaped response"})

    monkeypatch.setattr(live_openai_client.urllib_request, "urlopen", fake_urlopen)
    result = live_openai_client.call_openai("hello", "gpt-5.6", "test-key")

    assert result == "real-provider-shaped response"
    assert captured["url"] == live_openai_client.OPENAI_RESPONSES_URL
    assert captured["timeout"] == 30
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["body"]["model"] == "gpt-5.6"
    assert captured["body"]["input"][0]["content"][0]["text"] == "hello"


def test_response_text_supports_responses_output_items():
    assert live_openai_client._response_text({
        "output": [{"content": [{"type": "output_text", "text": "nested response"}]}]
    }) == "nested response"
