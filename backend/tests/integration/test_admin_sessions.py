"""Phase E — staff device sessions + last_login + revoke + logout-all (NO remember-me).

Covers: login mints a session and stamps last_login; /auth/sessions lists active sessions with a
this-device flag and NO token_hash leak; revoke (step-up gated) kills another device; logout-all
(step-up gated) kills every session including this one; logout revokes the current row; you can't
revoke another staff's session; endpoints require a session.

staff_login auto-grants step-up, so gated revoke / logout-all go through; the gate tests opt out.
"""

from __future__ import annotations


def test_login_reports_last_login_and_no_secret_leak(make_staff, staff_login):
    c = staff_login(*_c(make_staff(role="viewer")))
    me = c.get("/admin/v1/auth/me").json()
    assert me["last_login_at"] is not None

    d = c.get("/admin/v1/auth/sessions").json()
    assert d["last_login_at"] is not None
    assert len(d["items"]) == 1 and d["items"][0]["current"] is True
    for s in d["items"]:                         # token_hash must never be serialized
        assert "token_hash" not in s


def test_list_two_sessions_and_revoke_kills_the_other(make_staff, staff_login):
    s = make_staff(role="operator")
    c1 = staff_login(s["email"], s["password"])          # session A (+ step-up grant)
    c2 = staff_login(s["email"], s["password"])          # session B

    d = c1.get("/admin/v1/auth/sessions").json()
    assert len(d["items"]) == 2 and sum(1 for x in d["items"] if x["current"]) == 1
    other = next(x for x in d["items"] if not x["current"])

    assert c1.post(f"/admin/v1/auth/sessions/{other['id']}/revoke").status_code == 200
    assert c2.get("/admin/v1/auth/me").status_code == 401          # revoked device is out
    assert len(c1.get("/admin/v1/auth/sessions").json()["items"]) == 1


def test_revoke_requires_step_up(make_staff, staff_login):
    s = make_staff(role="operator")
    c1 = staff_login(s["email"], s["password"], with_step_up=False)
    staff_login(s["email"], s["password"], with_step_up=False)     # a second device
    other = next(x for x in c1.get("/admin/v1/auth/sessions").json()["items"] if not x["current"])
    r = c1.post(f"/admin/v1/auth/sessions/{other['id']}/revoke")
    assert r.status_code == 403 and r.json()["detail"] == "step_up_required"


def test_logout_all_revokes_everything(make_staff, staff_login):
    s = make_staff(role="operator")
    c1 = staff_login(s["email"], s["password"])
    c2 = staff_login(s["email"], s["password"])
    r = c1.post("/admin/v1/auth/logout-all")
    assert r.status_code == 200 and r.json()["revoked"] >= 2
    assert c1.get("/admin/v1/auth/me").status_code == 401         # this device out too
    assert c2.get("/admin/v1/auth/me").status_code == 401


def test_logout_all_requires_step_up(make_staff, staff_login):
    c = staff_login(*_c(make_staff(role="operator")), with_step_up=False)
    assert c.post("/admin/v1/auth/logout-all").status_code == 403


def test_logout_revokes_current_session(make_staff, staff_login):
    c = staff_login(*_c(make_staff(role="viewer")))
    assert c.post("/admin/v1/auth/logout").status_code == 200
    assert c.get("/admin/v1/auth/me").status_code == 401


def test_cannot_revoke_another_staffs_session(make_staff, staff_login):
    ca = staff_login(*_c(make_staff(role="operator")))
    cb = staff_login(*_c(make_staff(role="operator")))
    b_id = cb.get("/admin/v1/auth/sessions").json()["items"][0]["id"]
    assert ca.post(f"/admin/v1/auth/sessions/{b_id}/revoke").status_code == 404


def test_sessions_require_auth(client):
    assert client.get("/admin/v1/auth/sessions").status_code == 401
    assert client.post("/admin/v1/auth/logout-all").status_code == 401


def _c(s: dict):
    return s["email"], s["password"]
