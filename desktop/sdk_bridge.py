"""
Foxy Audit — Local SDK Bridge (UDP Listener)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Listens on localhost:9999 for UDP datagrams sent by the
@foxy.audit Python SDK decorator each time it hashes a prompt.

Protocol
────────
The SDK sends a small JSON payload over UDP:

  Successful hash:
    {"event": "hash_ok", "policy": "hipaa_basic", "ts": 1719300000}

  Gemini policy breach:
    {"event": "policy_breach", "reason": "Anomalous token count",
     "risk_score": 87, "ts": 1719300000}

This thread parses the JSON, emits the appropriate Qt signal,
and silently discards malformed or oversized packets.
"""

from __future__ import annotations

import json
import socket

from PyQt6.QtCore import QThread, pyqtSignal


SDK_LISTEN_HOST = "127.0.0.1"
SDK_LISTEN_PORT = 9999
MAX_PACKET_SIZE = 4096


class SDKBridgeListener(QThread):
    """Lightweight UDP listener for local SDK telemetry pings."""

    hash_confirmed = pyqtSignal(dict)   # {"event": "hash_ok", ...}
    policy_breach  = pyqtSignal(dict)   # {"event": "policy_breach", ...}

    def __init__(self, host: str = SDK_LISTEN_HOST,
                 port: int = SDK_LISTEN_PORT, parent=None):
        super().__init__(parent)
        self._host = host
        self._port = port
        self._sock: socket.socket | None = None

    def run(self):
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind((self._host, self._port))
            # Short timeout so we can check isInterruptionRequested regularly
            self._sock.settimeout(0.5)
        except OSError as exc:
            print(f"[SDKBridge] Could not bind {self._host}:{self._port}: {exc}")
            return

        while not self.isInterruptionRequested():
            try:
                data, addr = self._sock.recvfrom(MAX_PACKET_SIZE)
            except socket.timeout:
                continue
            except OSError:
                break

            self._process_packet(data)

        self._cleanup()

    def _process_packet(self, data: bytes):
        try:
            payload = json.loads(data.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return  # silently discard malformed packets

        if not isinstance(payload, dict):
            return

        event = payload.get("event", "")

        if event == "hash_ok":
            self.hash_confirmed.emit(payload)
        elif event == "policy_breach":
            self.policy_breach.emit(payload)
        # Unknown event types are silently ignored

    def _cleanup(self):
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def stop(self):
        """Gracefully stop the listener."""
        self.requestInterruption()
        self._cleanup()          # unblock recvfrom immediately
        self.quit()
        self.wait(2000)
