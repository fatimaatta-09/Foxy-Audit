"""Seed the hackathon JUDGE evaluation accounts (idempotent).

    docker compose exec foxy-backend python scripts/seed_judges.py

Creates five ready-to-use judge accounts, each with:
  • a fixed dashboard login (email + the shared password below),
  • 20,000 audit-event credits (monthly_log_quota),
  • plan_tier "pro" + subscription "active" so ingestion is never trial-blocked,
  • a peppered API key (printed ONCE on first creation).

Idempotent: org ids are derived deterministically from the email (uuid5), so
re-running skips accounts that already exist rather than duplicating them. On a
re-run the API key is NOT reprinted — a judge can always mint a fresh one from
the dashboard Access page after logging in.

RLS: audit/org tables have FORCE ROW LEVEL SECURITY, so we SET the app.current_org
GUC before inserting each org (same pattern as scripts/seed_org.py).
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text                      # noqa: E402
from sqlalchemy.exc import IntegrityError        # noqa: E402

from app.auth import hash_key                    # noqa: E402
from app.db import SessionLocal                  # noqa: E402
from app.models import ApiKey, Organization, User  # noqa: E402

# ── The judge accounts. Emails are fixed; the password is shared for all five
#    (throwaway evaluation accounts). Documented in the repo-root JUDGES.html. ──
JUDGE_PASSWORD = "Foxy-Judge-2026"
JUDGE_CREDITS = 20_000
JUDGES = [
    ("judge1@foxyaudit.tech", "Judge 1"),
    ("judge2@foxyaudit.tech", "Judge 2"),
    ("judge3@foxyaudit.tech", "Judge 3"),
    ("judge4@foxyaudit.tech", "Judge 4"),
    ("judge5@foxyaudit.tech", "Judge 5"),
]
_NS = uuid.uuid5(uuid.NAMESPACE_DNS, "foxyaudit.tech/judges")


def _org_id(email: str) -> str:
    return str(uuid.uuid5(_NS, email))


def seed_one(email: str, name: str) -> None:
    import bcrypt
    org_id = _org_id(email)
    key = "foxy_sk_" + secrets.token_hex(24)
    key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()

    db = SessionLocal()
    try:
        db.execute(text("SELECT set_config('app.current_org', :oid, true)"), {"oid": org_id})
        org = Organization(
            id=org_id, name=name, api_key_hash=key_hash,
            contact_email=email, plan_tier="pro", subscription_status="active",
            monthly_log_quota=JUDGE_CREDITS,
        )
        db.add(org)
        db.flush()                                # surfaces a PK clash before we do more work
        db.add(ApiKey(org_id=org.id, name="primary", key_hash=hash_key(key),
                      key_prefix=key[:11] + "…" + key[-4:], status="active"))
        ph = bcrypt.hashpw(JUDGE_PASSWORD.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        db.add(User(org_id=org.id, email=email, password_hash=ph, role="admin"))
        db.commit()
        print(f"  [NEW]  {email:<24} pw={JUDGE_PASSWORD}  credits={JUDGE_CREDITS:,}")
        print(f"         FOXY_API_KEY={key}   (shown once)")
    except IntegrityError:
        db.rollback()
        print(f"  [skip] {email:<24} already exists — mint a key from the dashboard Access page")
    except Exception as exc:                      # noqa: BLE001
        db.rollback()
        print(f"  [FAIL] {email}: {exc}", file=sys.stderr)
    finally:
        db.close()


def main() -> None:
    print(f"Seeding {len(JUDGES)} judge accounts (password '{JUDGE_PASSWORD}', "
          f"{JUDGE_CREDITS:,} credits each):\n")
    for email, name in JUDGES:
        seed_one(email, name)
    print("\nDone. Judges log in at https://app.foxyaudit.tech with the email + password above,")
    print("then copy an API key from Access → paste into foxy_test.py / demos.")


if __name__ == "__main__":
    main()
