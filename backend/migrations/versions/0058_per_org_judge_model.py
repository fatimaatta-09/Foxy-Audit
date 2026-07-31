"""org_policies: per-tenant judge MODEL, beside the existing provider choice

P6f. `judge_provider` already lets an org pick Gemini or OpenAI. These two let it
pick WHICH Gemini or WHICH OpenAI — the version, not just the vendor.

BOTH NULLABLE, WITH NO SERVER DEFAULT, AND THAT IS THE WHOLE DESIGN. NULL means
"inherit whatever this deployment currently runs" (settings.gemini_model /
settings.openai_model). A default here would write today's model id onto every
existing tenant, and from then on the deployment could never move them forward:
bumping the default would silently do nothing, because every row would already
hold an explicit answer nobody chose. Migration 0056 made the same argument for
`sdk_enforcement` and it holds harder here, since a model id ages out in months.

NO CHECK CONSTRAINT. A constraint listing valid model ids would need a migration
every time a provider ships a release, and would fail closed — an org pinned to a
model the constraint has not heard of yet could not be written at all. The
allow-list lives in judge_routing.py instead, where an unknown value falls back to
the deployment default and the grade still happens.

These columns are routing, not evidence: they live on the LIVE policy row and are
deliberately NOT projected into the policy snapshot. Which model graded an event
is recorded on the VERDICT, which is written after ingest and is not part of the
hashed chain material — see the note on Verdict in app/schemas.py.

Revision ID: 0058
Revises: 0057
"""

import sqlalchemy as sa
from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("org_policies",
                  sa.Column("gemini_judge_model", sa.String(64), nullable=True))
    op.add_column("org_policies",
                  sa.Column("openai_judge_model", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("org_policies", "openai_judge_model")
    op.drop_column("org_policies", "gemini_judge_model")
