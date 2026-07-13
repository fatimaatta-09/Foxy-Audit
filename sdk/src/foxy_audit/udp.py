"""Best-effort local UDP ping to the desktop fox companion.

Speaks the exact protocol of the desktop app's sdk_bridge listener:
a JSON datagram (<= 4096 bytes) sent to 127.0.0.1:9999, with an `event` of
"evaluating" (grading pending — fired instantly), "policy_breach", or the legacy
"hash_ok". This is purely cosmetic ambient feedback (the fox reacts); the
authoritative record is the HTTP-written
ledger row, so packet loss here is harmless. The whole send is wrapped so it
can never raise into the SDK or the host application.
"""

from __future__ import annotations

import json
import socket
import time

MAX_PACKET = 4096  # must match sdk_bridge.MAX_PACKET_SIZE


def send_ping(payload: dict, host: str = "127.0.0.1", port: int = 9999) -> bool:
    """Fire a single UDP datagram. Returns True on success, False otherwise.

    Always stamps `ts`. If the encoded payload exceeds the listener's 4096-byte
    limit, the `reason` field (if present) is trimmed before re-encoding.
    """
    try:
        msg = dict(payload)
        msg.setdefault("ts", time.time())
        data = json.dumps(msg).encode("utf-8")
        if len(data) > MAX_PACKET and isinstance(msg.get("reason"), str):
            overflow = len(data) - MAX_PACKET
            msg["reason"] = msg["reason"][: max(0, len(msg["reason"]) - overflow - 1)]
            data = json.dumps(msg).encode("utf-8")
        if len(data) > MAX_PACKET:
            return False  # still too big (no reason field to trim) — drop it
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.sendto(data, (host, port))
        finally:
            sock.close()
        return True
    except Exception:
        return False
