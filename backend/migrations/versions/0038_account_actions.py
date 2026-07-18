"""Per-org account audit log — customer-visible trail of their own actions (P2 · §D)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_actions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_email", sa.String(length=320), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=255), nullable=True),
        sa.Column("detail", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_account_actions_org_created", "account_actions",
                    ["org_id", "created_at"])
    op.execute("ALTER TABLE account_actions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE account_actions FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY account_action_isolation ON account_actions
        USING (org_id = current_setting('app.current_org', true)::uuid)
        WITH CHECK (org_id = current_setting('app.current_org', true)::uuid)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS account_actions")
