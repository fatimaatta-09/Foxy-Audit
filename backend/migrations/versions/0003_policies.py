"""policies: add org_policies table for Active Policy Configuration (Req #1)

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-29

Adds a per-org policy configuration table so non-technical executives can
flip compliance toggles without touching code — Core Requirement #1.

Schema:
  org_id               UUID FK → organizations.id (1:1 relationship)
  pii_detection        bool  — flag interactions that may contain PII
  prompt_injection     bool  — flag prompt-injection signatures
  regulated_data_mode  bool  — stricter evaluation for HIPAA/PCI/SOC2 orgs
  max_token_threshold  int   — flag interactions exceeding this token count
  updated_at           timestamptz
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE org_policies (
            org_id               UUID PRIMARY KEY REFERENCES organizations(id),
            pii_detection        BOOLEAN NOT NULL DEFAULT TRUE,
            prompt_injection     BOOLEAN NOT NULL DEFAULT TRUE,
            regulated_data_mode  BOOLEAN NOT NULL DEFAULT FALSE,
            max_token_threshold  INTEGER NOT NULL DEFAULT 50000,
            updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS org_policies;")
