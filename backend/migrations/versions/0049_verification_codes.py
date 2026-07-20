"""verification_codes — generic emailed-code step-up for customer danger actions (dashboard P15).

Generalizes the key-regen OTP into a dedicated table so step-up NEVER collides with login MFA (which
uses users.mfa_code_*). One row per emailed code: purpose + SHA-256 hash + expiry + consumed marker.
Confirming a code mints a short-lived session grant (require_step_up_user). Per-org: org_isolation RLS.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "verification_codes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("org_id", UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_verification_codes_user", "verification_codes", ["user_id"])
    op.execute("ALTER TABLE verification_codes ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE verification_codes FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY org_isolation ON verification_codes
            USING      (org_id = current_setting('app.current_org', true)::uuid)
            WITH CHECK (org_id = current_setting('app.current_org', true)::uuid);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS verification_codes")
