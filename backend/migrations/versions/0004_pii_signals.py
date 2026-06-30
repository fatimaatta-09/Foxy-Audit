"""add pii_signals

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-30

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("pii_signals", postgresql.JSONB, nullable=True))

def downgrade() -> None:
    op.drop_column("audit_logs", "pii_signals")
