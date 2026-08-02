"""organizations.past_due_since — when a failed payment started

D1. The dashboard is about to lock for a subscription that is `past_due`, but
not immediately: Stripe retries a declined charge over days, and a customer
whose bank declined once on Tuesday is not a customer who stopped paying. The
lock therefore needs an answer to "since when?", and nothing stored today can
give one.

WHY NOT A BILLING PERIOD BOUNDARY
---------------------------------
The obvious anchor is the end of the period they last paid for, and the plan
proposed exactly that. It is not available and, on inspection, not the right
question either:

* There is no `current_period_end` on `organizations` and never has been. The
  subscription webhook (`billing._handle_subscription_change`) reads only
  `status` off the subscription object.
* `invoices.period_end` does exist and is populated by `invoice.payment_failed`
  — but an invoice's period is the span it BILLS FOR, which for a renewal is the
  month ahead, not the month behind. A grace window measured from it would run
  from a date roughly a month in the future, and the lock would never fire.

So this column records the thing actually being measured — how long we have
known this org is not paying — rather than a Stripe field whose meaning shifts
at renewal. It is stamped once on the transition INTO `past_due` and cleared on
the way out, so the repeated `customer.subscription.updated` events Stripe emits
during its retry schedule cannot keep resetting the clock.

BACKFILL: NONE, DELIBERATELY
----------------------------
Every existing row gets NULL, including any org sitting at `past_due` right now,
and `billing_state.grace_ends_at` reads NULL as "never locked". So this
migration cannot lock anybody out on deploy: an org has to go past_due AFTER
this ships to acquire a stamp at all. Backfilling `now()` would do the opposite
— it would start a seven-day countdown for every already-failing customer
simultaneously, on a date that has nothing to do with when their payment
actually failed. Inventing that timestamp would also be fabricated data, which
this project does not do.

Revision ID: 0060
Revises: 0059
"""

import sqlalchemy as sa
from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations",
                  sa.Column("past_due_since", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("organizations", "past_due_since")
