"""init: organizations + audit_logs + Row-Level Security

Revision ID: 0001
Revises:
Create Date: 2026-06-26

Fix: billing columns (plan_tier, stripe_*, key_rotated_at) added here so that
models.py can map them without requiring 0002 to have run first.
Migration 0002 retains its ADD COLUMN statements but wraps them in DO $$ BEGIN
/ EXCEPTION WHEN duplicate_column THEN END; $$ blocks so re-running on a fresh
DB (where these columns already exist) is a no-op.
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

    op.execute(
        """
        CREATE TABLE organizations (
            id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name                   TEXT NOT NULL,
            api_key_hash           VARCHAR(64) NOT NULL UNIQUE,
            -- Billing / Stripe columns (also in 0002 for idempotency)
            plan_tier              VARCHAR(32),
            stripe_customer_id     VARCHAR(255) UNIQUE,
            stripe_subscription_id VARCHAR(255),
            subscription_status    VARCHAR(32),
            key_rotated_at         TIMESTAMPTZ,
            created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )

    op.execute(
        """
        CREATE TABLE audit_logs (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id        UUID NOT NULL REFERENCES organizations(id),
            seq           BIGINT NOT NULL,
            prompt_hash   VARCHAR(64) NOT NULL,
            response_hash VARCHAR(64) NOT NULL,
            token_count   INTEGER NOT NULL,
            policy_tag    VARCHAR(32) NOT NULL,
            prev_hash     VARCHAR(64) NOT NULL,
            chain_hash    VARCHAR(64) NOT NULL,
            gemini_verdict JSONB,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_audit_org_seq UNIQUE (org_id, seq)
        );
        """
    )
    op.execute("CREATE INDEX ix_audit_logs_org_id ON audit_logs(org_id);")

    # Tenant isolation enforced at the database layer. FORCE makes the policy
    # apply even to the table owner, so a single DATABASE_URL role still gets
    # RLS. current_setting(..., true) = missing_ok, so unset GUC -> no rows
    # rather than an error (e.g. during seeding before a tenant is scoped).
    op.execute("ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE audit_logs FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY org_isolation ON audit_logs
            USING      (org_id = current_setting('app.current_org', true)::uuid)
            WITH CHECK (org_id = current_setting('app.current_org', true)::uuid);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit_logs;")
    op.execute("DROP TABLE IF EXISTS organizations;")

