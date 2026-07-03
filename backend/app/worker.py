"""Durable Gemini grading worker — a Postgres-outbox poller.

Ingest (POST /v1/logs/batch) commits each chain row synchronously with
grading_status='pending' (the column default). That commit IS the durable
enqueue: the job lives in the same row as the chain data, so a crash between the
202 response and grading cannot lose it — unlike the previous in-memory pool.

This poller claims 'pending' rows with FOR UPDATE SKIP LOCKED (safe to run in
several processes at once), grades each with the org's configured policy via
gemini.evaluate(meta, policy_config), and writes the verdict back, marking the
row 'graded'. Stuck 'in_progress' rows (a worker died mid-job) are reclaimed
after grading_stuck_seconds; after grading_max_attempts failures a row is parked
in 'failed' — a human-visible dead-letter, never silently dropped.

RLS note: the poller must connect as a role that BYPASSES RLS (the docker 'foxy'
superuser does) so its cross-org claim query sees every org's rows; the per-row
write-back also sets app.current_org defensively.
"""

from __future__ import annotations

import json
import logging
import signal
import threading
import time
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from . import gemini
from .anchor import anchor_all_due
from .config import get_settings
from .db import SessionLocal
from .models import OrgPolicy

log = logging.getLogger("foxy.worker")

# Atomically claim a batch: mark pending (or stuck in_progress) rows in_progress
# and return their metadata. FOR UPDATE SKIP LOCKED lets multiple pollers run.
_CLAIM_SQL = text(
    """
    UPDATE audit_logs
       SET grading_status     = 'in_progress',
           grading_started_at = now(),
           grading_attempts   = grading_attempts + 1
     WHERE id IN (
           SELECT id FROM audit_logs
            WHERE grading_status = 'pending'
               OR (grading_status = 'in_progress'
                   AND grading_started_at < now() - make_interval(secs => :stuck))
            ORDER BY created_at
              FOR UPDATE SKIP LOCKED
            LIMIT :batch
     )
    RETURNING id, org_id, prompt_hash, response_hash,
              token_count, policy_tag, pii_signals, grading_attempts
    """
)

_MARK_GRADED_SQL = text(
    """
    UPDATE audit_logs
       SET gemini_verdict = CAST(:verdict AS jsonb),
           grading_status  = 'graded',
           graded_at       = now()
     WHERE id = :id
    """
)
_MARK_RETRY_SQL = text("UPDATE audit_logs SET grading_status = 'pending' WHERE id = :id")
_MARK_FAILED_SQL = text("UPDATE audit_logs SET grading_status = 'failed' WHERE id = :id")


def _policy_config(db: Session, org_id) -> dict | None:
    """Load the org's policy flags so the judge enforces what they configured."""
    oid = org_id if isinstance(org_id, uuid.UUID) else uuid.UUID(str(org_id))
    policy = db.get(OrgPolicy, oid)
    if policy is None:
        return None
    return {
        "pii_detection": policy.pii_detection,
        "prompt_injection": policy.prompt_injection,
        "regulated_data_mode": policy.regulated_data_mode,
        "max_token_threshold": policy.max_token_threshold,
    }


def _claim_batch(db: Session, batch: int, stuck: int) -> list:
    """Claim up to `batch` rows for grading; returns their metadata mappings."""
    rows = db.execute(_CLAIM_SQL, {"batch": batch, "stuck": stuck}).mappings().all()
    db.commit()   # release the row locks before the (possibly slow) Gemini call
    return list(rows)


def _grade_one(db: Session, row) -> None:
    """Grade one claimed row with its org's policy and persist the verdict."""
    meta = {
        "prompt_hash": row["prompt_hash"],
        "response_hash": row["response_hash"],
        "token_count": row["token_count"],
        "policy_tag": row["policy_tag"],
        "pii_signals": row["pii_signals"],
    }
    policy_config = _policy_config(db, row["org_id"])
    verdict = gemini.evaluate(meta, policy_config)
    # Scope RLS to this row's org before the write-back (no-op under a
    # BYPASSRLS/superuser role, required under a hardened one).
    db.execute(text("SELECT set_config('app.current_org', :oid, true)"),
               {"oid": str(row["org_id"])})
    db.execute(_MARK_GRADED_SQL,
               {"id": row["id"], "verdict": json.dumps(verdict.model_dump())})
    db.commit()


def _handle_failure(db: Session, row, max_attempts: int, exc: Exception) -> None:
    db.rollback()
    attempts = row["grading_attempts"] or 0
    sql = _MARK_FAILED_SQL if attempts >= max_attempts else _MARK_RETRY_SQL
    try:
        db.execute(sql, {"id": row["id"]})
        db.commit()
    except Exception:
        db.rollback()
    log.warning("grading %s for %s (attempt %s): %s",
                "FAILED (dead-letter)" if attempts >= max_attempts else "retry",
                row["id"], attempts, exc)


def _anchor_loop(stopping: dict, s) -> None:
    """Anchor each org's chain head on an interval, in its OWN thread and DB
    session. Kept off the grading poll loop so a slow chain RPC (an EVM receipt
    wait can take many seconds) can never starve grading or delay the liveness
    heartbeat — 'anchoring never blocks ingest/grading'."""
    log.info("Public-chain anchoring ON (provider=%s interval=%ss)",
             s.anchor_provider, s.anchor_interval_seconds)
    while not stopping["flag"]:
        db = SessionLocal()
        try:
            n = anchor_all_due(db, s)
            if n:
                log.info("anchored %s org(s)", n)
        except Exception as exc:               # noqa: BLE001 — a bad sweep must not kill the thread
            log.warning("anchor loop error: %s", exc)
        finally:
            db.close()
        # Interruptible sleep so shutdown is prompt even on a long interval.
        waited = 0.0
        while waited < s.anchor_interval_seconds and not stopping["flag"]:
            time.sleep(min(1.0, s.anchor_interval_seconds - waited))
            waited += 1.0


def run_forever() -> None:
    """Poll the outbox until interrupted. Entry point for `app.worker_main`."""
    s = get_settings()
    log.info("Foxy grading poller started (interval=%ss batch=%s)",
             s.grading_poll_interval, s.grading_batch_size)

    stopping = {"flag": False}

    def _stop(*_):
        stopping["flag"] = True

    try:
        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)
    except ValueError:
        pass   # not the main thread (e.g. a lifespan-daemon fallback)

    if s.anchor_enabled:
        threading.Thread(target=_anchor_loop, args=(stopping, s),
                         name="foxy-anchor", daemon=True).start()

    while not stopping["flag"]:
        rows: list = []
        db = SessionLocal()
        try:
            rows = _claim_batch(db, s.grading_batch_size, s.grading_stuck_seconds)
            for row in rows:
                try:
                    _grade_one(db, row)
                except Exception as exc:   # noqa: BLE001 — never let one row kill the loop
                    _handle_failure(db, row, s.grading_max_attempts, exc)
            # Liveness heartbeat (busy OR idle) so /health/ready can detect a
            # dead/stuck worker before the grading queue backs up silently.
            db.execute(text("UPDATE worker_heartbeat SET beat_at = now() WHERE id = 1"))
            db.commit()
            # NOTE: public-chain anchoring runs in its own thread (_anchor_loop),
            # NOT here — a slow chain RPC must never stall grading or the heartbeat.
        except Exception as exc:           # noqa: BLE001 — claim/DB hiccup: back off
            db.rollback()
            log.warning("poll loop error: %s", exc)
        finally:
            db.close()
        if not rows:
            time.sleep(s.grading_poll_interval)

    log.info("Foxy grading poller stopped")
