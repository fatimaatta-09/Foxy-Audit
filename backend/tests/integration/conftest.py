"""Shared fixtures for the Postgres-backed integration tests (Phase 3 Track D).

These exercise the real FastAPI app against a real Postgres so they cover what the
Postgres-free test_chain.py can't: auth/RBAC, tenant isolation, HMAC keys + legacy
fallback, ingest→chain→verify, /health/ready, policy→judge wiring, and anchoring.

Run locally:
    DATABASE_URL=postgresql+psycopg://foxy:foxy@localhost:5432/foxy_pytest \
    API_KEY_PEPPER=testpepper pytest backend/tests/integration -q

The env below is set BEFORE importing the app so app.db binds the engine to the
test database and auth uses a known pepper.

⚠ THE TEST DATABASE'S TIMEZONE IS NOT PINNED HERE, AND THAT IS DELIBERATE
(#123). `date_trunc('day', <timestamptz>)` resolves in the SESSION timezone, so
where this suite runs used to decide whether it could see that class of bug at
all — the developer's cluster is Asia/Karachi (UTC+5) and CI was UTC, which is
why the class reproduced locally and vanished in the only place that gates.

CI now runs the server at America/Los_Angeles (see ci.yml), west of UTC, because
the two skew in OPPOSITE directions: east rolls a late-evening UTC event forward
a day, west rolls an early-morning one back. Copying the dev zone would have
left half the class invisible in both places.

Nothing here pins the local cluster, for two reasons. A repo cannot set another
machine's initdb timezone — it can only say what to expect. And more to the
point, correctness no longer depends on it: test_timezone_independence.py sets
the zone per session and drives each endpoint under all three. MEASURED, and it
is the finding of that phase: with CI pinned but no such test, the full suite
passed 1139/1139 under America/Los_Angeles. The ambient skew detected nothing,
because no other test places an event near a UTC midnight. The pin is
defence-in-depth for expressions nobody wrote a test for; the explicit tests are
what actually catch the class.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
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
# P6c · avatars are the first thing this app writes to disk. Point it at a temp
# directory per run so the suite never touches the real /data/avatars mount and
# two runs cannot see each other's files.
os.environ.setdefault(
    "AVATAR_DIR", os.path.join(tempfile.gettempdir(), "foxy-pytest-avatars"))
# BYOK provider-key encryption (Fernet). A fixed urlsafe-b64 32-byte test key so
# the encrypted-at-rest path is exercised; tests that need the "deployment has no
# key" fail-closed path blank it with monkeypatch.
os.environ.setdefault("PROVIDER_KEY_ENCRYPTION_KEY",
                      "Zm94eS10ZXN0LXByb3ZpZGVyLWtleS0zMi1ieXRlISE=")

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
    "invoices, usage_daily, login_events, platform_config, platform_announcements, "
    "staff_notifications, staff_sessions, evaluation_campaigns, evaluation_redemptions"
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
            "org_id": str(org_id), "api_key": key, "name": name,
            "admin_email": admin_email, "admin_password": admin_password,
            "auth": {"Authorization": f"Bearer {key}"},
        }

    return _make


@pytest.fixture
def step_up_user():
    """Grant a ~10-min customer step-up (P15) on an already-authenticated client, via a patched OTP.
    Every gated danger mutation 403s (`step_up_required`) until this runs."""
    import app.mfa as _mfa

    def _grant(client: TestClient) -> None:
        orig = _mfa.new_otp
        _mfa.new_otp = lambda: "000000"
        try:
            assert client.post("/v1/auth/step-up/request").status_code == 200
            r = client.post("/v1/auth/step-up/confirm", json={"code": "000000"})
            assert r.status_code == 200, f"step-up confirm failed: {r.status_code} {r.text}"
        finally:
            _mfa.new_otp = orig

    return _grant


@pytest.fixture
def revealed_org_id(step_up_user):
    """The workspace id, from behind the P3 §7.1 step-up gate.

    Now that `/v1/auth/me` — and login, MFA, SSO and handoff — no longer carry
    org_id, this is the only way a session can answer "which tenant did I land
    in?", which is the question a good few tenant-isolation tests are really
    asking.

    Echoes the double-submit CSRF token first: a client built by `login` already
    has one, a client that authenticated any other way does not, and CSRF
    refuses the POST before authentication is even considered."""
    def _reveal(client) -> str:
        csrf = client.cookies.get("foxy_csrf")
        if csrf:
            client.headers.update({"X-CSRF-Token": csrf})
        step_up_user(client)
        r = client.post("/v1/account/org-id")
        assert r.status_code == 200, f"org-id reveal failed: {r.status_code} {r.text}"
        return r.json()["org_id"]

    return _reveal


@pytest.fixture
def login(step_up_user):
    """Factory: a fresh TestClient logged in as (email, password) AND granted step-up by default (P15),
    so the many existing danger-mutation tests keep working. Pass with_step_up=False for the no-grant
    path (the dedicated step-up gate tests)."""
    def _login(email: str, password: str, with_step_up: bool = True) -> TestClient:
        c = TestClient(app)
        r = c.post("/v1/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
        csrf = c.cookies.get("foxy_csrf")            # echo the double-submit token on later writes
        if csrf:
            c.headers.update({"X-CSRF-Token": csrf})
        if with_step_up:
            step_up_user(c)
        return c
    return _login


@pytest.fixture
def configure_judge():
    """Factory: set an org's per-tenant judge routing (0053) straight in the DB.

    An org with no provider key and no premium plan gets NO AI judge — that is the
    tier matrix, not a bug — so any test that wants the judge to actually run must
    give the org a key (BYOK) or premium + platform mode.
    """
    from tests.integration.judge_helpers import give_judge_key
    return give_judge_key


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
def staff_login(step_up):
    """Factory: a TestClient logged in to the ADMIN site AND granted step-up by default, so the
    many existing danger-mutation tests keep working. Pass `with_step_up=False` for the no-grant
    path (the dedicated step-up tests)."""
    def _login(email: str, password: str, with_step_up: bool = True) -> TestClient:
        c = TestClient(app)
        r = c.post("/admin/v1/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, f"staff login failed: {r.status_code} {r.text}"
        csrf = c.cookies.get("foxy_csrf")            # echo the double-submit token on later writes
        if csrf:
            c.headers.update({"X-CSRF-Token": csrf})
        if with_step_up:
            step_up(c)
        return c
    return _login


@pytest.fixture
def step_up():
    """Grant a ~10-min step-up on an already-authenticated staff client, via a patched OTP.
    Every audited danger mutation 403s (`step_up_required`) until this runs."""
    import app.mfa as _mfa

    def _grant(client: TestClient) -> None:
        orig = _mfa.new_otp
        _mfa.new_otp = lambda: "000000"
        try:
            assert client.post("/admin/v1/auth/step-up/request").status_code == 200
            r = client.post("/admin/v1/auth/step-up/confirm", json={"code": "000000"})
            assert r.status_code == 200, f"step-up confirm failed: {r.status_code} {r.text}"
        finally:
            _mfa.new_otp = orig

    return _grant
