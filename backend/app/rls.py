"""Can this connection see across organisations at all? — register #122.

THE FAILURE THIS EXISTS TO MAKE LOUD
------------------------------------
Seventeen tables in this schema carry ENABLE + FORCE ROW LEVEL SECURITY, and
every policy on them is

    org_id = current_setting('app.current_org', true)::uuid

The `true` is `missing_ok`, so an UNSET GUC yields NULL and `org_id = NULL`
matches nothing. Staff paths deliberately never set that GUC — `require_staff`
does not call `_scope_org` — so every cross-org aggregate on the admin console
works only because the connection role is a SUPERUSER, and a superuser is exempt
from RLS even under FORCE.

Take that exemption away and none of those queries ERROR. **They return zero
rows.** The quota meter reads 0, the health page reports no organisation
unanchored, revenue reads 0. Every number turns reassuring at the exact moment
the platform is most misconfigured, and `/health/ready` reports `pending: 0,
failed: 0` — it reads `audit_logs` twice itself — which is the shape of a
perfectly healthy deployment.

WHY THIS ASKS THE PLANNER AND NOT THE DATA
------------------------------------------
The obvious shape — compare an unscoped read of an RLS table against an
unprotected table that references it — has two problems this does not have:

  · It cannot tell "no rows" from "cannot see rows". An empty `organizations`
    is a legitimate state on a fresh deployment, so that check either fires on
    every new install (an alarm people learn to ignore, and then the real one is
    ignored too) or needs a heuristic to suppress itself.
  · It costs table scans on an endpoint that is polled.

`row_security_active()` is Postgres's own answer to "will a policy filter me on
this table", and it accounts for superuser, BYPASSRLS, table ownership and FORCE
together rather than reassembling that rule here and getting it subtly wrong.
It touches no application row, so it is silent on an empty database by
construction rather than by suppression.

WHY IT IS NOT CIRCULAR
----------------------
It reads `pg_class` and a planner predicate — not rows behind a policy. System
catalogs are not RLS-protected, so a confined role reads them perfectly well and
gets the honest answer about itself. Measured on the test database: as `foxy_app`
this returns all seventeen tables, as the superuser `foxy` it returns none. A
check that a confined role could not see would report "fine" for exactly the
reason everything else does, which is the trap this avoids.

⚠ IT IS TRANSACTION-LOCAL, AND THAT IS THE POINT. `current_user` and
`row_security_active()` both track `SET LOCAL ROLE`, so this reports the role the
session is ACTUALLY running as rather than the one spelled in DATABASE_URL —
verified, both flip inside a transaction and revert at COMMIT.

⚠ NOT A SECURITY CONTROL. Answering "blind" is a misconfiguration report, not a
permission decision; nothing gates on it. `_scope_org` is what confines the
customer path, and it already does — see auth.py.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

# One round trip: the effective role, and every RLS table whose policies would
# filter it right now. No table list is hardcoded — a table that gains RLS in a
# later migration is covered the day it lands, which a fixed list would not be.
_VISIBILITY_SQL = text("""
    SELECT current_user AS role,
           coalesce((
               SELECT array_agg(c.relname ORDER BY c.relname)
                 FROM pg_class c
                 JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND c.relkind = 'r'
                  AND c.relrowsecurity
                  AND row_security_active(c.oid)
           ), '{}') AS blinded
""")


def cross_org_visibility(db: Session) -> dict:
    """Report whether unscoped (staff) reads can see every organisation's rows.

    `status` is one of:
      ok      — no RLS policy applies to this role; cross-org reads are honest.
      blind   — at least one table would filter this role. Every unscoped
                aggregate over it returns zero rows and raises nothing.
      unknown — the catalog itself could not be read. Reported rather than
                guessed: a health check that invents "ok" when it could not
                measure is the same class of lie it is here to catch.
    """
    try:
        row = db.execute(_VISIBILITY_SQL).one()
    except Exception:  # noqa: BLE001 — health must report, never raise
        db.rollback()
        return {"status": "unknown", "role": None, "blinded_tables": [],
                "detail": "could not read the row-security catalog"}

    role, blinded = row[0], list(row[1] or [])
    if not blinded:
        return {"status": "ok", "role": role, "blinded_tables": [],
                "detail": "cross-organisation reads are unfiltered for this role"}
    return {
        "status": "blind",
        "role": role,
        "blinded_tables": blinded,
        # Names the consequence, not the mechanism: whoever reads this is
        # looking at a console full of zeros and needs to know they are not data.
        "detail": (
            f"{role} is subject to row-level security on {len(blinded)} table(s) "
            f"({', '.join(blinded[:4])}"
            f"{', …' if len(blinded) > 4 else ''}). Unscoped staff reads return "
            f"zero rows instead of failing, so every cross-organisation figure "
            f"on this deployment is understated."
        ),
    }
