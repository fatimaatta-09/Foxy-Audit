"""Staff device sessions + last_login (Phase E · dashboard parity).

Adds `staff_users.last_login_at` and a `staff_sessions` table (an active-devices list with
revoke + log-out-everywhere). The signed staff cookie now carries an opaque token whose SHA-256
is stored here; require_staff validates it. NO remember-me — the 2h cookie max-age is unchanged.
Platform-only: ENABLE + FORCE RLS with NO policy (same posture as staff_users / admin_actions).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("staff_users",
                  sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "staff_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("staff_user_id", UUID(as_uuid=True),
                  sa.ForeignKey("staff_users.id"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=400), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_staff_sessions_owner", "staff_sessions", ["staff_user_id"])
    op.execute("ALTER TABLE staff_sessions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE staff_sessions FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS staff_sessions")
    op.drop_column("staff_users", "last_login_at")
