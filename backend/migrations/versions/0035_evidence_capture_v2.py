"""Durable capture identity, safe chain metadata, and append-only grade events."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in (
        sa.Column("event_id", UUID(as_uuid=True), nullable=True),
        sa.Column("client_id", sa.String(length=128), nullable=True),
        sa.Column("client_seq", sa.BigInteger(), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=True),
        sa.Column("commitment_alg", sa.String(length=32), nullable=True),
        sa.Column("event_metadata", JSONB, nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("chain_version", sa.SmallInteger(), nullable=False, server_default="1"),
    ):
        op.add_column("audit_logs", column)
    op.create_index(
        "uq_audit_org_event_id", "audit_logs", ["org_id", "event_id"],
        unique=True, postgresql_where=sa.text("event_id IS NOT NULL"))
    op.create_index("ix_audit_org_client_seq", "audit_logs", ["org_id", "client_id", "client_seq"])

    op.create_table(
        "org_sequences",
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("next_seq", sa.BigInteger(), nullable=False, server_default="1"),
    )
    op.execute("ALTER TABLE org_sequences ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE org_sequences FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY org_sequence_isolation ON org_sequences
        USING (org_id = current_setting('app.current_org', true)::uuid)
        WITH CHECK (org_id = current_setting('app.current_org', true)::uuid)
    """)

    op.create_table(
        "audit_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("audit_log_id", UUID(as_uuid=True), sa.ForeignKey("audit_logs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("event_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_audit_events_org_id", "audit_events", ["org_id"])
    op.create_index("ix_audit_events_audit_log_id", "audit_events", ["audit_log_id"])
    op.execute("ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_events FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY audit_event_isolation ON audit_events
        USING (org_id = current_setting('app.current_org', true)::uuid)
        WITH CHECK (org_id = current_setting('app.current_org', true)::uuid)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit_events")
    op.execute("DROP TABLE IF EXISTS org_sequences")
    op.drop_index("ix_audit_org_client_seq", table_name="audit_logs")
    op.drop_index("uq_audit_org_event_id", table_name="audit_logs")
    for name in ("chain_version", "occurred_at", "event_metadata", "commitment_alg",
                 "event_type", "client_seq", "client_id", "event_id"):
        op.drop_column("audit_logs", name)
