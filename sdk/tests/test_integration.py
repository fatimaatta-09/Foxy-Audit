"""End-to-end SDK integration test against a stdlib mock backend + fake fox.

Proves the full dispatch path without Postgres or Gemini:
  • the instant `hash_ok` UDP ping is emitted per audited call,
  • the AsyncDispatcher POSTs a BATCH (a JSON list) to {endpoint}/v1/logs/batch
    with a Bearer header, and each item carries only hashes / token_count /
    policy_tag (never raw text),
  • every audited call is eventually delivered (no drops across batches).

Grading is asynchronous in the real backend (the batch endpoint returns 202
{"status": "pending"} and the worker back-fills verdicts later), so the SDK gets
no verdict at ingest time — breach signalling is not part of this HTTP path.

Run:  PYTHONPATH=sdk/src python sdk/tests/test_integration.py
 or:  pip install -e ./sdk && pytest sdk/tests/test_integration.py
"""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from foxy_audit import FoxyClient


class _MockBackend:
    """Captures POSTs and replies like the real batch endpoint (202, pending)."""

    def __init__(self):
        self.requests: list[tuple[str, dict, list]] = []   # (path, headers, body)
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
                body = json.loads(self.rfile.read(length) or b"[]")
                backend.requests.append((self.path, dict(self.headers), body))
                resp = json.dumps({"status": "pending", "count": len(body)}).encode()
                self.send_response(202)
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


def _delivered(backend) -> list:
    """All payload items received so far, across every batch POST."""
    return [item for _, _, body in backend.requests for item in body]


def _wait_for_delivered(backend, n, timeout=8.0):
    deadline = time.time() + timeout
    while len(_delivered(backend)) < n and time.time() < deadline:
        time.sleep(0.02)
    return len(_delivered(backend)) >= n


def test_full_dispatch_batches_hashes_only():
    backend = _MockBackend()
    sock, udp_port = _udp_listener()
    try:
        foxy = FoxyClient(api_key="foxy_sk_test", endpoint=backend.endpoint, udp_port=udp_port)

        @foxy.audit("hipaa_basic")
        def ask(prompt: str) -> str:
            return "a clinical summary response"

        # ── first audited call ──
        assert ask("a patient prompt") == "a clinical summary response"

        green = _recv_event(sock, 2.0)
        assert green and green["event"] == "hash_ok" and green["policy"] == "hipaa_basic", green

        assert _wait_for_delivered(backend, 1), "backend never received the first batch"
        path, headers, body = backend.requests[0]
        assert path == "/v1/logs/batch", path
        assert headers.get("Authorization") == "Bearer foxy_sk_test"
        assert isinstance(body, list) and len(body) >= 1, body

        item = body[0]
        assert len(item["prompt_hash"]) == 64 and len(item["response_hash"]) == 64
        assert item["policy_tag"] == "hipaa_basic" and isinstance(item["token_count"], int)
        # zero-knowledge: no raw text leaves the host, under any key
        wire = json.dumps(body)
        assert "a patient prompt" not in wire and "clinical summary" not in wire

        # ── second call: also delivered (a later batch or coalesced) ──
        ask("another prompt")
        green2 = _recv_event(sock, 2.0)
        assert green2 and green2["event"] == "hash_ok", green2

        assert _wait_for_delivered(backend, 2), "second call was never delivered"
        delivered = _delivered(backend)
        assert len(delivered) == 2
        assert all(len(d["prompt_hash"]) == 64 for d in delivered)
        # every batch went to the batch endpoint with the same Bearer key
        assert all(p == "/v1/logs/batch" for p, _, _ in backend.requests)
        assert all(h.get("Authorization") == "Bearer foxy_sk_test"
                   for _, h, _ in backend.requests)
        print("integration: batched hash-only dispatch PASS")
    finally:
        sock.close()
        backend.stop()


if __name__ == "__main__":
    test_full_dispatch_batches_hashes_only()
    print("\nINTEGRATION TEST PASSED")
