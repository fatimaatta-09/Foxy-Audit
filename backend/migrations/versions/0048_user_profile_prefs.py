"""users.full_name + users.preferences — customer identity & prefs (dashboard P14).

full_name: display name (nullable; identity defaults to email and uses the name once set → drives the
avatar initial + greeting). preferences: a small JSONB bag (hide_sensitive_metadata + notification
prefs). Columns only — no RLS change (users are already org-scoped). Mirrors the staff 0042 columns.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("full_name", sa.String(length=120), nullable=True))
    op.add_column("users", sa.Column("preferences", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "preferences")
    op.drop_column("users", "full_name")
