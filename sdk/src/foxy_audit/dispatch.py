"""Background dispatch — keeps all network I/O off the host application's thread.

A single lazily-started daemon thread drains a queue of ingest jobs in batches.
For each batch, it POSTs the metadata to the backend.

Every failure path is swallowed and logged at debug level — telemetry must never
crash, block, or surface an error to the host app.
"""

from __future__ import annotations

import atexit
import logging
import queue
import threading
import time
import requests

from .config import FoxyConfig

log = logging.getLogger("foxy_audit")


class AsyncDispatcher:
    def __init__(self, batch_size: int = 10, flush_interval: float = 1.0) -> None:
        self._q: "queue.Queue[tuple[FoxyConfig, dict]]" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self._shutdown = False
        atexit.register(self.flush)

    def _ensure_worker(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._shutdown = False
                self._thread = threading.Thread(
                    target=self._run, name="foxy-audit-async-dispatch", daemon=True
                )
                self._thread.start()

    def submit(self, cfg: FoxyConfig, payload: dict) -> None:
        self._ensure_worker()
        self._q.put((cfg, payload))

    def _run(self) -> None:
        batch = []
        last_flush = time.time()
        
        while not self._shutdown or not self._q.empty():
            try:
                # Wait up to the flush interval for an item
                timeout = max(0.1, self.flush_interval - (time.time() - last_flush))
                cfg, payload = self._q.get(timeout=timeout)
                batch.append((cfg, payload))
            except queue.Empty:
                pass

            now = time.time()
            if batch and (len(batch) >= self.batch_size or now - last_flush >= self.flush_interval):
                self._process_batch(batch)
                batch = []
                last_flush = now

    def _process_batch(self, batch: list[tuple[FoxyConfig, dict]]) -> None:
        if not batch:
            return
        
        cfg = batch[0][0]
        payloads = [item[1] for item in batch]
        
        try:
            headers = {"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"}
            
            # Normalise the endpoint to always be .../v1/logs/batch
            # Strip any trailing slashes and known suffixes, then append the canonical path.
            base = cfg.endpoint.rstrip("/")
            for suffix in ("/v1/logs/batch", "/v1/logs", "/logs/batch", "/logs"):
                if base.endswith(suffix):
                    base = base[: -len(suffix)]
                    break
            endpoint = f"{base}/v1/logs/batch"

            resp = requests.post(endpoint, json=payloads, headers=headers, timeout=cfg.timeout)
            resp.raise_for_status()
        except Exception as exc:
            log.debug("foxy-audit POST failed: %s", exc)

    def flush(self) -> None:
        self._shutdown = True
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)


_DISPATCHER = AsyncDispatcher()

def submit(cfg: FoxyConfig, payload: dict) -> None:
    """Enqueue an ingest job for background delivery."""
    _DISPATCHER.submit(cfg, payload)
