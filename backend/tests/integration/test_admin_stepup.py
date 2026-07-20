"""Phase C — email-code step-up on every audited danger mutation.

Covers: a gated mutation 403s `step_up_required` without a grant; request emails a code
and confirm mints a ~10-min grant; a valid grant lets the mutation through (+ audit row) and
covers a burst; wrong/expired codes are rejected; step-up is itself audited and requires a
session; a spread of endpoints across routers is gated; low-risk workflow actions are NOT.
"""

from __future__ import annotations

from datetime import timedelta

import app.auth as auth
import app.mfa as mfa
from app.db import SessionLocal
from app.models import AdminAction


def _count(action: str | None = None) -> int:
    db = SessionLocal()
    try:
        rows = db.query(AdminAction).all()
        return len(rows) if action is None else sum(1 for r in rows if r.action == action)
    finally:
        db.close()


# ───────────────────────── the core: gate + grant + effect ───────────────────

def test_danger_mutation_requires_step_up(make_org, make_staff, staff_login):
    a = make_org()
    co = staff_login(*_creds(make_staff(role="operator")), with_step_up=False)
    r = co.post(f"/admin/v1/organizations/{a['org_id']}/suspend", json={"reason": "x"})
    assert r.status_code == 403 and r.json()["detail"] == "step_up_required"


def test_grant_then_mutation_succeeds_and_audits(make_org, make_staff, staff_login, step_up):
    a = make_org()
    co = staff_login(*_creds(make_staff(role="operator")), with_step_up=False)
    assert co.post(f"/admin/v1/organizations/{a['org_id']}/suspend", json={}).status_code == 403
    before = _count("org.suspend")
    step_up(co)
    r = co.post(f"/admin/v1/organizations/{a['org_id']}/suspend", json={"reason": "abuse"})
    assert r.status_code == 200 and r.json()["status"] == "suspended"
    assert _count("org.suspend") == before + 1          # the mutation still writes its audit row


def test_grant_covers_a_burst(make_org, make_staff, staff_login, step_up):
    a, b = make_org(), make_org()
    co = staff_login(*_creds(make_staff(role="operator")), with_step_up=False)
    step_up(co)                                          # one code…
    assert co.post(f"/admin/v1/organizations/{a['org_id']}/suspend", json={}).status_code == 200
    assert co.post(f"/admin/v1/organizations/{b['org_id']}/suspend", json={}).status_code == 200  # …covers both


# ───────────────────────────── code + grant edges ────────────────────────────

def test_wrong_code_rejected_and_no_grant(make_org, make_staff, staff_login):
    a = make_org()
    co = staff_login(*_creds(make_staff(role="operator")), with_step_up=False)
    orig = mfa.new_otp
    mfa.new_otp = lambda: "000000"
    try:
        assert co.post("/admin/v1/auth/step-up/request").status_code == 200
        assert co.post("/admin/v1/auth/step-up/confirm", json={"code": "999999"}).status_code == 400
    finally:
        mfa.new_otp = orig
    # still no grant → mutation blocked
    assert co.post(f"/admin/v1/organizations/{a['org_id']}/suspend", json={}).status_code == 403


def test_expired_grant_blocks(make_org, make_staff, staff_login, step_up, monkeypatch):
    a = make_org()
    co = staff_login(*_creds(make_staff(role="operator")), with_step_up=False)
    monkeypatch.setattr(auth, "STEP_UP_TTL", timedelta(seconds=-1))   # grant is born expired
    step_up(co)
    assert co.post(f"/admin/v1/organizations/{a['org_id']}/suspend", json={}).status_code == 403


def test_step_up_is_audited(make_staff, staff_login, step_up):
    co = staff_login(*_creds(make_staff(role="operator")), with_step_up=False)
    before = _count("staff.step_up")
    step_up(co)
    assert _count("staff.step_up") == before + 1


def test_step_up_requires_session(client):
    assert client.post("/admin/v1/auth/step-up/request").status_code == 401
    assert client.post("/admin/v1/auth/step-up/confirm", json={"code": "000000"}).status_code == 401


# ───────────────── gating spread + intentional exemptions ─────────────────────

def test_representative_endpoints_across_routers_are_gated(make_org, make_staff, staff_login):
    a = make_org()
    cs = staff_login(*_creds(make_staff(role="superadmin")), with_step_up=False)
    for r in (
        cs.post("/admin/v1/staff", json={"email": "z@foxy.audit", "platform_role": "viewer"}),
        cs.put("/admin/v1/config", json={"updates": {}}),
        cs.post(f"/admin/v1/organizations/{a['org_id']}/offboard", json={}),
        cs.post("/admin/v1/broadcast", json={"title": "t", "body": "b", "level": "info"}),
    ):
        assert r.status_code == 403 and r.json().get("detail") == "step_up_required", r.text


def test_workflow_actions_are_not_step_up_gated(make_staff, staff_login):
    # inbox + lead are low-risk, high-frequency → intentionally NOT gated. They may 404 on a
    # missing id, but must never demand step-up.
    co = staff_login(*_creds(make_staff(role="operator")), with_step_up=False)
    r = co.post("/admin/v1/inbox/00000000-0000-0000-0000-000000000000/claim")
    assert not (r.status_code == 403 and r.json().get("detail") == "step_up_required")


def _creds(s: dict):
    return s["email"], s["password"]
