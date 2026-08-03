"""audit_logs.local_verdict + audit_logs.verdict_hash — the verdict joins the chain

H1. Until now `compute_chain_hash` enumerated every hashed field and no verdict
appeared in any of them, so a verdict could be rewritten in the database and the
chain would still verify. Chain version 4 binds `verdict_hash` into the hashed
event; these two columns are what it binds and what a verifier re-derives it
from.

WHICH VERDICT, AND WHY NOT THE AI ONE
-------------------------------------
`local_verdict` is the LOCAL, deterministic verdict `policy_engine` decides
synchronously at ingest. The AI judge's grade cannot go here: it does not exist
when the chain hash is computed (the outbox worker grades asynchronously
afterwards), so binding it would mean either re-hashing the row after grading —
invalidating every block after it — or putting an LLM call inside the ingest
path. `gemini_verdict` therefore stays exactly where it is, written by an UPDATE
that never touches `chain_hash`.

NULLABLE AND UN-BACKFILLED, DELIBERATELY
----------------------------------------
Every existing row is chain_version 1, 2 or 3, and its stored hash was computed
over a payload with no verdict field in it. Backfilling a verdict onto those
rows would attach evidence their hash never bound — the row would carry a claim
nothing protects — and inventing a historical verdict for an interaction we
graded under different rules is fabricated data, which this project does not do.
NULL is the honest value: this row predates V4.

Revision ID: 0061
Revises: 0060
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audit_logs",
                  sa.Column("local_verdict", postgresql.JSONB(astext_type=sa.Text()),
                            nullable=True))
    op.add_column("audit_logs",
                  sa.Column("verdict_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_logs", "verdict_hash")
    op.drop_column("audit_logs", "local_verdict")
