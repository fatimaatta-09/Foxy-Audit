"""drop the redundant unique constraint on organizations.stripe_customer_id

0001 created an inline UNIQUE (organizations_stripe_customer_id_key) and 0002
added a second, named constraint (uq_org_stripe_customer) on the SAME column, so
a fresh DB carries two unique constraints there. Keep the inline one (which
models.py's `unique=True` maps to) and drop the named duplicate. (Phase 5 · 5A.4)

Revision ID: 0017
Revises: 0016
"""

from __future__ import annotations

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE organizations DROP CONSTRAINT IF EXISTS uq_org_stripe_customer"
    )


def downgrade() -> None:
    # Re-add it (guarded) so downgrade restores the prior — if redundant — state.
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
