"""password-reset link token columns — customer + staff (Phase 5 · 5D)

Adds reset_token_hash + reset_token_expires_at to both users and staff_users.
Only the SHA-256 hash of a high-entropy token is stored; single-use, 1h TTL.
(The existing users/staff_users grants to foxy_app cover new columns.)

Revision ID: 0022
Revises: 0021
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("users", "staff_users"):
        op.add_column(table, sa.Column("reset_token_hash", sa.String(64), nullable=True))
        op.add_column(table, sa.Column(
            "reset_token_expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for table in ("users", "staff_users"):
        op.drop_column(table, "reset_token_expires_at")
        op.drop_column(table, "reset_token_hash")
