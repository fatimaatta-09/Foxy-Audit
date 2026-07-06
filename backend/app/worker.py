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
from .anchor import _ANCHOR_ALERT_STATE, alert_on_anchor_problems, anchor_all_due
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


def _org_history(db: Session, org_id) -> dict:
    """Compact last-7-day activity summary for the org, fed to the judge for
    temporal reasoning (5J). Cheap aggregate over the usage_daily rollup."""
    oid = org_id if isinstance(org_id, uuid.UUID) else uuid.UUID(str(org_id))
    row = db.execute(text(
        "SELECT COALESCE(SUM(breach_count),0) AS breaches, "
        "       COALESCE(SUM(graded_count),0) AS graded "
        "FROM usage_daily "
        "WHERE org_id = :oid AND day >= CURRENT_DATE - INTERVAL '7 days'"),
        {"oid": oid}).mappings().first()
    breaches, graded = int(row["breaches"]), int(row["graded"])
    return {
        "window_days": 7,
        "recent_breaches": breaches,
        "recent_graded": graded,
        "breach_rate_pct": round(100.0 * breaches / graded, 1) if graded else 0.0,
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
    history = _org_history(db, row["org_id"])
    verdict = gemini.evaluate(meta, policy_config, history=history)
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


class CircuitBreaker:
    """Trips OPEN after `threshold` consecutive systemic failures and stays open
    for `cooldown` seconds (work is skipped). After the cooldown one trial is
    allowed; a success closes + resets it, a failure slides the window forward.
    Keeps the grading poller from hammering a down Gemini / burning attempts (5E.2)."""

    def __init__(self, threshold: int, cooldown: float):
        self.threshold = threshold
        self.cooldown = cooldown
        self.failures = 0
        self.opened_at: float | None = None

    def on_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def on_failure(self, now: float) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = now          # (re)open, sliding the cooldown window

    def allow(self, now: float) -> bool:
        """True if work should proceed (closed, or a half-open trial post-cooldown)."""
        return self.opened_at is None or (now - self.opened_at) >= self.cooldown


def backoff_delay(failures: int, base: float, cap: float) -> float:
    """Capped exponential backoff: base, base, 2·base, 4·base … ≤ cap."""
    if failures <= 0:
        return base
    return min(cap, base * (2 ** (failures - 1)))


def _interruptible_sleep(stopping: dict, seconds: float) -> None:
    """Sleep up to `seconds`, waking promptly when shutdown is requested."""
    waited = 0.0
    while waited < seconds and not stopping["flag"]:
        time.sleep(min(1.0, seconds - waited))
        waited += 1.0


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
            # 7C: page someone if anchors are failing or the chain went stale.
            alert_on_anchor_problems(db, s, _ANCHOR_ALERT_STATE)
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

    # Usage rollups + traffic-partition maintenance run in their own thread so a
    # slow rollup never stalls grading or the liveness heartbeat.
    from .usage import usage_loop
    threading.Thread(target=usage_loop, args=(stopping, s),
                     name="foxy-usage", daemon=True).start()

    # Circuit-breaker so a Gemini outage doesn't hammer the API or burn attempts:
    # when whole batches keep failing, trip open and back off (5E.2).
    breaker = CircuitBreaker(s.grading_breaker_threshold, s.grading_breaker_cooldown)
    while not stopping["flag"]:
        if not breaker.allow(time.time()):
            _interruptible_sleep(stopping, s.grading_poll_interval)   # open → wait out cooldown
            continue
        rows: list = []
        db = SessionLocal()
        try:
            rows = _claim_batch(db, s.grading_batch_size, s.grading_stuck_seconds)
            ok = fail = 0
            for row in rows:
                try:
                    _grade_one(db, row)
                    ok += 1
                except Exception as exc:   # noqa: BLE001 — never let one row kill the loop
                    _handle_failure(db, row, s.grading_max_attempts, exc)
                    fail += 1
            # Liveness heartbeat (busy OR idle) so /health/ready can detect a
            # dead/stuck worker before the grading queue backs up silently.
            db.execute(text("UPDATE worker_heartbeat SET beat_at = now() WHERE id = 1"))
            db.commit()
            if rows:
                # A whole batch failing with none graded => Gemini is likely down
                # (systemic) → trip the breaker; any success closes it.
                if ok == 0 and fail > 0:
                    breaker.on_failure(time.time())
                else:
                    breaker.on_success()
            # NOTE: public-chain anchoring runs in its own thread (_anchor_loop),
            # NOT here — a slow chain RPC must never stall grading or the heartbeat.
        except Exception as exc:           # noqa: BLE001 — claim/DB hiccup: back off
            db.rollback()
            log.warning("poll loop error: %s", exc)
        finally:
            db.close()
        # Idle → poll interval; systemic failures → capped exponential backoff.
        # Interruptible so SIGTERM drains the in-flight batch then exits promptly.
        if not rows:
            _interruptible_sleep(stopping, s.grading_poll_interval)
        elif breaker.failures > 0:
            _interruptible_sleep(
                stopping, backoff_delay(breaker.failures, s.grading_poll_interval, s.grading_max_backoff))

    log.info("Foxy grading poller stopped")
