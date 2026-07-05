"""staff_users email-OTP MFA columns (Phase 5 · 5B.5)

Opt-in per staff: mfa_enabled gates the two-step login; mfa_code_hash /
mfa_code_expires_at hold the short-lived, single-use emailed code.

Revision ID: 0019
Revises: 0018
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("staff_users", sa.Column(
        "mfa_enabled", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("staff_users", sa.Column("mfa_code_hash", sa.String(64), nullable=True))
    op.add_column("staff_users", sa.Column(
        "mfa_code_expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("staff_users", "mfa_code_expires_at")
    op.drop_column("staff_users", "mfa_code_hash")
    op.drop_column("staff_users", "mfa_enabled")
