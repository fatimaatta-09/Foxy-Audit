"""usage_daily: per-org daily rollup of audit_logs + one-time backfill (Phase 4 #1)

Admin dashboards read aggregates from here instead of scanning the raw ledger.
Populated incrementally by the worker (app/usage.py); this migration backfills all
history once. RLS-scoped by org_id like audit_logs (a customer reads its own quota
widget; staff read all) — keeping the aggregate no broader than its source.

The backfill runs BEFORE RLS is enabled on this table so a single multi-org INSERT
isn't blocked by the WITH CHECK clause. (The SELECT over audit_logs still relies on
the migration role bypassing RLS — the docker 'foxy' superuser does.)

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usage_daily",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("logs_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_sum", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("breach_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("graded_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pending_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("org_id", "day", name="uq_usage_org_day"),
    )
    op.create_index("ix_usage_daily_org", "usage_daily", ["org_id"])
    op.create_index("ix_usage_daily_day", "usage_daily", ["day"])

    # One-time backfill over all existing audit_logs (before RLS is enabled here).
    op.execute(
        """
        INSERT INTO usage_daily
            (org_id, day, logs_count, tokens_sum, breach_count,
             graded_count, failed_count, pending_count)
        SELECT
            org_id,
            date_trunc('day', created_at)::date                                    AS day,
            count(*),
            coalesce(sum(token_count), 0),
            count(*) FILTER (WHERE gemini_verdict->>'policy_breach' = 'true'),
            count(*) FILTER (WHERE grading_status = 'graded'),
            count(*) FILTER (WHERE grading_status = 'failed'),
            count(*) FILTER (WHERE grading_status = 'pending')
        FROM audit_logs
        GROUP BY org_id, date_trunc('day', created_at)::date
        ON CONFLICT (org_id, day) DO NOTHING;
        """
    )

    op.execute("ALTER TABLE usage_daily ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE usage_daily FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY org_isolation ON usage_daily
            USING      (org_id = current_setting('app.current_org', true)::uuid)
            WITH CHECK (org_id = current_setting('app.current_org', true)::uuid);
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS org_isolation ON usage_daily;")
    op.drop_index("ix_usage_daily_day", table_name="usage_daily")
    op.drop_index("ix_usage_daily_org", table_name="usage_daily")
    op.drop_table("usage_daily")
