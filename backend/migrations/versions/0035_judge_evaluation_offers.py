"""time-limited, non-billable judge evaluation access

The plaintext offer code intentionally remains only in deployment configuration.
This migration stores entitlement state and an HMAC-hashed email fingerprint so
an evaluation campaign cannot be redeemed repeatedly by the same email.

Revision ID: 0035
Revises: 0034
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("evaluation_offer_id", sa.String(64), nullable=True))
    op.add_column("organizations", sa.Column("evaluation_credit_limit", sa.Integer(), nullable=True))
    op.add_column(
        "organizations",
        sa.Column("evaluation_credits_used", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column("organizations", sa.Column("evaluation_ends_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "evaluation_redemptions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("offer_id", sa.String(64), nullable=False),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email_hash", sa.String(64), nullable=False),
        sa.Column("credits_granted", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("offer_id", "email_hash", name="uq_evaluation_offer_email"),
        sa.UniqueConstraint("org_id", name="uq_evaluation_redemption_org"),
    )
    op.create_index("ix_evaluation_redemptions_offer_id", "evaluation_redemptions", ["offer_id"])


def downgrade() -> None:
    op.drop_index("ix_evaluation_redemptions_offer_id", table_name="evaluation_redemptions")
    op.drop_table("evaluation_redemptions")
    op.drop_column("organizations", "evaluation_ends_at")
    op.drop_column("organizations", "evaluation_credits_used")
    op.drop_column("organizations", "evaluation_credit_limit")
    op.drop_column("organizations", "evaluation_offer_id")
