"""users: avatar path + last-changed stamp

P6c. The first prod state in this product that does not live in Postgres. Only
the PATH is stored here — the PNG itself is written to a mounted volume — so a
row read never carries image bytes and `users` stays cheap to SELECT.

Both columns are nullable with no server default, because "this user has no
photo" is the correct and overwhelmingly common state, not a missing value. A
default would mean every existing account claims a picture that is not on disk,
and the GET route would start answering 404 for rows that say otherwise.

`avatar_updated_at` is not decoration and not `onupdate`: the dashboard appends
it to the image URL as a cache-buster, so it has to change exactly when the FILE
changes and at no other time. Tying it to row updates would re-fetch the picture
every time someone edited their display name.

Nothing here is dropped on downgrade beyond the columns. The files on the volume
outlive this migration in both directions — deleting them from a schema
migration would make a rollback destroy user data, which is not what a rollback
is for. They are orphaned, small, and reclaimed by the delete route or by hand.

Revision ID: 0057
Revises: 0056
"""

import sqlalchemy as sa
from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("avatar_path", sa.String(255), nullable=True))
    op.add_column("users",
                  sa.Column("avatar_updated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "avatar_updated_at")
    op.drop_column("users", "avatar_path")
