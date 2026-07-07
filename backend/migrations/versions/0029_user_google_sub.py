"""users.google_sub — link a Google identity for SSO sign-in (Google OAuth)

Nullable, globally unique. Set on a user's first Google sign-in so subsequent
logins resolve by the stable Google subject id. Postgres allows many NULLs under
a UNIQUE index, so password-only users are unaffected.

Revision ID: 0029
Revises: 0028
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("google_sub", sa.String(length=255), nullable=True))
    op.create_unique_constraint("uq_user_google_sub", "users", ["google_sub"])


def downgrade() -> None:
    op.drop_constraint("uq_user_google_sub", "users", type_="unique")
    op.drop_column("users", "google_sub")
