"""Create an organization and print its plaintext API key exactly once.

    python scripts/seed_org.py --name "Demo Corp"

Only the SHA-256 hash of the key is stored, so copy the printed key immediately
into the desktop app's settings and the demo's FOXY_API_KEY.

RLS note
────────
audit_logs has FORCE ROW LEVEL SECURITY, which means even the table owner is
subject to the org_isolation policy (org_id = current_setting('app.current_org')).
During a plain psql / alembic / script session that GUC is not set, so any INSERT
into audit_logs silently writes 0 rows and queries return nothing.

For the organizations table we have the same FORCE flag applied, so we must also
set the GUC before inserting there (the policy references the GUC too).  We do
this with SET LOCAL inside the same transaction so it auto-clears afterward.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Organization  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Create a Foxy Audit organization + API key.")
    ap.add_argument("--name", required=True, help="organization display name")
    args = ap.parse_args()

    # Generate a new UUID upfront so we can set the GUC *before* the INSERT.
    new_org_id = str(uuid.uuid4())
    key = "foxy_sk_" + secrets.token_hex(24)
    key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()

    db = SessionLocal()
    try:
        # SET LOCAL scopes the GUC to this transaction only.
        # This satisfies the RLS FORCE policy on audit_logs AND organizations.
        db.execute(text("SET LOCAL app.current_org = :oid"), {"oid": new_org_id})

        org = Organization(id=new_org_id, name=args.name, api_key_hash=key_hash)
        db.add(org)
        db.commit()
        db.refresh(org)
    except Exception as exc:
        db.rollback()
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()

    print(f"Created organization: {org.name}  (id={org.id})")
    print(f"FOXY_API_KEY={key}")
    print("Copy this key now — it is not recoverable (only its hash is stored).")


if __name__ == "__main__":
    main()
