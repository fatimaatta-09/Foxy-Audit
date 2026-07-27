"""audit_logs (org_id, created_at) index for the per-day usage aggregate

GET /v1/usage now groups audit_logs by day instead of reading the usage_daily
rollup, which only recomputed a rolling 48-hour window and so understated any
day older than ~2 days. audit_logs already had an index on org_id alone; adding
created_at makes the 90-day window an index range scan rather than a scan of
every row the org has ever written.

The same index also covers the `used_this_month` count already in that endpoint,
and the weekly digest's `_WEEK_TOTALS_SQL`, both of which filter on exactly
(org_id, created_at).

Revision ID: 0054
Revises: 0053
"""

from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain CREATE INDEX, matching every other migration here. It takes a brief
    # write lock on audit_logs; deploys already run migrations before the new
    # container serves traffic, and ingest retries, so a short pause is safer
    # than CONCURRENTLY — which cannot run inside Alembic's transaction and can
    # leave an INVALID index behind if it fails.
    op.create_index(
        "ix_audit_logs_org_created", "audit_logs", ["org_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_org_created", table_name="audit_logs")
