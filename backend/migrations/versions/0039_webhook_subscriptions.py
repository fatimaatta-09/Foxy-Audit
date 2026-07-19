"""Customer outbound webhook subscriptions (P3 · §F)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhook_subscriptions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("url", sa.String(length=1024), nullable=False),
        sa.Column("secret", sa.String(length=80), nullable=False),   # HMAC signing key
        sa.Column("events", sa.String(length=255), nullable=False,
                  server_default="breach"),                          # csv: breach[,graded]
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_status", sa.String(length=32), nullable=True),
        sa.Column("last_delivery_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_webhook_subs_org", "webhook_subscriptions", ["org_id"])
    op.execute("ALTER TABLE webhook_subscriptions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE webhook_subscriptions FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY webhook_sub_isolation ON webhook_subscriptions
        USING (org_id = current_setting('app.current_org', true)::uuid)
        WITH CHECK (org_id = current_setting('app.current_org', true)::uuid)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS webhook_subscriptions")
