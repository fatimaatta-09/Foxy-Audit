"""End-to-end SDK integration test against a stdlib mock backend + fake fox.

Proves the full dispatch path without Postgres or Gemini:
  • the instant `hash_ok` UDP ping is emitted,
  • the metadata is POSTed to {endpoint}/v1/logs with a Bearer header and only
    hashes / token_count / policy_tag (never raw text),
  • when the backend's verdict flags a breach, the red `policy_breach` UDP ping
    is fired with an int risk_score.

Run:  PYTHONPATH=sdk/src python sdk/tests/test_integration.py
 or:  pip install -e ./sdk[test] && pytest sdk/tests/test_integration.py
"""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from foxy_audit import FoxyClient


class _MockBackend:
    """Captures POSTs and replies with a configurable verdict."""

    def __init__(self):
        self.requests: list[tuple[dict, dict]] = []
        self.breach = False
        handler = self._make_handler()
        self.server = HTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _make_handler(self):
        backend = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):  # silence
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                backend.requests.append((dict(self.headers), body))
                verdict = {
                    "policy_breach": backend.breach,
                    "reason": "Anomalous token count" if backend.breach else "",
                    "risk_score": 88 if backend.breach else 3,
                }
                resp = json.dumps({
                    "log_id": "00000000-0000-0000-0000-000000000001",
                    "seq": 1, "chain_hash": "a" * 64, "verdict": verdict,
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)

        return Handler

    def stop(self):
        self.server.shutdown()


def _udp_listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(0.5)
    return sock, sock.getsockname()[1]


def _recv_event(sock, timeout=5.0):
    sock.settimeout(timeout)
    try:
        data, _ = sock.recvfrom(4096)
        return json.loads(data.decode())
    except socket.timeout:
        return None


def _wait_for_requests(backend, n, timeout=5.0):
    deadline = time.time() + timeout
    while len(backend.requests) < n and time.time() < deadline:
        time.sleep(0.02)
    return len(backend.requests) >= n


def test_full_dispatch_compliant_then_breach():
    backend = _MockBackend()
    sock, udp_port = _udp_listener()
    try:
        foxy = FoxyClient(api_key="foxy_sk_test", endpoint=backend.endpoint, udp_port=udp_port)

        @foxy.audit("hipaa_basic")
        def ask(prompt: str) -> str:
            return "a clinical summary response"

        # ── compliant call ──
        ask("a patient prompt")

        green = _recv_event(sock, 2.0)
        assert green and green["event"] == "hash_ok" and green["policy"] == "hipaa_basic", green

        assert _wait_for_requests(backend, 1), "backend never received the POST"
        headers, body = backend.requests[0]
        assert headers.get("Authorization") == "Bearer foxy_sk_test"
        assert len(body["prompt_hash"]) == 64 and len(body["response_hash"]) == 64
        assert body["policy_tag"] == "hipaa_basic" and isinstance(body["token_count"], int)
        # zero-knowledge: no raw text fields leak
        assert "prompt" not in body and "response" not in body and "text" not in body
        # compliant verdict → NO breach ping
        assert _recv_event(sock, 1.5) is None, "compliant call should not emit a breach ping"

        # ── breach call ──
        backend.breach = True
        ask("another prompt")

        green2 = _recv_event(sock, 2.0)
        assert green2 and green2["event"] == "hash_ok", green2
        red = _recv_event(sock, 5.0)
        assert red and red["event"] == "policy_breach", f"expected breach ping, got {red}"
        assert isinstance(red["risk_score"], int) and red["risk_score"] == 88
        assert red["reason"] == "Anomalous token count"
        assert red["policy"] == "hipaa_basic"
        print("integration: compliant + breach dispatch PASS")
    finally:
        sock.close()
        backend.stop()


if __name__ == "__main__":
    test_full_dispatch_compliant_then_breach()
    print("\nINTEGRATION TEST PASSED")
