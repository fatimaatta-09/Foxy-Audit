"""org_policies judge-sensitivity fields (Phase 5)

Make the dashboard's "Judge sensitivity" controls REAL (they were cosmetic and
saved nowhere): enforcement_mode, confidence_threshold, notify_on_breach. String
enums with safe defaults; existing rows adopt the defaults via server_default.

Revision ID: 0031
Revises: 0030
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("org_policies", sa.Column(
        "enforcement_mode", sa.String(length=16), nullable=False, server_default="block"))
    op.add_column("org_policies", sa.Column(
        "confidence_threshold", sa.String(length=16), nullable=False, server_default="balanced"))
    op.add_column("org_policies", sa.Column(
        "notify_on_breach", sa.String(length=16), nullable=False, server_default="immediate"))


def downgrade() -> None:
    op.drop_column("org_policies", "notify_on_breach")
    op.drop_column("org_policies", "confidence_threshold")
    op.drop_column("org_policies", "enforcement_mode")
