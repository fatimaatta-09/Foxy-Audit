"""Per-org daily usage rollups + traffic-partition maintenance (Phase 4 #1).

Runs in the worker (a superuser/BYPASSRLS role) so its cross-org aggregate over
audit_logs sees every tenant. Two jobs, both cheap and idempotent:

* ``rollup_recent`` — recompute the last WHOLE DAYS into usage_daily via an
  upsert. audit_logs is append-only and grading settles within minutes, so a
  short window is O(recent rows) rather than O(all rows) each pass, and the
  always-upsert shape makes re-running safe.
* ``backfill`` — the same aggregate with no lower bound, chunked by month. For
  repairing history written by the pre-R3 window; see below.
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

from . import email, email_templates as et
from .config import Settings, get_settings
from .db import SessionLocal

log = logging.getLogger("foxy.usage")

# R3 · Incremental upsert of the last whole days. Reuses the breach expression +
# date_trunc grouping from routers/logs.py. Under the superuser worker role
# app.current_org is unset, so the SELECT sees all orgs and the INSERT's WITH
# CHECK is bypassed.
#
# ⛔ THE WINDOW IS WHOLE DAYS, AND THAT IS THE WHOLE FIX (register #121).
# It used to be `created_at >= now() - interval '2 days'` — a ROLLING window. A
# rolling 48h spans THREE calendar days and its oldest day is a partial slice,
# so every pass wrote a partial count OVER a complete row via ON CONFLICT DO
# UPDATE. At a 300s cadence the FINAL value stored for a day was whatever was
# true in its last few minutes. usage_daily was accurate for today and yesterday
# and badly understated everything older; three readers escaped this table
# rather than fix it (worker._org_history, /v1/usage, and the admin quota
# meter). Anchoring to date_trunc('day', ...) means a day is only ever written
# from a complete day, so a partial can never overwrite a complete row.
#
# ⚠ `- interval '2 days'` is the same NUMBER the broken version used and none of
# its meaning. The fix is `date_trunc('day', ...)`; do not "simplify" the
# boundary back to a bare now().
#
# WIDTH: 2 whole days back = a 3-calendar-day window. One day back is the
# minimum that is correct while the worker runs — a day leaves the window ~5
# minutes after the pass that finalises it. Two days back buys ~48h of worker
# downtime for a measured +4ms per pass (14.8ms -> 18.9ms over 1.5M events,
# every 300s). Beyond that the marginal value falls off: an outage longer than
# 48h is a visible incident (heartbeat, health page, dead-letter alert) and
# `backfill` below is the right remedy, not a permanently wider window.
#
# ⚠ EVERY TIMESTAMP EXPRESSION IS PINNED TO UTC, and it was not before.
# `date_trunc('day', created_at)` evaluates in the SESSION timezone, so the
# stored `day` was whatever calendar day the DB server happened to be in — this
# machine is Asia/Karachi, where an event at 22:30 UTC on the 7th was bucketed
# into the 8th. Nothing pins TimeZone in the app, the engine or compose, while
# ALL SIX readers compute their bounds with datetime.now(timezone.utc).date().
# `AT TIME ZONE 'UTC'` makes the bucket and the window session-invariant and
# makes the readers' assumption true. Verified under two session timezones.
_ROLLUP_SQL = text(
    """
    INSERT INTO usage_daily
        (org_id, day, logs_count, tokens_sum, breach_count,
         graded_count, failed_count, pending_count)
    SELECT
        org_id,
        date_trunc('day', created_at AT TIME ZONE 'UTC')::date AS day,
        count(*),
        coalesce(sum(token_count), 0),
        count(*) FILTER (WHERE gemini_verdict->>'policy_breach' = 'true'),
        count(*) FILTER (WHERE grading_status = 'graded'),
        count(*) FILTER (WHERE grading_status = 'failed'),
        count(*) FILTER (WHERE grading_status = 'pending')
    FROM audit_logs
    WHERE created_at >= (date_trunc('day', now() AT TIME ZONE 'UTC')
                         - interval '2 days') AT TIME ZONE 'UTC'
    GROUP BY org_id, date_trunc('day', created_at AT TIME ZONE 'UTC')::date
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


# R3 · the same aggregate over an ARBITRARY closed range. One statement, one
# month at a time, for repairing the history the rolling window wrote wrong.
# Deliberately the same shape as _ROLLUP_SQL — same columns, same breach
# expression, same UTC bucketing, same ON CONFLICT — so a future change to what
# a "day" contains cannot fix one and miss the other.
_BACKFILL_SQL = text(
    """
    INSERT INTO usage_daily
        (org_id, day, logs_count, tokens_sum, breach_count,
         graded_count, failed_count, pending_count)
    SELECT
        org_id,
        date_trunc('day', created_at AT TIME ZONE 'UTC')::date AS day,
        count(*),
        coalesce(sum(token_count), 0),
        count(*) FILTER (WHERE gemini_verdict->>'policy_breach' = 'true'),
        count(*) FILTER (WHERE grading_status = 'graded'),
        count(*) FILTER (WHERE grading_status = 'failed'),
        count(*) FILTER (WHERE grading_status = 'pending')
    FROM audit_logs
    WHERE created_at >= :lo AND created_at < :hi
    GROUP BY org_id, date_trunc('day', created_at AT TIME ZONE 'UTC')::date
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


# R3 · rows whose (org_id, day) has no ledger events behind it at all.
#
# An upsert can only correct days it recomputes, so it cannot touch a row that
# should not exist. Two ways one appears: the pre-R3 bucketing resolved in the
# SESSION timezone, so re-bucketing to UTC moves an event to the previous day
# and STRANDS the old row; and any row written by hand or by an older shape.
# `platform_stats` sums this table with NO date filter, so one stranded row
# inflates "Audited interactions" on the console forever.
#
# Safe because usage_daily is by definition a projection of audit_logs, and
# audit_logs is append-only — "no events on that day" cannot become false later.
_ORPHAN_SQL = text(
    """
    DELETE FROM usage_daily u
    WHERE NOT EXISTS (
        SELECT 1 FROM audit_logs a
        WHERE a.org_id = u.org_id
          AND date_trunc('day', a.created_at AT TIME ZONE 'UTC')::date = u.day
    );
    """
)


def _month_starts(first: datetime, last: datetime) -> list[datetime]:
    """UTC month boundaries covering [first, last], oldest first."""
    out, cur = [], datetime(first.year, first.month, 1, tzinfo=timezone.utc)
    last = datetime(last.year, last.month, 1, tzinfo=timezone.utc)
    while cur <= last:
        out.append(cur)
        cur = datetime(cur.year + (cur.month == 12), (cur.month % 12) + 1, 1,
                       tzinfo=timezone.utc)
    return out


def backfill(db: Session, *, commit_each: bool = True) -> int:
    """Recompute EVERY usage_daily row from the ledger. Returns months processed.

    Why this exists: fixing the window repairs the future and nothing else. Six
    consumers read this table and two of them read all of history — the platform
    totals behind "Audited interactions" and "Policy breaches" (admin_stats
    lines 75-80) have no date filter at all — so the console's two headline
    numbers stay wrong until the stored rows are recomputed.

    audit_logs is append-only, so every damaged row is exactly recomputable and
    this is idempotent: running it twice writes the same values.

    ⚠ MUST RUN AFTER THE WINDOW FIX IS DEPLOYED. Under the old rolling window
    the worker would re-damage each repaired day the moment it left the window.

    CHUNKED BY UTC MONTH so the work is bounded and observable rather than one
    statement over the whole ledger. Measured on 1.5M events across 120 days and
    40 orgs: 188ms per month, 660ms for the lot, Index Only Scan with
    Heap Fetches: 0 on ix_audit_logs_org_created throughout.
    """
    bounds = db.execute(
        text("SELECT min(created_at), max(created_at) FROM audit_logs")
    ).first()
    if not bounds or bounds[0] is None:
        return 0
    months = _month_starts(bounds[0], bounds[1])
    for i, lo in enumerate(months):
        hi = months[i + 1] if i + 1 < len(months) else (
            datetime(lo.year + (lo.month == 12), (lo.month % 12) + 1, 1,
                     tzinfo=timezone.utc))
        db.execute(_BACKFILL_SQL, {"lo": lo, "hi": hi})
        if commit_each:
            db.commit()
        log.info("usage backfill: %s recomputed", lo.date().isoformat())
    # Last, and only after every month is correct: a row is an orphan relative
    # to the WHOLE ledger, so deleting per-chunk would drop days the next chunk
    # was about to write.
    dropped = db.execute(_ORPHAN_SQL).rowcount or 0
    db.commit()
    if dropped:
        log.info("usage backfill: dropped %d row(s) with no ledger behind them",
                 dropped)
    return len(months)


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
    html, plain = et.layout(
        title="Grading failures need attention",
        preheader=f"{count} interaction(s) are parked in the grading dead-letter.",
        blocks=[
            et.paragraph(f"{count} interaction(s) are parked in grading_status='failed' — the "
                         "Gemini judge exhausted all retries."),
            et.muted("Check the worker logs and the affected org's policy/config."),
        ],
        surface="staff", variant="compact",
    )
    ok = email.send_email(
        to=settings.alert_email,
        subject=f"[Foxy Audit] {count} grading failure(s) need attention",
        html=html, text=plain,
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
