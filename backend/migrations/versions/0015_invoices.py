"""invoices: per-org billing history (Phase 4 #1)

RLS-scoped by org_id exactly like audit_logs/chain_anchors — a CUSTOMER reads its
own invoice history; staff (superuser, GUC unset) read all for the admin billing
view. See models.Invoice.

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoices",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("stripe_invoice_id", sa.String(255), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="usd"),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True)),
        sa.Column("period_end", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status IN ('draft','open','paid','void','uncollectible')", name="ck_invoice_status"),
    )
    op.create_index("ix_invoices_org_created", "invoices", ["org_id", "created_at"])
    op.create_index("ix_invoices_stripe_id", "invoices", ["stripe_invoice_id"], unique=True)

    # Tenant isolation at the DB layer, mirroring audit_logs (0001) / chain_anchors (0009).
    op.execute("ALTER TABLE invoices ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE invoices FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY org_isolation ON invoices
            USING      (org_id = current_setting('app.current_org', true)::uuid)
            WITH CHECK (org_id = current_setting('app.current_org', true)::uuid);
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS org_isolation ON invoices;")
    op.drop_index("ix_invoices_stripe_id", table_name="invoices")
    op.drop_index("ix_invoices_org_created", table_name="invoices")
    op.drop_table("invoices")
