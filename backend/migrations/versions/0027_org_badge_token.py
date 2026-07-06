"""organizations.public_badge_token — opt-in embeddable trust badge (6C)

organizations.public_badge_token: unique per-org token resolving the PUBLIC
GET /v1/badge/{token}.svg; NULL = no badge (opt-in, revocable). The endpoint
exposes aggregate status only — never org_id/name.

Revision ID: 0027
Revises: 0026
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations",
                  sa.Column("public_badge_token", sa.String(length=64), nullable=True))
    op.create_unique_constraint(
        "uq_org_public_badge_token", "organizations", ["public_badge_token"])


def downgrade() -> None:
    op.drop_constraint("uq_org_public_badge_token", "organizations", type_="unique")
    op.drop_column("organizations", "public_badge_token")
