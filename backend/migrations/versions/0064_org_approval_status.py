"""organizations.approval_status — the demo route's own marker

M4a. The commercial model changed on 2026-08-06: there is no self-serve free
tier for new signups, only a 7-day demo that a human approves, and the dashboard
locks on day 7.

WHY A COLUMN AND NOT A DATE CUTOFF
----------------------------------
The trial lock is new, and the one outcome the owner ruled out is it firing on
the free organisations that already exist — they are grandfathered. Scoping it by
a configured date would be the wrong tool for a reason this repo has already
written down: `card_gate_grandfather_before`'s own comment says a date baked into
config is wrong the moment it passes, and an org created an hour after midnight
on that date gets locked out by an off-by-one nobody can see.

So the scope is structural instead. Only an organisation that arrives through the
demo route ever carries an `approval_status`; the lock fires only on "approved";
and this migration writes NO backfill, so every pre-existing row holds NULL and
is exempt **by construction**. Nobody has to count production rows to know that
— which matters, because nobody with production access was asked.

That is the same safety argument as 0060 (`past_due_since`) and 0061
(`local_verdict`): the absence of a backfill IS the feature. A `server_default`
of 'approved' would silently enrol every existing customer in a lock they never
signed up for, and a default of 'pending' would lock all of them out on deploy.
Hence: nullable, no default, no backfill.

VALUES
------
NULL       not a demo organisation. Every row before this migration, and every
           row a purchase creates.
'pending'  signed up, waiting for a human. Cannot capture, cannot read the
           dashboard, `trial_ends_at` still NULL.
'approved' a human said yes; the 7-day clock started AT THAT MOMENT.

There is deliberately no 'rejected'. `suspended` + `suspended_reason` already
express a refusal, are already enforced on every customer auth channel, and
already have their own message.

NO INDEX. The approvals queue will filter on `approval_status = 'pending'`, but
`organizations` is a small table read by primary key everywhere else, and an
index whose only reader does not exist yet is speculative. M4c can add one if the
queue is ever slow.

Revision ID: 0064
Revises: 0063
"""

import sqlalchemy as sa
from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column(
        "approval_status", sa.String(16), nullable=True))


def downgrade() -> None:
    # Dropping the column returns every organisation to "not a demo org", which
    # is exactly what the trial lock reads NULL as. A downgrade therefore unlocks
    # a locked demo rather than locking anyone — the safe direction, and the only
    # one available, since the state has nowhere else to live.
    op.drop_column("organizations", "approval_status")
