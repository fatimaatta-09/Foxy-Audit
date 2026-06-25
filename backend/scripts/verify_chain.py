"""Recompute an org's hash chain and print a per-row PASS/FAIL table.

    python scripts/verify_chain.py                       # first/only org
    python scripts/verify_chain.py --api-key foxy_sk_...  # specific org

Use after manually tampering a row in psql to see the chain break.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, text  # noqa: E402

from app.chain import GENESIS_HASH, compute_chain_hash  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import AuditLog, Organization  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-key", help="org API key; omit to verify the first/only org")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        if args.api_key:
            kh = hashlib.sha256(args.api_key.encode("utf-8")).hexdigest()
            org = db.execute(
                select(Organization).where(Organization.api_key_hash == kh)
            ).scalar_one_or_none()
        else:
            org = db.execute(
                select(Organization).order_by(Organization.created_at)
            ).scalars().first()

        if org is None:
            print("No organization found.")
            return

        # FORCE RLS filters even this script's role, so scope the GUC to read rows.
        db.execute(text("SELECT set_config('app.current_org', :o, true)"), {"o": str(org.id)})
        rows = db.execute(
            select(AuditLog).where(AuditLog.org_id == org.id).order_by(AuditLog.seq.asc())
        ).scalars().all()

        print(f"Verifying {len(rows)} rows for org {org.name} ({org.id})\n")
        prev = GENESIS_HASH
        broken = False
        for r in rows:
            expected = compute_chain_hash(
                org_id=org.id, prompt_hash=r.prompt_hash, response_hash=r.response_hash,
                token_count=r.token_count, policy_tag=r.policy_tag, seq=r.seq, prev_hash=prev,
            )
            ok = expected == r.chain_hash
            print(f"  seq {r.seq:>4}  {'PASS' if ok else 'FAIL'}  {r.chain_hash[:16]}...")
            if not ok:
                print(f"        expected {expected[:16]}... but stored {r.chain_hash[:16]}...")
                broken = True
                break
            prev = r.chain_hash

        print("\nCHAIN INTACT" if not broken else "\nCHAIN BROKEN")
    finally:
        db.close()


if __name__ == "__main__":
    main()
