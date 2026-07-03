"""stripe_events: durable, idempotent webhook log (Phase 4 #1)

Makes the formerly fire-and-forget Stripe webhook auditable + replay-safe. The
UNIQUE stripe_event_id is the idempotency key (INSERT … ON CONFLICT DO NOTHING →
a replayed event is a no-op). Platform-only, NO RLS. See models.StripeEvent.

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stripe_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("stripe_event_id", sa.String(255), nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("org_id", UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="received"),
        sa.Column("error", sa.String(500)),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('received','processed','ignored','failed')", name="ck_stripe_event_status"),
    )
    op.create_index("ix_stripe_events_event_id", "stripe_events",
                    ["stripe_event_id"], unique=True)
    op.execute(
        "CREATE INDEX ix_stripe_events_org ON stripe_events (org_id, received_at DESC) "
        "WHERE org_id IS NOT NULL;")
    op.create_index("ix_stripe_events_type_received", "stripe_events",
                    ["type", "received_at"])


def downgrade() -> None:
    op.drop_index("ix_stripe_events_type_received", table_name="stripe_events")
    op.execute("DROP INDEX IF EXISTS ix_stripe_events_org;")
    op.drop_index("ix_stripe_events_event_id", table_name="stripe_events")
    op.drop_table("stripe_events")
