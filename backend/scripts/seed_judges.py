"""Seed — or REPAIR — the hackathon JUDGE evaluation accounts (idempotent).

    docker compose exec foxy-backend python scripts/seed_judges.py

Gives each of the five judge accounts:
  • a fixed dashboard login (email + the shared password below),
  • 20,000 audit-event credits (monthly_log_quota),
  • plan_tier "pro" + subscription "active" so ingestion is never trial-blocked,
  • a peppered API key (printed ONCE, on first creation only).

Idempotent, and — since 2026-07-30 — SELF-HEALING. It used to `INSERT` and treat
an IntegrityError as "already fine", which meant an account that existed with a
different password hash could never be fixed by re-running it: the script printed
[skip] and changed nothing, while every judge got "Invalid email or password".
It now looks the row up first and re-asserts the documented state:

  • password_hash  → rehashed from JUDGE_PASSWORD (the actual login fix)
  • disabled       → False
  • org plan/quota → pro · active · 20,000, and never deleted/suspended

The API key is not reprinted on a repair — a judge mints a fresh one from the
dashboard Access page after logging in.

RLS: audit/org tables have FORCE ROW LEVEL SECURITY, so we SET the app.current_org
GUC before touching each org (same pattern as scripts/seed_org.py).
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sys
import uuid

try:
    _BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
except NameError:
    # Piped into an already-running container over stdin — `docker compose exec -T
    # foxy-backend python - < backend/scripts/seed_judges.py` — where there is no
    # __file__. WORKDIR is the backend root, so cwd is the right import base, and
    # this is how the repair reaches a box whose image predates it.
    _BACKEND = os.getcwd()
sys.path.insert(0, _BACKEND)

from sqlalchemy import func, select, text          # noqa: E402

from app.auth import hash_key                      # noqa: E402
from app.db import SessionLocal                    # noqa: E402
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


def _entitle(org: Organization) -> None:
    """Re-assert the evaluation entitlement. A suspended or soft-deleted org is
    refused at login with a 403 *before* the password is even considered, so the
    repair has to clear those too — not just the plan fields."""
    org.plan_tier = "pro"
    org.subscription_status = "active"
    org.monthly_log_quota = JUDGE_CREDITS
    org.deleted_at = None
    org.suspended = False


def seed_one(email: str, name: str) -> None:
    import bcrypt
    org_id = _org_id(email)

    db = SessionLocal()
    try:
        db.execute(text("SELECT set_config('app.current_org', :oid, true)"), {"oid": org_id})
        ph = bcrypt.hashpw(JUDGE_PASSWORD.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

        # Look the address up across ALL orgs, not just the derived one: login
        # scans every account with the address, so a stray row created elsewhere
        # (signup, invite) is part of this account's behaviour and gets repaired.
        users = db.execute(select(User).where(User.email == email)).scalars().all()

        if users:
            for user in users:
                user.password_hash = ph
                user.disabled = False
                org = db.get(Organization, user.org_id)
                if org is not None:
                    _entitle(org)
            db.commit()
            extra = f" ({len(users)} accounts share this address)" if len(users) > 1 else ""
            mfa = [u.email for u in users if u.mfa_enabled]
            print(f"  [fixed] {email:<24} password reset to '{JUDGE_PASSWORD}'{extra}")
            if mfa:
                print("          ⚠ MFA is ON for this account — sign-in will also "
                      "ask for an emailed code")
            return

        # No account with this address anywhere → create org (unless a previous
        # half-run left one behind), key, and user.
        org = db.get(Organization, org_id)
        key = None
        if org is None:
            key = "foxy_sk_" + secrets.token_hex(24)
            org = Organization(
                id=org_id, name=name,
                api_key_hash=hashlib.sha256(key.encode("utf-8")).hexdigest(),
                contact_email=email, plan_tier="pro", subscription_status="active",
                monthly_log_quota=JUDGE_CREDITS,
            )
            db.add(org)
            db.flush()
            db.add(ApiKey(org_id=org.id, name="primary", key_hash=hash_key(key),
                          key_prefix=key[:11] + "…" + key[-4:], status="active"))
        else:
            _entitle(org)
        db.add(User(org_id=org.id, email=email, password_hash=ph, role="admin"))
        db.commit()
        print(f"  [NEW]   {email:<24} pw={JUDGE_PASSWORD}  credits={JUDGE_CREDITS:,}")
        if key:
            print(f"          FOXY_API_KEY={key}   (shown once)")
        else:
            print("          org already existed — mint a key from the dashboard Access page")
    except Exception as exc:                      # noqa: BLE001
        db.rollback()
        print(f"  [FAIL]  {email}: {exc}", file=sys.stderr)
    finally:
        db.close()


def main() -> None:
    print(f"Seeding {len(JUDGES)} judge accounts (password '{JUDGE_PASSWORD}', "
          f"{JUDGE_CREDITS:,} credits each):\n")
    for email, name in JUDGES:
        seed_one(email, name)

    # Whether this database has any accounts at all is the first thing you want
    # to know when a login fails — a fresh/empty volume and a drifted password
    # produce the identical "Invalid email or password".
    db = SessionLocal()
    try:
        orgs = db.execute(select(func.count()).select_from(Organization)).scalar_one()
        users = db.execute(select(func.count()).select_from(User)).scalar_one()
        print(f"\nThis database now holds {orgs} organizations and {users} user accounts.")
    finally:
        db.close()

    print("\nDone. Judges log in at https://app.foxyaudit.tech with the email + password above,")
    print("then copy an API key from Access → paste into foxy_test.py / demos.")


if __name__ == "__main__":
    main()
