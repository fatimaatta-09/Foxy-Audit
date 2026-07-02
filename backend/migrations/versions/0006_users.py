"""users: human dashboard accounts (email/password + role)

Adds the `users` table so people (admins / auditors) can log into the web
dashboard, separate from the machine org API key the SDK uses.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),   # pgcrypto (added in 0001)
        sa.Column("org_id", UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="member"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("org_id", "email", name="uq_user_org_email"),
    )
    op.create_index("ix_users_org_id", "users", ["org_id"])
    op.create_index("ix_users_email", "users", ["email"])


def downgrade() -> None:
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_org_id", table_name="users")
    op.drop_table("users")
