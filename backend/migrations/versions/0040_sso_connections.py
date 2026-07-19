"""Per-org enterprise SSO (OIDC) connections (P3 · §H)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sso_connections",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False, unique=True),                 # one connection per org
        sa.Column("email_domain", sa.String(length=255), nullable=False, unique=True),
        sa.Column("issuer", sa.String(length=512), nullable=False),
        sa.Column("client_id", sa.String(length=512), nullable=False),
        sa.Column("client_secret", sa.String(length=512), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.execute("ALTER TABLE sso_connections ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE sso_connections FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY sso_conn_isolation ON sso_connections
        USING (org_id = current_setting('app.current_org', true)::uuid)
        WITH CHECK (org_id = current_setting('app.current_org', true)::uuid)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sso_connections")
