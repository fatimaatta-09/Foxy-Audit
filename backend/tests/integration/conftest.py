"""Shared fixtures for the Postgres-backed integration tests (Phase 3 Track D).

These exercise the real FastAPI app against a real Postgres so they cover what the
Postgres-free test_chain.py can't: auth/RBAC, tenant isolation, HMAC keys + legacy
fallback, ingest→chain→verify, /health/ready, policy→judge wiring, and anchoring.

Run locally:
    DATABASE_URL=postgresql+psycopg://foxy:foxy@localhost:5432/foxy_pytest \
    API_KEY_PEPPER=testpepper pytest backend/tests/integration -q

The env below is set BEFORE importing the app so app.db binds the engine to the
test database and auth uses a known pepper.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import uuid

# ── env must be set before importing the app ────────────────────────────────
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://foxy:foxy@localhost:5432/foxy_pytest")
os.environ.setdefault("API_KEY_PEPPER", "testpepper")
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("GEMINI_API_KEY", "")          # judge fail-open (no network in tests)
os.environ.setdefault("ANCHOR_PROVIDER", "stub")     # deterministic anchoring
os.environ.setdefault("ANCHOR_ENABLED", "false")     # no background worker in tests
# Off by default so the async traffic writer never contaminates another test; the
# dedicated traffic test flips get_settings().traffic_tracking_enabled on itself.
os.environ.setdefault("TRAFFIC_TRACKING_ENABLED", "false")
os.environ.setdefault("STAFF_SESSION_SECRET", "test-staff-session-secret")

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _BACKEND_DIR)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.auth import hash_key  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ApiKey, Organization, StaffUser, User  # noqa: E402

try:
    import bcrypt
except ImportError:  # pragma: no cover
    bcrypt = None

_DATA_TABLES = (
    "organizations, users, api_keys, audit_logs, chain_anchors, org_policies, "
    "staff_users, admin_actions, traffic_events, marketing_leads, stripe_events, "
    "invoices, usage_daily"
)


@pytest.fixture(scope="session", autouse=True)
def _migrate():
    """Bring the test DB to head once per session (also proves migrations apply)."""
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_BACKEND_DIR, env=os.environ, check=True,
    )
    yield


@pytest.fixture(autouse=True)
def _clean_db():
    """Reset all tenant data before each test so tests are independent. Runs as the
    superuser 'foxy', which bypasses RLS, so TRUNCATE clears every org's rows.
    Also resets the in-memory rate limiters — every TestClient shares one client
    IP, so the staff login's 10/minute brute-force cap would otherwise trip
    spuriously partway through a test session."""
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {_DATA_TABLES} RESTART IDENTITY CASCADE"))
        conn.execute(text("UPDATE worker_heartbeat SET beat_at = NULL WHERE id = 1"))
    from app.routers import auth_staff as _auth_staff
    from app.routers import logs as _logs
    _logs.limiter.reset()
    _auth_staff.limiter.reset()
    yield


@pytest.fixture
def client() -> TestClient:
    """An unauthenticated TestClient (its own cookie jar)."""
    return TestClient(app)


@pytest.fixture
def make_org():
    """Factory: create an org + peppered api_key + admin user. Returns a dict with
    api_key (plaintext), org_id, admin_email, admin_password. Mirrors seed_org."""
    counter = {"n": 0}

    def _make(name: str | None = None, admin_password: str = "adminpass123",
              admin_role: str = "admin"):
        counter["n"] += 1
        n = counter["n"]
        name = name or f"Org {n}"
        admin_email = f"admin{n}@test.dev"
        org_id = uuid.uuid4()
        key = "foxy_sk_" + uuid.uuid4().hex + uuid.uuid4().hex[:16]
        legacy_hash = hashlib.sha256(key.encode()).hexdigest()

        db = SessionLocal()
        try:
            db.add(Organization(id=org_id, name=name, api_key_hash=legacy_hash))
            db.commit()
            db.add(ApiKey(org_id=org_id, name="primary", key_hash=hash_key(key),
                          key_prefix=key[:11] + "…" + key[-4:], status="active"))
            ph = bcrypt.hashpw(admin_password.encode(), bcrypt.gensalt()).decode()
            db.add(User(org_id=org_id, email=admin_email, password_hash=ph, role=admin_role))
            db.commit()
        finally:
            db.close()
        return {
            "org_id": str(org_id), "api_key": key,
            "admin_email": admin_email, "admin_password": admin_password,
            "auth": {"Authorization": f"Bearer {key}"},
        }

    return _make


@pytest.fixture
def login():
    """Factory: return a fresh TestClient logged in as (email, password)."""
    def _login(email: str, password: str) -> TestClient:
        c = TestClient(app)
        r = c.post("/v1/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
        return c
    return _login


@pytest.fixture
def add_user():
    """Factory: create an extra user in an org directly (bypassing the invite API)."""
    def _add(org_id: str, email: str, password: str, role: str = "member",
             disabled: bool = False):
        db = SessionLocal()
        try:
            ph = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
            db.add(User(org_id=uuid.UUID(org_id), email=email, password_hash=ph,
                        role=role, disabled=disabled))
            db.commit()
        finally:
            db.close()
    return _add


@pytest.fixture
def make_staff():
    """Factory: create a platform-staff account. Returns email/password/role/id.
    Staff are NOT org-scoped — this is the admin-site ("site 3") channel."""
    counter = {"n": 0}

    def _make(role: str = "superadmin", password: str = "staffpass123"):
        counter["n"] += 1
        email = f"staff{counter['n']}@foxy.audit"
        db = SessionLocal()
        try:
            ph = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
            s = StaffUser(email=email, password_hash=ph, platform_role=role)
            db.add(s)
            db.commit()
            db.refresh(s)
            sid = str(s.id)
        finally:
            db.close()
        return {"id": sid, "email": email, "password": password, "role": role}

    return _make


@pytest.fixture
def staff_login():
    """Factory: a fresh TestClient logged in to the ADMIN site as a staff user."""
    def _login(email: str, password: str) -> TestClient:
        c = TestClient(app)
        r = c.post("/admin/v1/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, f"staff login failed: {r.status_code} {r.text}"
        return c
    return _login
