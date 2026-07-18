"""Breach-notification destinations on org_policies (P2 · §F)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("org_policies",
                  sa.Column("notify_email", sa.String(length=320), nullable=True))
    op.add_column("org_policies",
                  sa.Column("notify_webhook_url", sa.String(length=1024), nullable=True))


def downgrade() -> None:
    op.drop_column("org_policies", "notify_webhook_url")
    op.drop_column("org_policies", "notify_email")
