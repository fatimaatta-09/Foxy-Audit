"""traffic_events: partitioned in/out request tracking (Phase 4 #1)

The "all traffic in/out" feed for the admin site. RANGE-partitioned by created_at
so retention is an instant DROP of an old partition (not a vacuum-storming DELETE
on a hot table) and time-bounded admin queries prune to a small partition.

Platform-only, NO RLS: org_id is nullable (anonymous marketing traffic), which
can never satisfy the org_isolation predicate, and the table is read only by staff.

A DEFAULT partition guarantees an insert never fails if monthly-partition creation
lags; the worker (app/usage.py partition maintenance) pre-creates next month and
drops partitions past the retention window. The partition key must be part of the
PK, so the PK is (id, created_at).

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-03
"""
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE traffic_events (
            id           UUID NOT NULL DEFAULT gen_random_uuid(),
            site         VARCHAR(16) NOT NULL,
            path         VARCHAR(512) NOT NULL,
            method       VARCHAR(8) NOT NULL,
            status_code  SMALLINT NOT NULL,
            latency_ms   INTEGER,
            direction    VARCHAR(3) NOT NULL DEFAULT 'in',
            org_id       UUID REFERENCES organizations(id),
            user_id      UUID REFERENCES users(id),
            staff_id     UUID REFERENCES staff_users(id),
            ip_hash      VARCHAR(64),
            ua_hash      VARCHAR(64),
            referrer     VARCHAR(512),
            visitor_id   UUID,
            session_id   UUID,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT traffic_events_pkey PRIMARY KEY (id, created_at),
            CONSTRAINT ck_traffic_site CHECK (site IN ('marketing','app','admin')),
            CONSTRAINT ck_traffic_direction CHECK (direction IN ('in','out'))
        ) PARTITION BY RANGE (created_at);
        """
    )

    # Default partition: nothing is ever lost even before a monthly partition exists.
    op.execute("CREATE TABLE traffic_events_default PARTITION OF traffic_events DEFAULT;")

    # Pre-create this month + next month so pruning helps immediately. Bounds are
    # computed in-DB (now()) — the running app never sees a missing current month.
    op.execute(
        """
        DO $$
        DECLARE
            m0 date := date_trunc('month', now())::date;
            m1 date := (date_trunc('month', now()) + interval '1 month')::date;
            m2 date := (date_trunc('month', now()) + interval '2 month')::date;
        BEGIN
            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS %I PARTITION OF traffic_events '
                'FOR VALUES FROM (%L) TO (%L)',
                'traffic_events_' || to_char(m0, 'YYYY_MM'), m0, m1);
            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS %I PARTITION OF traffic_events '
                'FOR VALUES FROM (%L) TO (%L)',
                'traffic_events_' || to_char(m1, 'YYYY_MM'), m1, m2);
        END $$;
        """
    )

    # Indexes on the parent propagate to every partition. Named for the exact admin
    # queries; partials keep the error/funnel indexes tiny. path is NOT indexed.
    op.execute(
        "CREATE INDEX ix_traffic_site_created ON traffic_events (site, created_at DESC);")
    op.execute(
        "CREATE INDEX ix_traffic_org_created ON traffic_events (org_id, created_at DESC) "
        "WHERE org_id IS NOT NULL;")
    op.execute(
        "CREATE INDEX ix_traffic_status_created ON traffic_events (status_code, created_at DESC) "
        "WHERE status_code >= 400;")
    op.execute(
        "CREATE INDEX ix_traffic_visitor ON traffic_events (visitor_id, created_at) "
        "WHERE visitor_id IS NOT NULL;")


def downgrade() -> None:
    # Dropping the partitioned parent drops all child partitions + indexes.
    op.execute("DROP TABLE IF EXISTS traffic_events;")
