"""drop the vestigial traffic_events.direction column

The middleware and /v1/track only ever wrote 'in'; nothing consumes an 'out'
direction and no outbound capture exists, so the column + its CHECK constraint
are dead weight and the "in/out" framing is misleading. Drop them. (Phase 5 · 5A.5)

Revision ID: 0018
Revises: 0017
"""

from __future__ import annotations

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the CHECK first, then the column (propagates across all partitions).
    op.execute("ALTER TABLE traffic_events DROP CONSTRAINT IF EXISTS ck_traffic_direction")
    op.execute("ALTER TABLE traffic_events DROP COLUMN IF EXISTS direction")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE traffic_events ADD COLUMN direction VARCHAR(3) NOT NULL DEFAULT 'in'"
    )
    op.execute(
        "ALTER TABLE traffic_events "
        "ADD CONSTRAINT ck_traffic_direction CHECK (direction IN ('in','out'))"
    )
