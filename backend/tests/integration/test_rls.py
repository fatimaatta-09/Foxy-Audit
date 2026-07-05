"""RLS is actually enforced for scoped (customer) transactions — Phase 5 · 5B.2.

The app connects as the `foxy` superuser, which BYPASSES `FORCE ROW LEVEL
SECURITY`, so tenant isolation historically rested entirely on app-code
`WHERE org_id` clauses. These tests prove the defense-in-depth layer: a scoped
transaction runs as the confined `foxy_app` role (NOLOGIN, NOBYPASSRLS), so even
a raw cross-org read with NO where-clause returns only the scoped org's rows —
while an unscoped (staff) transaction still sees every org.
"""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import text

from app.auth import _scope_org
from app.db import SessionLocal


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _ingest(client, org, n: int) -> None:
    rows = [{"prompt_hash": _h(f"{org['org_id']}-p{i}"),
             "response_hash": _h(f"{org['org_id']}-r{i}"),
             "token_count": 100, "policy_tag": "chat"} for i in range(n)]
    assert client.post("/v1/logs/batch", json=rows,
                       headers=org["auth"]).status_code == 202


def test_scoped_transaction_runs_as_confined_nonbypass_role():
    """After _scope_org the transaction's effective role is the confined
    foxy_app — not the superuser — so FORCE RLS applies to it."""
    db = SessionLocal()
    try:
        _scope_org(db, uuid.uuid4())
        cur, is_super, can_bypass = db.execute(text(
            "SELECT current_user, rolsuper, rolbypassrls "
            "FROM pg_roles WHERE rolname = current_user")).one()
        assert cur == "foxy_app"
        assert not is_super       # not a superuser
        assert not can_bypass     # cannot bypass RLS
    finally:
        db.close()


def test_rls_hides_other_orgs_rows_from_scoped_read(make_org, client):
    """A raw `SELECT ... FROM audit_logs` (no WHERE) under a scoped transaction
    returns only the scoped org's rows — proving RLS, not the app-code WHERE,
    is what isolates tenants."""
    a, b = make_org(), make_org()
    _ingest(client, a, 3)
    _ingest(client, b, 2)

    db = SessionLocal()
    try:
        _scope_org(db, a["org_id"])
        n = db.execute(text("SELECT count(*) FROM audit_logs")).scalar()
        assert n == 3             # only org A — org B's 2 rows are invisible
    finally:
        db.close()


def test_unscoped_transaction_still_sees_all_orgs(make_org, client):
    """The staff path never scopes; that transaction stays superuser and still
    reads across every org (what admin_stats / admin_orgs rely on)."""
    a, b = make_org(), make_org()
    _ingest(client, a, 3)
    _ingest(client, b, 2)

    db = SessionLocal()
    try:
        n = db.execute(text("SELECT count(*) FROM audit_logs")).scalar()
        assert n == 5             # no scope → all orgs visible
    finally:
        db.close()
