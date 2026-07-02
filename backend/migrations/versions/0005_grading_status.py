"""grading_status: durable Postgres-outbox columns for the grading queue

Turns the async Gemini grading job into a column on the very row being graded,
so the job is committed in the same transaction as the chain row (no in-memory
queue that a crash can drop). Drained by the poller in app/worker.py.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-02
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "audit_logs",
        sa.Column("grading_status", sa.String(16), nullable=False,
                  server_default="pending"),   # pending | in_progress | graded | failed
    )
    op.add_column(
        "audit_logs",
        sa.Column("grading_attempts", sa.Integer(), nullable=False,
                  server_default="0"),
    )
    op.add_column(
        "audit_logs",
        sa.Column("grading_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "audit_logs",
        sa.Column("graded_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Rows that already carry a verdict are done; leave the rest 'pending'.
    op.execute(
        "UPDATE audit_logs SET grading_status = 'graded' "
        "WHERE gemini_verdict IS NOT NULL"
    )
    # Partial index keeps the poller's claim query cheap as the table grows.
    op.create_index(
        "ix_audit_logs_grading_pending",
        "audit_logs",
        ["created_at"],
        postgresql_where=sa.text("grading_status IN ('pending','in_progress')"),
    )


def downgrade() -> None:
    op.drop_index("ix_audit_logs_grading_pending", table_name="audit_logs")
    op.drop_column("audit_logs", "graded_at")
    op.drop_column("audit_logs", "grading_started_at")
    op.drop_column("audit_logs", "grading_attempts")
    op.drop_column("audit_logs", "grading_status")
