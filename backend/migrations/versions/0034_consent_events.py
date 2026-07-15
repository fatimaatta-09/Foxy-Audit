"""consent_events — auditable record of cookie-consent choices from the marketing site

Each Accept / Reject / save on the consent banner writes one row, with the IP/UA
anonymized (HMAC pepper, exactly like traffic_events). Platform-only, no RLS —
anonymous marketing visitors, read by staff. foxy_app grants inherited via 0021's
ALTER DEFAULT PRIVILEGES.

Revision ID: 0034
Revises: 0033
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "consent_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("region", sa.String(8), nullable=True),          # eu | us | other
        sa.Column("regime", sa.String(8), nullable=True),          # gdpr | ccpa
        sa.Column("analytics", sa.Boolean(), nullable=False),
        sa.Column("functional", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("policy_version", sa.String(16), nullable=True),
        sa.Column("ip_hash", sa.String(64), nullable=True),        # HMAC(pepper, ip)
        sa.Column("ua_hash", sa.String(64), nullable=True),        # HMAC(pepper, user-agent)
    )
    op.create_index("ix_consent_events_created_at", "consent_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_consent_events_created_at", table_name="consent_events")
    op.drop_table("consent_events")
