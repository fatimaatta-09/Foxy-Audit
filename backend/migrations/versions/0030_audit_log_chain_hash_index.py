"""audit_logs (org_id, chain_hash) index — fast per-hash lookup (Phase 2)

Backs GET /v1/verify/hash/{hash} (the dashboard "paste any hash to check status"
card). chain_hash was previously unindexed; this composite index keeps the lookup
org-scoped and O(log n) instead of a sequential scan. Non-unique — a chain hash is
effectively unique per org, but we don't enforce it at the DB level here.

Revision ID: 0030
Revises: 0029
"""

from __future__ import annotations

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_audit_logs_org_chain_hash", "audit_logs", ["org_id", "chain_hash"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_org_chain_hash", table_name="audit_logs")
