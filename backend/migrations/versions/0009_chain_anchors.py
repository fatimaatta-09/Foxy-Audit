"""chain_anchors: public-chain anchoring receipts (Phase 3 A1)

Records each publication of an org's hash-chain head (root_hash @ last_seq) to a
public chain. RLS-scoped by org_id exactly like audit_logs, so a future
non-superuser DATABASE_URL role is confined to its own tenant; the app also
filters on org_id explicitly (the docker 'foxy' superuser bypasses RLS).

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chain_anchors",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),   # pgcrypto (0001)
        sa.Column("org_id", UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("root_hash", sa.String(64), nullable=False),
        sa.Column("last_seq", sa.BigInteger(), nullable=False),
        sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("tx_hash", sa.String(128)),
        sa.Column("block_number", sa.BigInteger()),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("detail", sa.String(500)),
        sa.Column("anchored_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()")),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_chain_anchors_org_id", "chain_anchors", ["org_id"])

    # Tenant isolation at the DB layer, mirroring audit_logs (0001).
    op.execute("ALTER TABLE chain_anchors ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE chain_anchors FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY org_isolation ON chain_anchors
            USING      (org_id = current_setting('app.current_org', true)::uuid)
            WITH CHECK (org_id = current_setting('app.current_org', true)::uuid);
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS org_isolation ON chain_anchors;")
    op.drop_index("ix_chain_anchors_org_id", table_name="chain_anchors")
    op.drop_table("chain_anchors")
