"""Bootstrap the FIRST platform-staff superadmin for the admin site ("site 3").

    python scripts/seed_staff.py --email you@foxy.audit --password 's3cret...'

Guarded: refuses to run if any staff already exist (so this can't be re-used as a
backdoor to silently mint staff accounts) unless --allow-additional is passed.
After the first superadmin exists, create further staff via POST /admin/v1/staff
(superadmin only), which records an admin_actions audit row.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bcrypt  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import StaffUser  # noqa: E402

_VALID_ROLES = {"viewer", "operator", "superadmin"}


def main() -> None:
    ap = argparse.ArgumentParser(description="Create a Foxy Audit platform-staff account.")
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--role", default="superadmin", choices=sorted(_VALID_ROLES))
    ap.add_argument("--allow-additional", action="store_true",
                    help="permit creating staff even when some already exist "
                         "(normally use the admin API for that)")
    args = ap.parse_args()

    if len(args.password) < 8:
        ap.error("--password must be at least 8 characters")
    email = args.email.strip().lower()
    if "@" not in email:
        ap.error("--email must be a valid email")

    db = SessionLocal()
    try:
        existing = db.execute(select(func.count()).select_from(StaffUser)).scalar_one()
        if existing and not args.allow_additional:
            print("ERROR: staff already exist — create further staff via POST "
                  "/admin/v1/staff (superadmin), or pass --allow-additional.",
                  file=sys.stderr)
            sys.exit(1)

        ph = bcrypt.hashpw(args.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        staff = StaffUser(email=email, password_hash=ph, platform_role=args.role)
        db.add(staff)
        db.commit()
        db.refresh(staff)
    except Exception as exc:                     # noqa: BLE001
        db.rollback()
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()

    print(f"Created staff user: {email}  (role={args.role}, id={staff.id})")
    print("Log in at /admin/v1/auth/login on the admin site.")


if __name__ == "__main__":
    main()
