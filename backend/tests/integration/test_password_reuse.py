"""Changing a password to the one you already have must be refused — on both
surfaces, staff (`/admin/v1/auth/change-password`) and customer
(`/v1/auth/change-password`).

The check sits *after* the current-password check on purpose: with a wrong
current password you get the current-password error, never the reuse error, so
the endpoint stays an oracle for nothing.
"""

from __future__ import annotations

REUSE_DETAIL = "new password must be different from your previous one"


def test_reuse_message_does_not_say_current():
    """The admin modal routes a server error to the field it is about with
    `/current/i.test(m)`. This message is about the NEW field, so the word
    "current" in it would send focus to the wrong input. The substring test is
    fragile and P3 replaces it — until then this assertion is what stops the
    wording drifting back."""
    assert "current" not in REUSE_DETAIL.lower()


# ------------------------------- staff surface -------------------------------

def test_staff_same_password_rejected(make_staff, staff_login):
    s = make_staff(role="viewer")
    c = staff_login(s["email"], s["password"])
    r = c.post("/admin/v1/auth/change-password",
               json={"current_password": s["password"], "new_password": s["password"]})
    assert r.status_code == 400
    assert r.json()["detail"] == REUSE_DETAIL
    # the account is untouched — the old password still signs in
    assert staff_login(s["email"], s["password"]).get(
        "/admin/v1/auth/me").status_code == 200


def test_staff_different_password_accepted(make_staff, staff_login):
    s = make_staff(role="viewer")
    c = staff_login(s["email"], s["password"])
    r = c.post("/admin/v1/auth/change-password",
               json={"current_password": s["password"], "new_password": "a-different-pass1"})
    assert r.status_code == 200
    assert staff_login(s["email"], "a-different-pass1").get(
        "/admin/v1/auth/me").status_code == 200


def test_staff_wrong_current_wins_over_reuse(make_staff, staff_login):
    """Wrong current password AND a reused new one → the current-password error.
    Otherwise the reuse error tells an attacker their guess matched the stored
    hash without them ever proving they know the current password."""
    s = make_staff(role="viewer")
    c = staff_login(s["email"], s["password"])
    r = c.post("/admin/v1/auth/change-password",
               json={"current_password": "not-it", "new_password": s["password"]})
    assert r.status_code == 400
    assert r.json()["detail"] == "current password is incorrect"


# ----------------------------- customer surface ------------------------------

def test_customer_same_password_rejected(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    r = c.post("/v1/auth/change-password",
               json={"current_password": org["admin_password"],
                     "new_password": org["admin_password"]})
    assert r.status_code == 400
    assert r.json()["detail"] == REUSE_DETAIL
    assert login(org["admin_email"], org["admin_password"]).get(
        "/v1/auth/me").status_code == 200


def test_customer_different_password_accepted(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    r = c.post("/v1/auth/change-password",
               json={"current_password": org["admin_password"],
                     "new_password": "a-different-pass1"})
    assert r.status_code == 200
    assert login(org["admin_email"], "a-different-pass1").get(
        "/v1/auth/me").status_code == 200


def test_customer_wrong_current_wins_over_reuse(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    r = c.post("/v1/auth/change-password",
               json={"current_password": "not-it",
                     "new_password": org["admin_password"]})
    assert r.status_code == 403
    assert r.json()["detail"] == "current password is incorrect"
