"""marketing_leads: marketing-site conversions before they become orgs (Phase 4 #1)

Platform-only, NO RLS (no org_id until conversion; pre-sales data never on a
customer path). See models.MarketingLead.

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "marketing_leads",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("name", sa.String(255)),
        sa.Column("company", sa.String(255)),
        sa.Column("source", sa.String(64)),
        sa.Column("utm_campaign", sa.String(128)),
        sa.Column("utm_source", sa.String(128)),
        sa.Column("utm_medium", sa.String(128)),
        sa.Column("status", sa.String(16), nullable=False, server_default="new"),
        sa.Column("converted_org_id", UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("detail", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status IN ('new','trial','converted','churned')", name="ck_lead_status"),
    )
    # A churned email may re-enter the funnel, so uniqueness only holds while active.
    op.execute(
        "CREATE UNIQUE INDEX uq_leads_email_active ON marketing_leads (lower(email)) "
        "WHERE status <> 'churned';")
    op.create_index("ix_leads_status_created", "marketing_leads", ["status", "created_at"])
    op.execute(
        "CREATE INDEX ix_leads_converted_org ON marketing_leads (converted_org_id) "
        "WHERE converted_org_id IS NOT NULL;")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_leads_converted_org;")
    op.drop_index("ix_leads_status_created", table_name="marketing_leads")
    op.execute("DROP INDEX IF EXISTS uq_leads_email_active;")
    op.drop_table("marketing_leads")
