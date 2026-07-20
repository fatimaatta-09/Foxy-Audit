"""staff-managed, hashed evaluation campaigns

Campaign codes are stored only as HMACs. Staff can create and revoke finite
evaluation campaigns without changing deployment secrets.

Revision ID: 0052
Revises: 0051
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evaluation_campaigns",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("offer_id", sa.String(64), nullable=False),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("credits", sa.Integer(), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("max_redemptions", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by", UUID(as_uuid=True),
            sa.ForeignKey("staff_users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("offer_id", name="uq_evaluation_campaign_offer_id"),
        sa.UniqueConstraint("code_hash", name="uq_evaluation_campaign_code_hash"),
        sa.CheckConstraint("credits > 0", name="ck_evaluation_campaign_credits"),
        sa.CheckConstraint("duration_days > 0", name="ck_evaluation_campaign_days"),
        sa.CheckConstraint("max_redemptions > 0", name="ck_evaluation_campaign_capacity"),
        sa.CheckConstraint("status IN ('active', 'revoked')",
                           name="ck_evaluation_campaign_status"),
    )
    op.create_index("ix_evaluation_campaigns_offer_id", "evaluation_campaigns", ["offer_id"])
    op.create_index("ix_evaluation_campaigns_code_hash", "evaluation_campaigns", ["code_hash"])


def downgrade() -> None:
    op.drop_index("ix_evaluation_campaigns_code_hash", table_name="evaluation_campaigns")
    op.drop_index("ix_evaluation_campaigns_offer_id", table_name="evaluation_campaigns")
    op.drop_table("evaluation_campaigns")
