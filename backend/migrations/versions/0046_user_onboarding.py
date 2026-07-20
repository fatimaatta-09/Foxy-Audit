"""users.onboarding_state — persist the dashboard onboarding checklist dismissal (dashboard P4).

A small JSONB bag on the customer `users` table (e.g. {"dismissed": true}). The checklist COMPLETION
is computed live from real data (active key / first logged call / team size) by GET /v1/onboarding;
only the dismissal is persisted here. Column only — no RLS change (users are already org-scoped).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("onboarding_state", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "onboarding_state")
