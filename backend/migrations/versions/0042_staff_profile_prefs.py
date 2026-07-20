"""Staff identity & preferences (Phase B · dashboard parity).

Adds `staff_users.full_name` (display name → avatar initial + topbar) and
`staff_users.preferences` (a small JSONB bag: hide_sensitive_metadata + notify_*).

`staff_users` is a platform-only table that already has ENABLE + FORCE RLS with no
policy (migration 0010) — the confined app role is denied, the superuser staff role
bypasses. Adding columns changes nothing about that posture, so no RLS work here.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("staff_users",
                  sa.Column("full_name", sa.String(length=120), nullable=True))
    op.add_column("staff_users",
                  sa.Column("preferences", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("staff_users", "preferences")
    op.drop_column("staff_users", "full_name")
