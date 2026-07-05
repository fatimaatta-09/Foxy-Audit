"""Per-org daily usage rollups + traffic-partition maintenance (Phase 4 #1).

Runs in the worker (a superuser/BYPASSRLS role) so its cross-org aggregate over
audit_logs sees every tenant. Two jobs, both cheap and idempotent:

* ``rollup_recent`` — recompute ONLY today + yesterday into usage_daily via an
  upsert. Everything older is immutable (audit_logs is append-only and grading
  settles within minutes), so a 2-day window is correct and O(recent rows) rather
  than O(all rows) each pass. The always-upsert shape makes widening the window or
  re-running safe.
* ``maintain_partitions`` — pre-create next month's traffic_events partition and
  DROP partitions whose entire range is past the retention window (an instant DROP,
  not a vacuum-storming DELETE on a hot table).
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from . import email
from .config import Settings, get_settings
from .db import SessionLocal

log = logging.getLogger("foxy.usage")

# Incremental upsert of the last 2 days. Reuses the breach expression + date_trunc
# grouping from routers/logs.py. Under the superuser worker role app.current_org is
# unset, so the SELECT sees all orgs and the INSERT's WITH CHECK is bypassed.
_ROLLUP_SQL = text(
    """
    INSERT INTO usage_daily
        (org_id, day, logs_count, tokens_sum, breach_count,
         graded_count, failed_count, pending_count)
    SELECT
        org_id,
        date_trunc('day', created_at)::date AS day,
        count(*),
        coalesce(sum(token_count), 0),
        count(*) FILTER (WHERE gemini_verdict->>'policy_breach' = 'true'),
        count(*) FILTER (WHERE grading_status = 'graded'),
        count(*) FILTER (WHERE grading_status = 'failed'),
        count(*) FILTER (WHERE grading_status = 'pending')
    FROM audit_logs
    WHERE created_at >= (now() - interval '2 days')
    GROUP BY org_id, date_trunc('day', created_at)::date
    ON CONFLICT (org_id, day) DO UPDATE SET
        logs_count   = EXCLUDED.logs_count,
        tokens_sum   = EXCLUDED.tokens_sum,
        breach_count = EXCLUDED.breach_count,
        graded_count = EXCLUDED.graded_count,
        failed_count = EXCLUDED.failed_count,
        pending_count= EXCLUDED.pending_count,
        computed_at  = now();
    """
)

# Ensure next month's traffic partition exists (idempotent). Bounds computed in-DB.
_ENSURE_NEXT_PARTITION_SQL = text(
    """
    DO $$
    DECLARE
        nxt  date := (date_trunc('month', now()) + interval '1 month')::date;
        nxt2 date := (date_trunc('month', now()) + interval '2 month')::date;
    BEGIN
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS %I PARTITION OF traffic_events '
            'FOR VALUES FROM (%L) TO (%L)',
            'traffic_events_' || to_char(nxt, 'YYYY_MM'), nxt, nxt2);
    END $$;
    """
)

# Existing monthly partitions (excludes the DEFAULT partition), strictly named.
_LIST_PARTITIONS_SQL = text(
    """
    SELECT c.relname
    FROM pg_inherits i
    JOIN pg_class c ON c.oid = i.inhrelid
    JOIN pg_class p ON p.oid = i.inhparent
    WHERE p.relname = 'traffic_events'
      AND c.relname ~ '^traffic_events_[0-9]{4}_[0-9]{2}$'
    """
)


def rollup_recent(db: Session) -> None:
    db.execute(_ROLLUP_SQL)
    db.commit()


def maintain_partitions(db: Session, retention_days: int) -> None:
    db.execute(_ENSURE_NEXT_PARTITION_SQL)
    db.commit()

    cutoff = date.today() - timedelta(days=retention_days)
    names = db.execute(_LIST_PARTITIONS_SQL).scalars().all()
    for name in names:
        try:
            _, _, y, m = name.split("_")           # traffic_events_YYYY_MM
            year, month = int(y), int(m)
        except ValueError:                          # pragma: no cover — regex-guarded
            continue
        # Exclusive upper bound of this month's partition = first day of next month.
        part_end = date(year + (month == 12), (month % 12) + 1, 1)
        if part_end <= cutoff:
            # name is regex-validated in SQL above, so the interpolation is safe.
            db.execute(text(f'DROP TABLE IF EXISTS "{name}"'))
            log.info("dropped expired traffic partition %s", name)
    db.commit()


def purge_admin_actions(db: Session, retention_days: int) -> int:
    """Delete admin_actions rows older than the retention window (the table is
    append-only and would otherwise grow unbounded). System-only — runs in the
    worker under a superuser role; staff can never DELETE from it. Returns the
    number of rows removed."""
    result = db.execute(
        text("DELETE FROM admin_actions "
             "WHERE created_at < now() - make_interval(days => :days)"),
        {"days": retention_days},
    )
    db.commit()
    return result.rowcount or 0


# In-memory last-alert clock for the grading dead-letter check (per worker process).
_ALERT_STATE: dict = {}


def alert_on_grading_failures(db: Session, settings, state: dict, *,
                              now: float | None = None) -> bool:
    """If failed (dead-letter) grading rows are at/over the threshold, log a
    WARNING and — at most once per cooldown — email settings.alert_email. Returns
    True iff an email was sent. `state` carries the last-alert time across calls."""
    now = time.time() if now is None else now
    count = db.execute(
        text("SELECT count(*) FROM audit_logs WHERE grading_status = 'failed'")
    ).scalar() or 0
    if count < settings.grading_failure_alert_threshold:
        return False
    log.warning("grading dead-letter: %d row(s) parked in grading_status='failed'", count)
    last = state.get("last_alert")   # None = never alerted → don't apply cooldown
    if not settings.alert_email or (
            last is not None and (now - last) < settings.grading_failure_alert_cooldown):
        return False
    ok = email.send_email(
        to=settings.alert_email,
        subject=f"[Foxy Audit] {count} grading failure(s) need attention",
        html=(f"<p><b>{count}</b> interaction(s) are parked in "
              f"grading_status='failed' — the Gemini judge exhausted all retries. "
              f"Check the worker logs and the affected org's policy/config.</p>"),
        text=(f"{count} grading failures are parked in the dead-letter "
              f"(grading_status='failed'). Investigate the worker."),
    )
    if ok:
        state["last_alert"] = now
    return ok


def run_once(db: Session, settings: Settings | None = None) -> None:
    """One rollup + partition-maintenance + retention + dead-letter-alert pass."""
    settings = settings or get_settings()
    rollup_recent(db)
    if settings.traffic_tracking_enabled:
        maintain_partitions(db, settings.traffic_retention_days)
    purge_admin_actions(db, settings.admin_actions_retention_days)
    alert_on_grading_failures(db, settings, _ALERT_STATE)


def usage_loop(stopping: dict, s: Settings) -> None:
    """Rollup + partition maintenance on an interval, in its OWN thread + session
    (kept off the grading poll so neither can stall the other). Mirrors
    worker._anchor_loop."""
    log.info("Usage rollup ON (interval=%ss retention=%sd)",
             s.usage_rollup_interval, s.traffic_retention_days)
    while not stopping["flag"]:
        db = SessionLocal()
        try:
            run_once(db, s)
        except Exception as exc:               # noqa: BLE001 — a bad pass must not kill the thread
            log.warning("usage loop error: %s", exc)
        finally:
            db.close()
        waited = 0.0
        while waited < s.usage_rollup_interval and not stopping["flag"]:
            time.sleep(min(1.0, s.usage_rollup_interval - waited))
            waited += 1.0
