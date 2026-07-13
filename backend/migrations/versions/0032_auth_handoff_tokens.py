"""auth_handoff_tokens — single-use desktop-pet -> dashboard auto-login handoff (Phase B)

The desktop pet (which holds the org API key) mints a short-lived, single-use token via
POST /v1/auth/handoff; the dashboard redeems it (?handoff=...) to establish a session with
no fresh login. Only the SHA-256 hash of the token is stored, so a DB leak alone can't be
redeemed. foxy_app grants are inherited via 0021's ALTER DEFAULT PRIVILEGES (no RLS here —
it's an auth-plumbing table, looked up by hash).

Revision ID: 0032
Revises: 0031
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_handoff_tokens",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_unique_constraint("uq_handoff_token_hash", "auth_handoff_tokens", ["token_hash"])


def downgrade() -> None:
    op.drop_table("auth_handoff_tokens")
