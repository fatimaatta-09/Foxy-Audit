"""recompute usage_daily from the ledger — repair for the rolling rollup window

R3 · register #121. The window fix in `app/usage.py` repairs the future and
nothing else; this repairs what is already stored.

WHAT WAS WRONG
--------------
`_ROLLUP_SQL` selected `created_at >= now() - interval '2 days'` — a ROLLING
window — and upserted with ON CONFLICT DO UPDATE. A rolling 48h spans THREE
calendar days and its oldest day is a partial slice, so every pass wrote a
partial count OVER a complete row. At a 300s cadence the FINAL value stored for
any day was whatever was true in its last few minutes. usage_daily was accurate
for today and yesterday and badly understated everything older.

WHY A BACKFILL IS NOT OPTIONAL
------------------------------
Six consumers read this table and two read ALL of history with no date filter:
`admin_stats.platform_stats` sums logs_count and breach_count for "Audited
interactions" and "Policy breaches" — the two headline KPI cards on the console
overview. Those numbers stay wrong forever unless the stored rows are recomputed.

audit_logs is append-only, so every damaged row is exactly recomputable and this
is idempotent: running it twice writes the same values.

WHY A MIGRATION AND NOT A SCRIPT
--------------------------------
Ordering. The backfill MUST run after the window fix is deployed, or the old
rollup re-damages each repaired day as it leaves the window. In
`deploy/docker-compose.prod.yml` the `foxy-migrate` service runs `alembic upgrade
head` and exits, and BOTH `foxy-backend` and the worker wait on
`service_completed_successfully` — its own comment says "Run migrations to head,
then exit. Everything below waits for this." So a migration is guaranteed to
finish before the worker's first pass, with no human step to forget and no window
in which the two can interleave. A script has no equally safe moment.

COST, MEASURED
--------------
On 1.5M events across 120 days and 40 organisations: 188ms per month-chunk,
660ms for the whole ledger, an Index Only Scan on ix_audit_logs_org_created with
Heap Fetches: 0 throughout. Chunked by UTC month so the work is bounded and
observable rather than one statement over the entire table.

⚠ THE SQL IS INLINED RATHER THAN IMPORTED FROM app.usage. A migration is a
historical record and must keep doing what it did on the day it ran; importing
live application code makes it change under later refactors. The duplication
cannot drift in the way that normally matters, because this runs exactly once.

⚠ EVERY TIMESTAMP EXPRESSION IS PINNED TO UTC, matching the fixed rollup.
`date_trunc('day', created_at)` evaluates in the SESSION timezone, so the old
`day` was whatever calendar day the DB server sat in; all six readers compute
their bounds with `datetime.now(timezone.utc).date()`. This rewrites every row
under UTC bucketing, so the table and its readers finally agree.
"""
from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


_BACKFILL = """
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

# Rows whose (org_id, day) has no ledger events at all. An upsert can only
# correct days it recomputes, so it cannot remove a row that should not exist.
# The pre-R3 bucketing resolved in the SESSION timezone, so re-bucketing to UTC
# moves an event to the previous day and STRANDS the old row — and
# `platform_stats` sums this table with NO date filter, so one stranded row
# inflates the console's headline number forever. Caught by running this
# migration against damaged data and finding it 1 short of the ledger.
_ORPHANS = """
DELETE FROM usage_daily u
WHERE NOT EXISTS (
    SELECT 1 FROM audit_logs a
    WHERE a.org_id = u.org_id
      AND date_trunc('day', a.created_at AT TIME ZONE 'UTC')::date = u.day
);
"""

# One chunk per UTC month. `AT TIME ZONE 'UTC'` twice on purpose: the inner one
# takes the timestamptz to UTC wall time so date_trunc lands on a UTC month
# boundary, the outer one takes that naive timestamp back to a timestamptz
# meaning that same instant. Without the second, the bounds would be naive and
# the `created_at >= :lo` comparison would re-interpret them in the SESSION
# timezone — the exact skew this migration exists to remove.
_MONTHS = """
SELECT generate_series(
         date_trunc('month', min(created_at) AT TIME ZONE 'UTC') AT TIME ZONE 'UTC',
         date_trunc('month', max(created_at) AT TIME ZONE 'UTC') AT TIME ZONE 'UTC',
         interval '1 month')
FROM audit_logs
"""


def upgrade() -> None:
    from datetime import datetime, timezone

    from sqlalchemy import text

    conn = op.get_bind()
    months = [r[0] for r in conn.execute(text(_MONTHS)) if r[0] is not None]
    stmt = text(_BACKFILL)
    for lo in months:
        hi = datetime(lo.year + (lo.month == 12), (lo.month % 12) + 1, 1,
                      tzinfo=timezone.utc)
        conn.execute(stmt, {"lo": lo, "hi": hi})
    # After every month, never per-chunk: a row is an orphan relative to the
    # whole ledger, and sweeping early would delete days a later chunk fills.
    conn.execute(text(_ORPHANS))


def downgrade() -> None:
    """No down path, deliberately.

    There is nothing to restore TO: the values this replaces were wrong, and the
    ledger they were recomputed from is append-only, so re-deriving the damaged
    numbers is neither possible nor desirable. A downgrade that reverted the
    rollup window without this would simply start re-damaging the table again.
    """
