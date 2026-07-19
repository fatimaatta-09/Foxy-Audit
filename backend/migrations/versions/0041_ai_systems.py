"""Tenant-scoped, content-blind AI system inventory."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_systems",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("owner_email", sa.String(length=320), nullable=False),
        sa.Column("purpose", sa.String(length=256), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False, server_default="other"),
        sa.Column("model_name", sa.String(length=128), nullable=True),
        sa.Column("environment", sa.String(length=16), nullable=False, server_default="production"),
        sa.Column("data_classification", sa.String(length=16), nullable=False,
                  server_default="internal"),
        sa.Column("risk_tier", sa.String(length=16), nullable=False, server_default="medium"),
        sa.Column("lifecycle_status", sa.String(length=16), nullable=False,
                  server_default="active"),
        sa.Column("created_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("org_id", "name", name="uq_ai_system_org_name"),
        sa.CheckConstraint(
            "provider IN ('openai', 'azure_openai', 'anthropic', 'google', 'aws_bedrock', "
            "'self_hosted', 'other')", name="ck_ai_system_provider"),
        sa.CheckConstraint(
            "environment IN ('development', 'staging', 'production')",
            name="ck_ai_system_environment"),
        sa.CheckConstraint(
            "data_classification IN ('public', 'internal', 'confidential', 'regulated')",
            name="ck_ai_system_data_classification"),
        sa.CheckConstraint(
            "risk_tier IN ('low', 'medium', 'high', 'critical')",
            name="ck_ai_system_risk_tier"),
        sa.CheckConstraint(
            "lifecycle_status IN ('draft', 'active', 'retired')",
            name="ck_ai_system_lifecycle_status"),
    )
    op.create_index("ix_ai_systems_org", "ai_systems", ["org_id"])
    op.execute("ALTER TABLE ai_systems ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ai_systems FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY ai_system_isolation ON ai_systems
        USING (org_id = current_setting('app.current_org', true)::uuid)
        WITH CHECK (org_id = current_setting('app.current_org', true)::uuid)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_systems")
