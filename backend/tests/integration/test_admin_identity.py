"""Phase B — staff identity & preferences (dashboard parity).

Covers: /auth/me exposes full_name + preferences (defaults, no secret leak);
PUT /auth/profile sets/clears the display name and is audited; GET/PUT
/auth/preferences merges only known boolean keys (drops arbitrary JSON), persists,
and is audited; and all three require a staff session.
"""

from __future__ import annotations

from app.db import SessionLocal
from app.models import AdminAction


def _count_actions(action: str | None = None) -> int:
    db = SessionLocal()
    try:
        rows = db.query(AdminAction).all()
        if action is None:
            return len(rows)
        return sum(1 for r in rows if r.action == action)
    finally:
        db.close()


# ───────────────────────────── /auth/me shape ────────────────────────────────

def test_me_has_profile_defaults_and_no_secret_leak(make_staff, staff_login):
    s = make_staff(role="viewer")
    cs = staff_login(s["email"], s["password"])
    me = cs.get("/admin/v1/auth/me").json()
    assert me["full_name"] is None
    assert me["preferences"] == {}
    for secret in ("password_hash", "mfa_code_hash", "reset_token_hash"):
        assert secret not in me


# ─────────────────────────────── profile (name) ──────────────────────────────

def test_update_profile_sets_name_trims_and_audits(make_staff, staff_login):
    s = make_staff(role="viewer")
    cs = staff_login(s["email"], s["password"])
    before = _count_actions("staff.profile_update")

    r = cs.put("/admin/v1/auth/profile", json={"full_name": "  Ada Lovelace  "})
    assert r.status_code == 200
    assert r.json()["full_name"] == "Ada Lovelace"                 # trimmed

    assert cs.get("/admin/v1/auth/me").json()["full_name"] == "Ada Lovelace"
    assert _count_actions("staff.profile_update") == before + 1    # exactly one audit row


def test_profile_blank_clears_name(make_staff, staff_login):
    s = make_staff(role="viewer")
    cs = staff_login(s["email"], s["password"])
    cs.put("/admin/v1/auth/profile", json={"full_name": "Grace Hopper"})
    r = cs.put("/admin/v1/auth/profile", json={"full_name": "   "})
    assert r.status_code == 200 and r.json()["full_name"] is None
    assert cs.get("/admin/v1/auth/me").json()["full_name"] is None


def test_profile_rejects_overlong_name(make_staff, staff_login):
    s = make_staff(role="viewer")
    cs = staff_login(s["email"], s["password"])
    assert cs.put("/admin/v1/auth/profile", json={"full_name": "x" * 121}).status_code == 422


def test_profile_requires_staff_session(client):
    # No session cookie → CSRF is skipped and require_staff rejects with 401.
    assert client.put("/admin/v1/auth/profile", json={"full_name": "x"}).status_code == 401


# ───────────────────────────────── preferences ───────────────────────────────

def test_preferences_merge_known_drop_unknown_and_audit(make_staff, staff_login):
    s = make_staff(role="operator")
    cs = staff_login(s["email"], s["password"])
    before = _count_actions("staff.preferences_update")

    r = cs.put("/admin/v1/auth/preferences", json={"preferences": {
        "hide_sensitive_metadata": True,
        "notify_system": False,
        "evil_key": "drop-me",           # unknown → dropped
        "platform_role": "superadmin",   # privilege stuffing → dropped
    }})
    assert r.status_code == 200
    prefs = r.json()["preferences"]
    assert prefs["hide_sensitive_metadata"] is True
    assert prefs["notify_system"] is False
    assert "evil_key" not in prefs and "platform_role" not in prefs

    # persisted + reflected on both /me and GET /preferences
    assert cs.get("/admin/v1/auth/me").json()["preferences"]["hide_sensitive_metadata"] is True
    assert cs.get("/admin/v1/auth/preferences").json()["preferences"]["notify_system"] is False
    assert _count_actions("staff.preferences_update") == before + 1


def test_preferences_partial_update_merges(make_staff, staff_login):
    s = make_staff(role="viewer")
    cs = staff_login(s["email"], s["password"])
    cs.put("/admin/v1/auth/preferences", json={"preferences": {"hide_sensitive_metadata": True}})
    cs.put("/admin/v1/auth/preferences", json={"preferences": {"notify_broadcasts": False}})
    prefs = cs.get("/admin/v1/auth/preferences").json()["preferences"]
    assert prefs["hide_sensitive_metadata"] is True   # preserved across the later partial write
    assert prefs["notify_broadcasts"] is False


def test_preferences_coerces_to_bool(make_staff, staff_login):
    s = make_staff(role="viewer")
    cs = staff_login(s["email"], s["password"])
    r = cs.put("/admin/v1/auth/preferences",
               json={"preferences": {"hide_sensitive_metadata": 1, "notify_system": 0}})
    prefs = r.json()["preferences"]
    assert prefs["hide_sensitive_metadata"] is True and prefs["notify_system"] is False


def test_preferences_requires_staff_session(client):
    assert client.get("/admin/v1/auth/preferences").status_code == 401
    assert client.put("/admin/v1/auth/preferences",
                      json={"preferences": {}}).status_code == 401
