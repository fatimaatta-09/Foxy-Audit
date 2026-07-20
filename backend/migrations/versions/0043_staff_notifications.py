"""Staff notifications center (Phase D · dashboard parity).

A dedicated `staff_notifications` table generated from REAL events only (broadcasts, staff
actions targeting you, system/org events). Platform-only: ENABLE + FORCE RLS with NO policy
(confined app role denied, superuser staff role bypasses) — the same posture as staff_users /
admin_actions. One row per recipient (broadcasts fan out) so read state is per-staff.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "staff_notifications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("staff_user_id", UUID(as_uuid=True),
                  sa.ForeignKey("staff_users.id"), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("level", sa.String(length=16), nullable=False, server_default="info"),
        sa.Column("target_type", sa.String(length=32), nullable=True),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_staff_notifications_recipient",
                    "staff_notifications", ["staff_user_id", "created_at"])
    op.execute("ALTER TABLE staff_notifications ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE staff_notifications FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS staff_notifications")
