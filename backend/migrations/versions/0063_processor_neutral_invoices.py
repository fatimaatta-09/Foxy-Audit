"""invoices becomes processor-neutral — a customer can finally see what they paid

M3f. Two kinds of customer pay us and neither could see a record of it: a Paddle
card customer's transaction lived in `payment_events` (a staff webhook log, not a
receipt), and a Payoneer customer's payment existed only as a `payment_reference`
on an `admin_actions` row that M3c made readable to STAFF.

WIDEN, NOT A SECOND TABLE
-------------------------
Three reasons, and the first is the one that decided it:

* **`invoices` already has the RLS posture this needs.** Migration 0015 gave it
  `FORCE ROW LEVEL SECURITY` and an `org_isolation` policy on `org_id`. A new
  table means writing that policy again, and getting RLS right is the risky part
  of the job — not the column names.
* **`admin_billing.revenue` sums PAID invoices.** A separate table would silently
  exclude every real payment this company has ever taken from its own revenue
  figures, which is a worse outcome than a legacy column name.
* **Both shipped clients already render this list**, and neither reads
  `stripe_invoice_id` (`desktop/billing_data.invoice_rows` and the dashboard's
  invoice table both take date/amount/currency/status/period). Widening is
  additive for them; a second endpoint would not be.

WIDENING IS NOT OVERLOADING
---------------------------
`stripe_invoice_id` keeps meaning exactly what it says. It becomes NULLABLE
because a Paddle or manual row has no Stripe invoice — not so that a `txn_…` or
a Payoneer reference can be written into it. Those go in `provider_ref`, beside
a `provider` that names which system the reference belongs to.

NO BACKFILL OF A FAKE VALUE
---------------------------
`provider` takes a server default of 'stripe', which is true of every row that
could already exist: the Stripe webhook was the only writer. `provider_ref` and
`amount_cents` stay NULL where nothing real is known. Inventing a reference to
satisfy a NOT NULL is exactly the fabrication the column change exists to avoid.

`amount_cents` becomes nullable for the same reason: staff activating a plan
record WHICH payment they saw, not how much it was — the form has no amount
field. Zero would tell a customer they paid nothing.

RLS IS UNCHANGED AND DELIBERATELY UNTOUCHED. Widening an existing table inherits
its policy; `org_isolation` on `org_id` already covers every new column, and the
new writers scope themselves with `set_config('app.current_org', …)` the way
`billing._handle_invoice` already does.

Revision ID: 0063
Revises: 0062
"""

import sqlalchemy as sa
from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column(
        "provider", sa.String(32), nullable=False, server_default="stripe"))
    op.add_column("invoices", sa.Column("provider_ref", sa.String(255), nullable=True))
    op.alter_column("invoices", "stripe_invoice_id",
                    existing_type=sa.String(255), nullable=True)
    op.alter_column("invoices", "amount_cents",
                    existing_type=sa.Integer(), nullable=True)
    # One payment per (processor, reference). This is what makes the two new
    # writers idempotent: a replayed Paddle webhook and a staff member pressing
    # Apply twice both land on the same row instead of a duplicate receipt.
    # NULLs are distinct in Postgres, so legacy Stripe rows (provider='stripe',
    # provider_ref=NULL) are unaffected however many there are.
    op.create_unique_constraint(
        "uq_invoices_provider_ref", "invoices", ["provider", "provider_ref"])
    # No (org_id, created_at) index here: migration 0015 already created
    # `ix_invoices_org_created`, which is exactly the index this route's
    # "newest first, for one org" query wants. Adding it again is a duplicate
    # relation and fails the upgrade — found by running it, not by reading it.


def downgrade() -> None:
    op.drop_constraint("uq_invoices_provider_ref", "invoices", type_="unique")
    # Rows without a Stripe invoice id cannot survive the column going back to
    # NOT NULL, and inventing one to keep them would be the fabrication this
    # migration exists to prevent. They are non-Stripe payments; a schema that
    # cannot express them should not hold them.
    op.execute("DELETE FROM invoices WHERE stripe_invoice_id IS NULL")
    op.execute("UPDATE invoices SET amount_cents = 0 WHERE amount_cents IS NULL")
    op.alter_column("invoices", "amount_cents",
                    existing_type=sa.Integer(), nullable=False)
    op.alter_column("invoices", "stripe_invoice_id",
                    existing_type=sa.String(255), nullable=False)
    op.drop_column("invoices", "provider_ref")
    op.drop_column("invoices", "provider")
