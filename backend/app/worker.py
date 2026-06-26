"""Background Gemini grading worker — decouples AI evaluation from the HTTP path.

The ingest route (`POST /v1/logs`) writes the chain row immediately and returns
`202 Accepted` with `verdict=null, status="pending"`.  It then enqueues the
row's ID + metadata here.  A single daemon thread drains the queue, calls
`gemini.evaluate()`, and patches the verdict back into the row via its own
DB session.

Design constraints:
  • The hash chain is NEVER delayed — it is written inline before this worker
    touches the row.
  • If Gemini is down the row already exists (fail-open); the verdict stays null
    and can be back-filled later.
  • The worker opens its own short-lived session per job so it doesn't hold a
    long-running transaction or block the ingest path's FOR UPDATE lock.
"""

from __future__ import annotations

import logging
import queue
import threading
import uuid

from sqlalchemy import update
from sqlalchemy.orm import Session

from . import gemini
from .db import SessionLocal
from .models import AuditLog

log = logging.getLogger("foxy.worker")

_SENTINEL = None  # pushed to signal graceful shutdown


class GeminiWorker:
    def __init__(self) -> None:
        self._q: "queue.Queue[tuple[uuid.UUID, dict] | None]" = queue.Queue()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, name="foxy-gemini-worker", daemon=True
        )
        self._thread.start()
        log.info("Gemini background worker started")

    def stop(self) -> None:
        self._q.put(_SENTINEL)

    def submit(self, log_id: uuid.UUID, metadata: dict) -> None:
        """Enqueue a grading job.  Called from the ingest route after commit."""
        self._q.put((log_id, metadata))

    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is _SENTINEL:
                log.info("Gemini worker shutting down")
                break
            log_id, metadata = item
            try:
                self._grade(log_id, metadata)
            except Exception as exc:
                log.warning("gemini worker error for %s: %s", log_id, exc)
            finally:
                self._q.task_done()

    def _grade(self, log_id: uuid.UUID, metadata: dict) -> None:
        verdict = gemini.evaluate(metadata)
        db: Session = SessionLocal()
        try:
            db.execute(
                update(AuditLog)
                .where(AuditLog.id == log_id)
                .values(gemini_verdict=verdict.model_dump())
            )
            db.commit()
            log.debug("graded %s → breach=%s score=%s", log_id, verdict.policy_breach, verdict.risk_score)
        finally:
            db.close()


# Module-level singleton — started once from main.py's lifespan.
worker = GeminiWorker()
