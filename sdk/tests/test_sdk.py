"""Unit tests for the Foxy Audit SDK that need no backend.

Run with:  pip install -e ./sdk[test]  &&  pytest sdk/tests
"""

import json
import socket
import time

from foxy_audit import FoxyClient
from foxy_audit import hashing


def _udp_listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))          # ephemeral port
    sock.settimeout(2.0)
    return sock, sock.getsockname()[1]


def test_hashing_is_64_hex():
    h = hashing.sha256_hex("hello")
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)


def test_return_value_passes_through():
    foxy = FoxyClient(api_key="", desktop_ping=False)

    @foxy.audit("demo")
    def f(prompt: str) -> str:
        return prompt.upper()

    assert f("hi") == "HI"


def test_host_exception_not_masked():
    foxy = FoxyClient(api_key="", desktop_ping=False)

    @foxy.audit("demo")
    def boom(prompt: str) -> str:
        raise ValueError("host error")

    try:
        boom("x")
        assert False, "expected ValueError"
    except ValueError as e:
        assert str(e) == "host error"


def test_hash_ok_udp_emitted():
    sock, port = _udp_listener()
    try:
        foxy = FoxyClient(api_key="", udp_port=port)   # no key → HTTP disabled, UDP still fires

        @foxy.audit("hipaa_basic")
        def ask(prompt: str) -> str:
            return "a response"

        ask("a prompt")
        data, _ = sock.recvfrom(4096)
        msg = json.loads(data.decode())
        assert msg["event"] == "hash_ok"
        assert msg["policy"] == "hipaa_basic"
        assert isinstance(msg["tokens"], int)
        assert "ts" in msg
    finally:
        sock.close()


def test_agent_included_in_payload(monkeypatch):
    """audit(policy, agent=…) puts the agent into the backend payload (6B)."""
    from foxy_audit import dispatch
    captured = {}
    monkeypatch.setattr(dispatch, "submit", lambda cfg, payload: captured.update(payload))
    foxy = FoxyClient(api_key="foxy_sk_test", desktop_ping=False)   # enabled → dispatch fires

    @foxy.audit("demo", agent="gpt-4o")
    def ask(prompt: str) -> str:
        return "resp"

    ask("hi")
    assert captured.get("agent") == "gpt-4o"


def test_agent_absent_by_default(monkeypatch):
    from foxy_audit import dispatch
    captured = {}
    monkeypatch.setattr(dispatch, "submit", lambda cfg, payload: captured.update(payload))
    foxy = FoxyClient(api_key="foxy_sk_test", desktop_ping=False)

    @foxy.audit("demo")
    def ask(prompt: str) -> str:
        return "resp"

    ask("hi")
    assert not captured.get("agent")          # no agent when not specified


def test_no_key_means_http_disabled_but_udp_enabled():
    foxy = FoxyClient(api_key="")
    assert foxy.enabled is False
    foxy2 = FoxyClient(api_key="foxy_sk_test")
    assert foxy2.enabled is True
