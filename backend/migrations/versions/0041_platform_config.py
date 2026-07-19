"""Platform config (flags / quotas / thresholds) + broadcast announcements (P3 · §F/§G).

Both tables are platform-global (no org_id), so the tenant-isolation policy pattern
doesn't apply. Instead we ENABLE + FORCE row-level security with NO permissive policy:
the confined app role (customer-scoped sessions) is denied entirely, while the
superuser staff role bypasses RLS — the same staff-only posture the ops console relies
on for its cross-org reads. Reads/writes only ever happen from admin routers.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_config",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("value", JSONB(), nullable=True),
        sa.Column("updated_by", UUID(as_uuid=True),
                  sa.ForeignKey("staff_users.id"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_table(
        "platform_announcements",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False, server_default="info"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True),
                  sa.ForeignKey("staff_users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    for table in ("platform_config", "platform_announcements"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform_announcements")
    op.execute("DROP TABLE IF EXISTS platform_config")
