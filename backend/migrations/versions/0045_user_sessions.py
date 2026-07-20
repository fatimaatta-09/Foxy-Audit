"""Customer device sessions — remember-me + active-devices + log-out-everywhere (dashboard P3).

Adds a `user_sessions` table: the signed customer cookie (`session`) carries an opaque token whose
SHA-256 is stored here; `require_user` validates it (not revoked, not past `expires_at`) and refreshes
`last_seen_at` — enabling an active-devices list + per-session revoke + log-out-everywhere. The
`remember_me` flag on login picks the expiry (30d) vs the default (12h); the cookie max-age is raised to
the 30d ceiling and `expires_at` is the authoritative gate. Per-org: org_isolation RLS (same posture as
invoices / usage_daily). token_hash never leaves the server.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("org_id", UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=400), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_user_sessions_owner", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_org", "user_sessions", ["org_id"])
    op.execute("ALTER TABLE user_sessions ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE user_sessions FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY org_isolation ON user_sessions
            USING      (org_id = current_setting('app.current_org', true)::uuid)
            WITH CHECK (org_id = current_setting('app.current_org', true)::uuid);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_sessions")
