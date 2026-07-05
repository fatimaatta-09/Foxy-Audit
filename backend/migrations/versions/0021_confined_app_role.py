"""confined app role (foxy_app) so FORCE RLS is actually enforced — Phase 5 · 5B.2

The app connects as the `foxy` superuser, which BYPASSES `FORCE ROW LEVEL
SECURITY`. Org-scoped transactions now `SET LOCAL ROLE foxy_app` (see
auth._scope_org) so the existing RLS policies on audit_logs / invoices /
usage_daily / chain_anchors genuinely filter rows.

`foxy_app` is NOLOGIN (only ever assumed via SET ROLE from the app's superuser
connection — it never authenticates on its own) and NOBYPASSRLS. Its table
grants are deliberately BROAD: RLS on the four tenant tables is the isolation
boundary, not the grants, so we grant DML across the schema and let the policies
constrain the rows a scoped transaction can see.

Revision ID: 0021
Revises: 0020
"""

from __future__ import annotations

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

ROLE = "foxy_app"


def upgrade() -> None:
    # Roles are CLUSTER-wide, but grants are per-DATABASE. Guard the CREATE so
    # running migrations against a second DB in the same cluster (e.g. foxy and
    # foxy_pytest) doesn't error on "role already exists"; re-grant idempotently.
    op.execute(f"""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}') THEN
            CREATE ROLE {ROLE} NOLOGIN NOBYPASSRLS;
        END IF;
    END $$;
    """)
    op.execute(f"GRANT USAGE ON SCHEMA public TO {ROLE};")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {ROLE};")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {ROLE};")
    # Tables/sequences created by LATER migrations inherit the same grants (the
    # grantor is the migration superuser, which owns everything it creates).
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
               f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {ROLE};")
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
               f"GRANT USAGE, SELECT ON SEQUENCES TO {ROLE};")


def downgrade() -> None:
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
               f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {ROLE};")
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
               f"REVOKE USAGE, SELECT ON SEQUENCES FROM {ROLE};")
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {ROLE};")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLE};")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {ROLE};")
    # Deliberately DO NOT `DROP ROLE foxy_app`: the role is cluster-wide and may
    # still own grants in a sibling database, where DROP would fail. Leaving a
    # privilege-less NOLOGIN role behind is harmless and keeps downgrade portable.
