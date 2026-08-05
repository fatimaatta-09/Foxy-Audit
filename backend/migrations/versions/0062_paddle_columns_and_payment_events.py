"""Paddle: two columns on organizations, and payment_events

M2. Everything a second payment processor needs to record who bought what,
without touching a single Stripe column.

WHY A NEW TABLE AND NOT `stripe_events`
---------------------------------------
`stripe_events` has exactly the right shape, and reusing it would have been one
fewer migration. It is still wrong: a table named for one processor holding
another's events is a lie that every later reader has to un-learn, and
`admin_billing` already surfaces that table to staff as "Stripe events". The
cost of the honest version is this file.

`payment_events` is deliberately provider-tagged rather than Paddle-specific, so
a third processor does not need a third table — the shape is the durable-log
pattern, not a Paddle detail.

`provider_event_id` HOLDS `event_id`, NOT `notification_id`
-----------------------------------------------------------
Paddle sends both. `notification_id` (`ntf_…`) is unique per DELIVERY ATTEMPT;
`event_id` (`evt_…`) is unique per EVENT. Paddle guarantees at-least-once
delivery and retries on exponential backoff whenever the endpoint does not answer
200 within five seconds, so keying the UNIQUE on `notification_id` would insert a
fresh row for every retry and re-run every side effect that row guards. The
UNIQUE here is what makes `ON CONFLICT DO NOTHING` mean "we have already done
this", which is the entire reason replay is safe.

NO RLS, MATCHING `stripe_events`
--------------------------------
`org_id` is nullable — an event can arrive that resolves to no org (an unknown
customer, a malformed custom_data), and staff need those rows most of all. A NULL
can never match an RLS policy predicate, so enabling RLS would hide exactly the
failures somebody is trying to debug. Platform-only by construction, same
posture and same reasoning as `stripe_events`, `traffic_events` and
`marketing_leads`.

NO BACKFILL, and nothing to backfill: both new columns are nullable and every
existing org legitimately has no Paddle identity.

Revision ID: 0062
Revises: 0061
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations",
                  sa.Column("paddle_customer_id", sa.String(255), nullable=True))
    op.add_column("organizations",
                  sa.Column("paddle_subscription_id", sa.String(255), nullable=True))
    # UNIQUE, like `stripe_customer_id`: one processor customer maps to one
    # workspace, and the webhook resolves an org by this value. Two orgs sharing
    # one customer id would make that lookup ambiguous — better a write that
    # fails loudly than a payment applied to the wrong tenant.
    op.create_unique_constraint(
        "uq_organizations_paddle_customer_id", "organizations", ["paddle_customer_id"])

    op.create_table(
        "payment_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_event_id", sa.String(255), nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("org_id", UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="received"),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # The idempotency key. UNIQUE is what `ON CONFLICT DO NOTHING` conflicts on;
    # without it a retried delivery is processed twice.
    op.create_unique_constraint(
        "uq_payment_events_provider_event_id", "payment_events", ["provider_event_id"])
    op.create_index("ix_payment_events_provider_event_id",
                    "payment_events", ["provider_event_id"])
    # The ops console lists newest-first, filtered by provider.
    op.create_index("ix_payment_events_provider_received",
                    "payment_events", ["provider", "received_at"])


def downgrade() -> None:
    op.drop_index("ix_payment_events_provider_received", table_name="payment_events")
    op.drop_index("ix_payment_events_provider_event_id", table_name="payment_events")
    op.drop_constraint("uq_payment_events_provider_event_id",
                       "payment_events", type_="unique")
    op.drop_table("payment_events")
    op.drop_constraint("uq_organizations_paddle_customer_id",
                       "organizations", type_="unique")
    op.drop_column("organizations", "paddle_subscription_id")
    op.drop_column("organizations", "paddle_customer_id")
