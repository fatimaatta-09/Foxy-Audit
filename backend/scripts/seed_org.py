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
from app.models import Organization, User  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Create a Foxy Audit organization + API key.")
    ap.add_argument("--name", required=True, help="organization display name")
    ap.add_argument("--admin-email", help="also create an admin dashboard user")
    ap.add_argument("--admin-password", help="password for --admin-email")
    args = ap.parse_args()

    if bool(args.admin_email) != bool(args.admin_password):
        ap.error("--admin-email and --admin-password must be given together")

    # Generate a new UUID upfront so we can set the GUC *before* the INSERT.
    new_org_id = str(uuid.uuid4())
    key = "foxy_sk_" + secrets.token_hex(24)
    key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()

    admin_email = None
    db = SessionLocal()
    try:
        # Scope the RLS GUC to this transaction (SET LOCAL semantics). The
        # function form is used instead of "SET LOCAL app.current_org = :oid"
        # because a bare SET statement cannot take a bound parameter.
        db.execute(
            text("SELECT set_config('app.current_org', :oid, true)"),
            {"oid": new_org_id},
        )

        org = Organization(id=new_org_id, name=args.name, api_key_hash=key_hash)
        db.add(org)
        db.commit()
        db.refresh(org)

        if args.admin_email:
            import bcrypt
            ph = bcrypt.hashpw(
                args.admin_password.encode("utf-8"), bcrypt.gensalt()
            ).decode("utf-8")
            db.add(User(org_id=org.id, email=args.admin_email.strip().lower(),
                        password_hash=ph, role="admin"))
            db.commit()
            admin_email = args.admin_email.strip().lower()

        org_name, org_id = org.name, org.id
    except Exception as exc:
        db.rollback()
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()

    print(f"Created organization: {org_name}  (id={org_id})")
    print(f"FOXY_API_KEY={key}")
    print("Copy this key now — it is not recoverable (only its hash is stored).")
    if admin_email:
        print(f"Created admin user: {admin_email}  (role=admin)")


if __name__ == "__main__":
    main()
