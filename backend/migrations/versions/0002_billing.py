"""billing: add Stripe columns + key_rotated_at to organizations

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-26
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("plan_tier", sa.String(32), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("stripe_customer_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("stripe_subscription_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("subscription_status", sa.String(32), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("key_rotated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_unique_constraint(
        "uq_org_stripe_customer",
        "organizations",
        ["stripe_customer_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_org_stripe_customer", "organizations")
    op.drop_column("organizations", "key_rotated_at")
    op.drop_column("organizations", "subscription_status")
    op.drop_column("organizations", "stripe_subscription_id")
    op.drop_column("organizations", "stripe_customer_id")
    op.drop_column("organizations", "plan_tier")
