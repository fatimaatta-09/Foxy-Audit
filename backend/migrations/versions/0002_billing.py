"""billing: add Stripe columns + key_rotated_at to organizations (idempotent)

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-26

Note: 0001 was updated to include these columns in the initial CREATE TABLE.
This migration remains for databases created before that fix — it uses
IF NOT EXISTS guards so it is a safe no-op on fresh schemas.
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Each column add is wrapped in a PL/pgSQL block that silently skips if the
    # column already exists (i.e., was added by the updated 0001 migration).
    op.execute(
        """
        DO $$
        BEGIN
            ALTER TABLE organizations ADD COLUMN plan_tier VARCHAR(32);
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            ALTER TABLE organizations ADD COLUMN stripe_customer_id VARCHAR(255) UNIQUE;
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            ALTER TABLE organizations ADD COLUMN stripe_subscription_id VARCHAR(255);
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            ALTER TABLE organizations ADD COLUMN subscription_status VARCHAR(32);
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            ALTER TABLE organizations ADD COLUMN key_rotated_at TIMESTAMPTZ;
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$;
        """
    )
    # Unique constraint on stripe_customer_id (no-op if already present)
    op.execute(
        """
        DO $$
        BEGIN
            ALTER TABLE organizations
                ADD CONSTRAINT uq_org_stripe_customer UNIQUE (stripe_customer_id);
        EXCEPTION WHEN duplicate_table THEN NULL;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            ALTER TABLE organizations
                DROP CONSTRAINT IF EXISTS uq_org_stripe_customer;
        END $$;
        """
    )
    op.execute("ALTER TABLE organizations DROP COLUMN IF EXISTS key_rotated_at;")
    op.execute("ALTER TABLE organizations DROP COLUMN IF EXISTS subscription_status;")
    op.execute("ALTER TABLE organizations DROP COLUMN IF EXISTS stripe_subscription_id;")
    op.execute("ALTER TABLE organizations DROP COLUMN IF EXISTS stripe_customer_id;")
    op.execute("ALTER TABLE organizations DROP COLUMN IF EXISTS plan_tier;")

