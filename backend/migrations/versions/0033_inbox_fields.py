"""inbox fields on marketing_leads — read/claim/reply tracking for the admin support inbox

Every marketing_lead that carries a written `message` (support/contact/enterprise note)
becomes an admin-inbox item. read_at drives the unread badge; claimed_by/claimed_at lock a
message to one staff member (others read-only); reply/replied_at hold the emailed response.
foxy_app grants inherited via 0021's ALTER DEFAULT PRIVILEGES.

Revision ID: 0033
Revises: 0032
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("marketing_leads", sa.Column("read_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("marketing_leads", sa.Column(
        "claimed_by", UUID(as_uuid=True), sa.ForeignKey("staff_users.id"), nullable=True))
    op.add_column("marketing_leads", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("marketing_leads", sa.Column("reply", sa.Text(), nullable=True))
    op.add_column("marketing_leads", sa.Column("replied_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for col in ("replied_at", "reply", "claimed_at", "claimed_by", "read_at"):
        op.drop_column("marketing_leads", col)
