"""Schema-invariant checks that run against the migrated test DB (Phase 5)."""

from __future__ import annotations

from sqlalchemy import text

from app.db import SessionLocal


def _unique_constraints_on(db, table: str, column: str) -> int:
    return db.execute(
        text(
            """
            SELECT count(DISTINCT tc.constraint_name)
            FROM information_schema.table_constraints tc
            JOIN information_schema.constraint_column_usage ccu
              ON tc.constraint_name = ccu.constraint_name
             AND tc.table_schema = ccu.table_schema
            WHERE tc.table_name = :t
              AND tc.constraint_type = 'UNIQUE'
              AND ccu.column_name = :c
            """
        ),
        {"t": table, "c": column},
    ).scalar_one()


def test_stripe_customer_id_has_exactly_one_unique_constraint():
    """0001 created an inline UNIQUE and 0002 added a named one on the same
    column — the redundant one must be dropped (Phase 5 · 5A.4)."""
    db = SessionLocal()
    try:
        assert _unique_constraints_on(db, "organizations", "stripe_customer_id") == 1
    finally:
        db.close()


def test_traffic_events_has_no_direction_column():
    """`direction` was only ever written 'in' and nothing consumes 'out', so the
    column + its CHECK were dropped as dead weight (Phase 5 · 5A.5)."""
    db = SessionLocal()
    try:
        cols = db.execute(
            text("SELECT column_name FROM information_schema.columns "
                 "WHERE table_name = 'traffic_events'")
        ).scalars().all()
        assert "direction" not in cols
    finally:
        db.close()
