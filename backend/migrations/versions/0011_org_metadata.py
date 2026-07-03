"""organizations: platform/admin metadata columns (Phase 4 #1)

All additions are nullable or defaulted so this ADD COLUMN is backward-compatible
— the running app keeps working mid-migration. Guarded with duplicate_column
EXCEPTION blocks (matching 0002_billing.py) so a re-run on a schema that already
has them is a safe no-op.

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-03
"""
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

_ADD_COLUMNS = [
    "contact_email VARCHAR(320)",
    "trial_ends_at TIMESTAMPTZ",
    "deleted_at TIMESTAMPTZ",
    "suspended BOOLEAN NOT NULL DEFAULT false",
    "suspended_reason VARCHAR(255)",
    "monthly_log_quota INTEGER",
]


def upgrade() -> None:
    for coldef in _ADD_COLUMNS:
        op.execute(
            f"""
            DO $$
            BEGIN
                ALTER TABLE organizations ADD COLUMN {coldef};
            EXCEPTION WHEN duplicate_column THEN NULL;
            END $$;
            """
        )


def downgrade() -> None:
    op.execute("ALTER TABLE organizations DROP COLUMN IF EXISTS monthly_log_quota;")
    op.execute("ALTER TABLE organizations DROP COLUMN IF EXISTS suspended_reason;")
    op.execute("ALTER TABLE organizations DROP COLUMN IF EXISTS suspended;")
    op.execute("ALTER TABLE organizations DROP COLUMN IF EXISTS deleted_at;")
    op.execute("ALTER TABLE organizations DROP COLUMN IF EXISTS trial_ends_at;")
    op.execute("ALTER TABLE organizations DROP COLUMN IF EXISTS contact_email;")
