"""Durable, retrying background delivery for metadata-only events."""

from __future__ import annotations

import atexit
import json
import logging
import queue
import threading
import time
from collections import defaultdict

import requests

from .config import FoxyConfig
from .spool import EventSpool

log = logging.getLogger("foxy_audit")


def _endpoint(cfg: FoxyConfig) -> str:
    base = cfg.endpoint.rstrip("/")
    for suffix in ("/v1/logs/batch", "/v1/logs", "/logs/batch", "/logs"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return f"{base}/v1/logs/batch"


class AsyncDispatcher:
    def __init__(self, batch_size: int = 10, flush_interval: float = 1.0) -> None:
        self._q: "queue.Queue[tuple[FoxyConfig, str]]" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self._shutdown = False
        self._paths: set[str | None] = set()
        atexit.register(self.flush)

    def _ensure_worker(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._shutdown = False
                self._thread = threading.Thread(
                    target=self._run, name="foxy-audit-async-dispatch", daemon=True)
                self._thread.start()

    def submit(self, cfg: FoxyConfig, payload: dict, wait: bool = False):
        spool = EventSpool(cfg.spool_path or None)
        enriched = spool.enqueue(_endpoint(cfg), cfg.api_key, payload)
        self._paths.add(cfg.spool_path or None)
        self._ensure_worker()
        self._q.put((cfg, enriched["event_id"]))
        if wait:
            deadline = time.time() + max(1.0, cfg.timeout)
            while time.time() < deadline:
                self._flush_spool({cfg.spool_path or None})
                receipt = spool.receipt(enriched["event_id"])
                if receipt is not None:
                    return receipt
                time.sleep(0.05)
            raise TimeoutError("Foxy Audit server receipt was not received")
        return {"status": "queued", "event_id": enriched["event_id"],
                "client_seq": enriched["client_seq"]}

    def resume(self, cfg: FoxyConfig) -> None:
        """Wake delivery for a client even when no new event has arrived yet."""
        self._paths.add(cfg.spool_path or None)
        self._ensure_worker()

    def _run(self) -> None:
        while not self._shutdown or not self._q.empty():
            try:
                self._q.get(timeout=self.flush_interval)
            except queue.Empty:
                pass
            self._flush_spool()

    def _flush_spool(self, paths: set[str | None] | None = None) -> None:
        for path in (paths or self._paths or {None}):
            spool = EventSpool(path)
            rows = spool.due(self.batch_size)
            if not rows:
                continue
            grouped = defaultdict(list)
            for row in rows:
                grouped[(row["endpoint"], row["api_key"])].append(row)
            for (endpoint, api_key), batch in grouped.items():
                try:
                    body = [json.loads(row["payload"]) for row in batch]
                    resp = requests.post(
                        endpoint, json=body,
                        headers={"Authorization": f"Bearer {api_key}",
                                 "Content-Type": "application/json"},
                        timeout=10.0,
                    )
                    resp.raise_for_status()
                    try:
                        response = resp.json()
                    except ValueError:
                        response = {"status": "accepted", "http_status": resp.status_code}
                    spool.ack(batch, response)
                except Exception as exc:
                    spool.retry(batch, f"{type(exc).__name__}: {exc}")
                    log.debug("foxy-audit POST failed; event(s) retained: %s", exc)

    def flush(self) -> None:
        self._shutdown = True
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._flush_spool()


_DISPATCHER = AsyncDispatcher()


def submit(cfg: FoxyConfig, payload: dict, wait: bool = False):
    """Persist an event before returning; optionally wait for a server receipt."""
    return _DISPATCHER.submit(cfg, payload, wait=wait)


def resume(cfg: FoxyConfig) -> None:
    _DISPATCHER.resume(cfg)
