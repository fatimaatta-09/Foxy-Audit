"""org_policies: a separate, nullable SDK-facing enforcement field

P4 §B. `enforcement_mode` (block|flag|monitor) is a JUDGE-RESPONSE vocabulary —
what to do with a verdict AFTER grading. The SDK's (observe|redact|block) is a
PRE-CALL vocabulary — what to do before the model is invoked. They are different
settings that happen to share a word, and overloading one for the other is how
the SDK would end up enforcing a rule the owner set for the judge.

Nullable with NO server default, deliberately. NULL means "this workspace has
expressed no opinion about SDK enforcement", which is what every existing org
reads on upgrade — so shipping this changes nothing for anybody until an owner
deliberately chooses. That matters more than it sounds: `enforcement_mode`
defaults to "block" and `_get_or_create` writes a default row on first read, so
every org already stores "block" whether or not a human ever chose it. Had the
SDK honoured THAT field, every customer running `observe` would have started
raising FoxyPolicyBlocked the moment they upgraded.

`enforcement_mode` is deliberately left alone — not migrated, not rewritten. Its
values are already recorded inside historical policy snapshots that have been
handed to auditors, and rewriting recorded policy in a compliance product is the
wrong instinct even when the new value would be "better".

Revision ID: 0056
Revises: 0055
"""

import sqlalchemy as sa
from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No server_default: NULL is the meaningful "unset" state, not a placeholder
    # for one. A default here would recreate the exact problem this column exists
    # to avoid.
    op.add_column("org_policies",
                  sa.Column("sdk_enforcement", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("org_policies", "sdk_enforcement")
