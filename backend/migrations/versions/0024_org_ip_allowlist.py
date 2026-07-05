"""organizations per-org dashboard IP allow-list (Phase 5 · 5K)

organizations.ip_allowlist: comma-separated IPs/CIDRs restricting DASHBOARD
(session) access; NULL/empty = no restriction. Not applied to the SDK key.

Revision ID: 0024
Revises: 0022
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("ip_allowlist", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("organizations", "ip_allowlist")
