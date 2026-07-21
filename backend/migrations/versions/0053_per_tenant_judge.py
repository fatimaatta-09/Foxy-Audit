"""per-tenant AI Judge selection (provider, key mode, encrypted BYOK keys)

Each org chooses which judge grades their events and whose key pays for it.
All four columns are additive and nullable/defaulted, so existing rows keep
working untouched and no chain snapshot hash changes (the policy snapshot
deliberately excludes these fields — see app/policy_snapshot.py).

The two *_key_enc columns hold Fernet ciphertext ONLY (app/crypto_secrets.py).

Revision ID: 0053
Revises: 0052
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("org_policies", sa.Column(
        "judge_provider", sa.String(16), nullable=False, server_default="gemini"))
    op.add_column("org_policies", sa.Column(
        "judge_key_mode", sa.String(16), nullable=False, server_default="own"))
    op.add_column("org_policies", sa.Column("gemini_key_enc", sa.Text(), nullable=True))
    op.add_column("org_policies", sa.Column("openai_key_enc", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_org_policies_judge_provider", "org_policies",
        "judge_provider IN ('gemini', 'openai', 'both')")
    op.create_check_constraint(
        "ck_org_policies_judge_key_mode", "org_policies",
        "judge_key_mode IN ('own', 'platform')")


def downgrade() -> None:
    op.drop_constraint("ck_org_policies_judge_key_mode", "org_policies", type_="check")
    op.drop_constraint("ck_org_policies_judge_provider", "org_policies", type_="check")
    op.drop_column("org_policies", "openai_key_enc")
    op.drop_column("org_policies", "gemini_key_enc")
    op.drop_column("org_policies", "judge_key_mode")
    op.drop_column("org_policies", "judge_provider")
