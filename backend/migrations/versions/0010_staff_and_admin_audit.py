"""staff_users + admin_actions: platform-staff accounts + audit trail (Phase 4 #1)

The internal admin site ("site 3") runs on a platform-staff channel that is
provably disjoint from the customer channel. Neither table is org-scoped and
neither has RLS: staff read cross-org with app.current_org UNSET, and admin_actions
is a platform-wide log. See models.StaffUser / models.AdminAction.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "staff_users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),   # pgcrypto (0001)
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("platform_role", sa.String(16), nullable=False, server_default="viewer"),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_by", UUID(as_uuid=True),
                  sa.ForeignKey("staff_users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint(
            "platform_role IN ('viewer','operator','superadmin')",
            name="ck_staff_platform_role"),
    )
    op.create_index("ix_staff_users_email", "staff_users", ["email"], unique=True)

    op.create_table(
        "admin_actions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("staff_user_id", UUID(as_uuid=True),
                  sa.ForeignKey("staff_users.id"), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_org_id", UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("target_type", sa.String(32)),
        sa.Column("target_id", sa.String(64)),
        sa.Column("detail", JSONB),
        sa.Column("ip", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_admin_actions_staff", "admin_actions", ["staff_user_id"])
    op.create_index("ix_admin_actions_org", "admin_actions", ["target_org_id"])


def downgrade() -> None:
    op.drop_index("ix_admin_actions_org", table_name="admin_actions")
    op.drop_index("ix_admin_actions_staff", table_name="admin_actions")
    op.drop_table("admin_actions")
    op.drop_index("ix_staff_users_email", table_name="staff_users")
    op.drop_table("staff_users")
