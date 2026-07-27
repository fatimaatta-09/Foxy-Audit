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
import hashlib

from sqlalchemy import text
from sqlalchemy.orm import Session


from . import gemini
from . import judge
from . import judge_routing
from . import org_notifications
from . import openai_judge
from . import policy_engine
from . import user_notifications
from . import webhook_delivery
from .anchor import _ANCHOR_ALERT_STATE, alert_on_anchor_problems, anchor_all_due
from .config import get_settings
from .db import SessionLocal
from .models import AuditEvent, OrgPolicy
from .policy_snapshot import judge_policy_config, policy_snapshot_hash

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
    RETURNING id, org_id, seq, prompt_hash, response_hash,
              token_count, policy_tag, pii_signals, grading_attempts,
              chain_hash,
              event_id, client_id, client_seq, event_type, commitment_alg,
              event_metadata
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


def _policy_config(db: Session, org_id, event_metadata: dict | None = None) -> dict | None:
    """Use an event's bound policy snapshot, with a legacy-row fallback."""
    metadata = event_metadata or {}
    snapshot = metadata.get("policy_snapshot")
    stored_hash = metadata.get("policy_snapshot_hash")
    if snapshot is not None or stored_hash is not None:
        if not isinstance(snapshot, dict) or not isinstance(stored_hash, str):
            raise ValueError("policy snapshot metadata is incomplete")
        if policy_snapshot_hash(snapshot) != stored_hash:
            raise ValueError("policy snapshot hash does not match stored policy")
        config = judge_policy_config(snapshot)
        if config is None:
            raise ValueError("policy snapshot metadata is invalid")
        return config

    # Rows written before chain V3 did not retain policy snapshots. Preserve
    # their legacy behavior rather than rewriting historical evidence.
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


def _judge_verdict(db: Session, org_id, meta: dict, policy_config: dict | None,
                   history: dict):
    """Grade with the judge(s) THIS org chose, on the key THEY pay for.

    Routing comes from the org's LIVE OrgPolicy row (judge_routing), never from
    the event's policy snapshot — a provider key must never touch the chain. A
    chosen provider with no usable key is skipped with the provider's own
    evaluator_unavailable verdict, so grading degrades honestly instead of
    crashing or quietly falling back to Foxy's platform key.

    Decrypted keys live only in this frame, for the duration of the call.
    """
    routing = judge_routing.resolve_judge_routing(db, org_id)
    verdicts = []
    if routing.uses_gemini:
        verdicts.append(
            gemini.evaluate(meta, policy_config, history=history,
                            api_key=routing.gemini_key)
            if routing.can_call("gemini")
            else gemini._fallback(routing.problems.get("gemini", "no_api_key")))
    if routing.uses_openai:
        verdicts.append(
            openai_judge.evaluate(meta, policy_config, history=history,
                                  api_key=routing.openai_key)
            if routing.can_call("openai")
            else openai_judge._fallback(routing.problems.get("openai", "no_api_key")))
    if len(verdicts) == 2:
        return judge.combine(verdicts[0], verdicts[1])
    return verdicts[0]


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
        "event_id": str(row["event_id"]) if row.get("event_id") else None,
        "event_type": row.get("event_type"),
        "commitment_alg": row.get("commitment_alg"),
        "event_metadata": row.get("event_metadata"),
    }
    if row.get("event_type") in policy_engine.ENFORCEMENT_EVENT_TYPES:
        # Terminal & locally decided (blocked/redacted): the host already enforced
        # policy and there is no model response to grade. Never call the judge —
        # write a deterministic verdict from the enforcement labels instead.
        verdict = policy_engine.evaluate_enforcement(meta)
    else:
        policy_config = _policy_config(db, row["org_id"], row.get("event_metadata"))
        history = _org_history(db, row["org_id"])
        verdict = _judge_verdict(db, row["org_id"], meta, policy_config, history)
        # Never persist a self-contradictory or empty judge answer as a confident
        # grade — quarantine it as evaluator_unknown to keep the audit report honest.
        verdict = judge.validate(verdict)
        if verdict.decision == "unknown" and verdict.reason.startswith("evaluator_unavailable"):
            verdict = policy_engine.evaluate(meta, policy_config)
    # Scope RLS to this row's org before the write-back (no-op under a
    # BYPASSRLS/superuser role, required under a hardened one).
    db.execute(text("SELECT set_config('app.current_org', :oid, true)"),
               {"oid": str(row["org_id"])})
    db.execute(_MARK_GRADED_SQL,
               {"id": row["id"], "verdict": json.dumps(verdict.model_dump())})
    payload = verdict.model_dump()
    event_blob = json.dumps({"audit_log_id": str(row["id"]),
                             "event_type": "verdict", "payload": payload,
                             "parent_chain_hash": row.get("chain_hash", "")},
                            sort_keys=True, separators=(",", ":"))
    db.add(AuditEvent(
        org_id=row["org_id"], audit_log_id=row["id"], event_type="verdict",
        payload=payload, event_hash=hashlib.sha256(event_blob.encode()).hexdigest(),
    ))
    db.commit()
    # Fire the breach notifier AFTER the verdict is durably committed, so a notify
    # failure can never lose the grade. Best-effort — never raises into grading.
    if verdict.policy_breach:
        # BOTH breach paths QUEUE only — no provider call happens inside the
        # grading batch. The org-level notice used to be sent inline here: a
        # synchronous send_email plus a 5 s webhook POST in the loop that also
        # drives the worker heartbeat, so a hanging mail provider could stall
        # grading and take /health/ready down with it.
        org_notifications.enqueue_breach_notice(row, verdict)
        # Per-user fan-out (D-S): one email per opted-in seat.
        user_notifications.enqueue_breach_alert(row, verdict)
    # Outbound webhook subscriptions (P3 §F): deliver a signed 'graded' (and
    # 'breach') event to each matching subscription. Best-effort, content-blind.
    try:
        webhook_delivery.deliver_grading(
            db, row["org_id"], is_breach=bool(verdict.policy_breach),
            payload={"seq": row.get("seq"), "policy_tag": row.get("policy_tag"),
                     "decision": verdict.decision, "risk_score": verdict.risk_score,
                     "chain_hash": row.get("chain_hash")})
    except Exception as exc:                 # noqa: BLE001 — delivery never breaks grading
        log.warning("webhook delivery error: %s", exc)


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

    # Org-policy breach notices, own thread + session. NOT gated on
    # user_notifications_enabled: that switch governs the per-user preference
    # fan-out, and using it to also silence a tenant's configured policy notice
    # would disable a paid feature by accident.
    from .org_notifications import org_notifications_loop
    threading.Thread(target=org_notifications_loop, args=(stopping, s),
                     name="foxy-org-notifications", daemon=True).start()

    # Per-user breach alerts + weekly digest + key-rotation reminders (D-S), own
    # thread + session so a slow mail provider never stalls grading.
    if s.user_notifications_enabled:
        from .user_notifications import user_notifications_loop
        threading.Thread(target=user_notifications_loop, args=(stopping, s),
                         name="foxy-user-notifications", daemon=True).start()

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
