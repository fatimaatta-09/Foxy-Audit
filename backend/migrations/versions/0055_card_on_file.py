"""organizations: card-on-file state for the signup payment gate

P3 §4. The owner's decision is "card captured at signup, never charged without
consent", so we need to know whether a card exists WITHOUT holding anything that
could be used to charge one. Stripe keeps the payment method; we keep only the
answer to "is there a card?" plus the brand and last four, which is all the UI
needs to show "Visa ···· 4242" on a locked or unlocked dashboard.

Deliberately NOT stored: any PAN, any Stripe payment-method id that could be
charged directly, any token. The card lives at Stripe; this is a flag and a
label.

`card_on_file` defaults FALSE, which means every existing org reads as "no card".
That is correct and intentional — none of them have completed a card setup — but
it also means turning on REQUIRE_CARD_ON_FILE locks every current customer out
of their dashboard until they add one. The flag ships OFF for exactly that
reason; flipping it is a business decision, not a deploy.

Revision ID: 0055
Revises: 0054
"""

import sqlalchemy as sa
from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column(
        "card_on_file", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("organizations", sa.Column("card_brand", sa.String(32), nullable=True))
    op.add_column("organizations", sa.Column("card_last4", sa.String(4), nullable=True))
    op.add_column("organizations", sa.Column(
        "card_added_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("organizations", "card_added_at")
    op.drop_column("organizations", "card_last4")
    op.drop_column("organizations", "card_brand")
    op.drop_column("organizations", "card_on_file")
