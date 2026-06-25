"""Create an organization and print its plaintext API key exactly once.

    python scripts/seed_org.py --name "Demo Corp"

Only the SHA-256 hash of the key is stored, so copy the printed key immediately
into the desktop app's settings and the demo's FOXY_API_KEY.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import SessionLocal  # noqa: E402
from app.models import Organization  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Create a Foxy Audit organization + API key.")
    ap.add_argument("--name", required=True, help="organization display name")
    args = ap.parse_args()

    key = "foxy_sk_" + secrets.token_hex(24)
    key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()

    db = SessionLocal()
    try:
        org = Organization(name=args.name, api_key_hash=key_hash)
        db.add(org)
        db.commit()
        db.refresh(org)
    finally:
        db.close()

    print(f"Created organization: {org.name}  (id={org.id})")
    print(f"FOXY_API_KEY={key}")
    print("Copy this key now — it is not recoverable (only its hash is stored).")


if __name__ == "__main__":
    main()
