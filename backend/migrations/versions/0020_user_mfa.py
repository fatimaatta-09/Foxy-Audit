"""users email-OTP MFA columns — dashboard MFA (Phase 5 · 5B.5)

Mirrors the staff_users MFA columns from 0019, for the customer dashboard login.

Revision ID: 0020
Revises: 0019
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column(
        "mfa_enabled", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("users", sa.Column("mfa_code_hash", sa.String(64), nullable=True))
    op.add_column("users", sa.Column(
        "mfa_code_expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "mfa_code_expires_at")
    op.drop_column("users", "mfa_code_hash")
    op.drop_column("users", "mfa_enabled")
