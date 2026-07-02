"""worker_heartbeat: a liveness row the grading poller updates each loop

Lets GET /health/ready detect a dead/stuck worker (the grading queue can back up
silently otherwise). Single row, id=1, updated by app/worker.py's run_forever().

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-03
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_heartbeat",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("beat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("INSERT INTO worker_heartbeat (id, beat_at) VALUES (1, NULL)")


def downgrade() -> None:
    op.drop_table("worker_heartbeat")
