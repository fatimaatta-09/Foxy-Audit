"""Background dispatch — keeps all network I/O off the host application's thread.

A single lazily-started daemon thread drains a queue of ingest jobs. For each
job it POSTs the metadata to the backend; if the returned Gemini verdict flags a
policy breach, it fires the red `policy_breach` UDP ping to the desktop fox.

Every failure path is swallowed and logged at debug level — telemetry must never
crash, block, or surface an error to the host app.
"""

from __future__ import annotations

import logging
import queue
import threading

from . import transport, udp
from .config import FoxyConfig

log = logging.getLogger("foxy_audit")


class _Dispatcher:
    def __init__(self) -> None:
        self._q: "queue.Queue[tuple[FoxyConfig, dict]]" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def _ensure_worker(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run, name="foxy-audit-dispatch", daemon=True
                )
                self._thread.start()

    def submit(self, cfg: FoxyConfig, payload: dict) -> None:
        self._ensure_worker()
        self._q.put((cfg, payload))

    def _run(self) -> None:
        while True:
            cfg, payload = self._q.get()
            try:
                self._process(cfg, payload)
            except Exception as exc:  # never let the worker die
                log.debug("foxy-audit dispatch error: %s", exc)
            finally:
                self._q.task_done()

    def _process(self, cfg: FoxyConfig, payload: dict) -> None:
        try:
            result = transport.post_log(cfg.endpoint, cfg.api_key, payload, cfg.timeout)
        except Exception as exc:
            log.debug("foxy-audit POST failed: %s", exc)
            return
        verdict = (result or {}).get("verdict") or {}
        if verdict.get("policy_breach") and cfg.desktop_ping:
            udp.send_ping(
                {
                    "event": "policy_breach",
                    "reason": str(verdict.get("reason", "Policy violation"))[:300],
                    "risk_score": int(verdict.get("risk_score", 100)),
                    "policy": payload.get("policy_tag", "default"),
                    "tokens": payload.get("token_count", 0),
                },
                cfg.udp_host,
                cfg.udp_port,
            )


_DISPATCHER = _Dispatcher()


def submit(cfg: FoxyConfig, payload: dict) -> None:
    """Enqueue an ingest job for background delivery."""
    _DISPATCHER.submit(cfg, payload)
