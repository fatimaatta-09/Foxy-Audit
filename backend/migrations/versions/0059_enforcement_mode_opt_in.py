"""org_policies: `block` becomes a deliberate choice, not an inherited default

P5 §A.1. `enforcement_mode` (block|flag|monitor) is about to get its first real
consumer: it decides whether a graded breach EMAILS a human (P5 §A.2). Before
that consumer ships, the stored population has to mean what it says.

It does not today. The column carried `server_default='block'` and
`routers/policies.py::_get_or_create` writes a default policy row on the first
GET, so EVERY organisation is stored as `block` because that is the column
default — not because anybody chose it. Switching on a consumer over that
population would escalate breach mail for every tenant at once: an alert storm
from a feature nobody enabled. Migration 0056 made the same observation about
this exact column while explaining why the SDK could not honour it.

So the default moves to `flag` — today's behaviour, notify only when the tenant
asked for immediate notice — and the inherited values move with it.

What makes the flip safe is `account_actions`. `routers/policies.py` has
recorded every `policy.update` with the chosen `enforcement_mode` in its JSONB
`detail`, so an org that DELIBERATELY selected `block` is distinguishable from
one that merely inherited it. The UPDATE skips those rows: a real choice is
never overwritten, in either direction.

WHY THIS DOES NOT CONTRADICT 0056
---------------------------------
0056 refuses to rewrite `enforcement_mode` because its values "are already
recorded inside historical policy snapshots that have been handed to auditors".
That objection is about RECORDED EVIDENCE, and it is honored here in full: this
UPDATE touches only the live `org_policies` row. `audit_logs.event_metadata`
— its `policy_snapshot` and the `policy_snapshot_hash` bound to it — is never
read, never written and never rewritten by this migration. Every snapshot
already exported stays byte-identical, every `policy_snapshot_hash` still
matches the JSON it hashes, and every `/v1/verify` that passed before this
migration still passes after it. A guard test asserts precisely that
(`tests/integration/test_enforcement_mode.py`).

Recorded policy stays recorded. What changes is what the tenant is configured
to do NEXT, which is the only thing a live settings row was ever for.

The downgrade restores the server default and stops there. It deliberately does
NOT flip values back: once this has run there is no way to distinguish a row
this migration moved from one an owner chose afterwards, and guessing would
overwrite a real choice in the direction that sends MORE mail.

Revision ID: 0059
Revises: 0058
"""

import sqlalchemy as sa
from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None

#: Module-level so the guard tests exercise the SQL that actually ships rather
#: than a copy of it that can drift out from under them.
FLIP_UNCHOSEN_SQL = """
UPDATE org_policies p SET enforcement_mode = 'flag'
 WHERE p.enforcement_mode = 'block'
   AND NOT EXISTS (SELECT 1 FROM account_actions a
                    WHERE a.org_id = p.org_id
                      AND a.action = 'policy.update'
                      AND a.detail->>'enforcement_mode' = 'block')
"""


def upgrade() -> None:
    op.alter_column("org_policies", "enforcement_mode",
                    existing_type=sa.String(16), existing_nullable=False,
                    server_default="flag")
    op.execute(FLIP_UNCHOSEN_SQL)


def downgrade() -> None:
    # Default only — see the docstring for why the values are left where they are.
    op.alter_column("org_policies", "enforcement_mode",
                    existing_type=sa.String(16), existing_nullable=False,
                    server_default="block")
