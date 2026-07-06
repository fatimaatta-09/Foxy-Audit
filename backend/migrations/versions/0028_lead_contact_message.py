"""marketing_leads.subject / .message — capture the contact form's message (7A-2)

The public sales-page contact form collected a subject + message but the backend
only stored name/email, silently dropping the message. Add nullable columns so the
message is persisted (staff read it via the admin Data tab).

Revision ID: 0028
Revises: 0027
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("marketing_leads",
                  sa.Column("subject", sa.String(length=128), nullable=True))
    op.add_column("marketing_leads",
                  sa.Column("message", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("marketing_leads", "message")
    op.drop_column("marketing_leads", "subject")
