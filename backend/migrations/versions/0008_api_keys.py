"""api_keys: named multi-key HMAC table + users.disabled

Phase 3 A2. Adds the `api_keys` table (many named keys per org, stored as
HMAC-SHA256(pepper, key) so a DB leak can't recover a usable key) and a
`disabled` flag on `users` for offboarding.

No RLS on api_keys — auth.require_org resolves the org from a key *before* the
app.current_org GUC is set, so tenant isolation for key management is enforced
in application code (WHERE org_id = ...), exactly like `organizations`/`users`.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),   # pgcrypto (0001)
        sa.Column("org_id", UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("key_prefix", sa.String(64), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()")),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
    )
    op.create_index("ix_api_keys_org_id", "api_keys", ["org_id"])
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"])

    op.add_column(
        "users",
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("users", "disabled")
    op.drop_index("ix_api_keys_key_hash", table_name="api_keys")
    op.drop_index("ix_api_keys_org_id", table_name="api_keys")
    op.drop_table("api_keys")
