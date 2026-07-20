"""export_jobs — history/audit of compliance exports (dashboard P11).

Records who exported what and when (logs CSV/JSON, or the compliance passport). The server keeps NO
file archive — a re-download simply re-runs the existing producer with the same params — so `file_ref`
is reserved/nullable. Per-org: org_isolation RLS (same posture as invoices / usage_daily).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "export_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("requested_by", sa.String(length=320), nullable=True),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("params", JSONB(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="completed"),
        sa.Column("file_ref", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_export_jobs_org", "export_jobs", ["org_id"])
    op.execute("ALTER TABLE export_jobs ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE export_jobs FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY org_isolation ON export_jobs
            USING      (org_id = current_setting('app.current_org', true)::uuid)
            WITH CHECK (org_id = current_setting('app.current_org', true)::uuid);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS export_jobs")
