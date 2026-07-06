"""audit_logs.agent — agent/model attribution, folded into the chain hash (6B)

audit_logs.agent: which agent/model produced the interaction; NULL for pre-6B
rows. Indexed for the dashboard agent filter. When present it is appended to the
chain data_blob as ``|agent=<agent>``, so it is tamper-evident; absent rows hash
exactly as before (backward-compatible).

Revision ID: 0026
Revises: 0025
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("agent", sa.String(length=128), nullable=True))
    op.create_index("ix_audit_logs_agent", "audit_logs", ["agent"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_agent", table_name="audit_logs")
    op.drop_column("audit_logs", "agent")
