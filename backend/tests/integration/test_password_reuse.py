"""Changing a password to the one you already have must be refused — on both
surfaces, staff (`/admin/v1/auth/change-password`) and customer
(`/v1/auth/change-password`).

The check sits *after* the current-password check on purpose: with a wrong
current password you get the current-password error, never the reuse error, so
the endpoint stays an oracle for nothing.
"""

from __future__ import annotations

# Anchored by the two `== REUSE_DETAIL` assertions below, which compare it to a
# live response. It is NOT a safe thing to assert against on its own.
REUSE_DETAIL = "new password must be different from your previous one"


def test_reuse_message_names_the_field_it_is_about(make_staff, staff_login, make_org, login):
    """The reuse rejection is about the NEW password, and its wording should say
    so rather than borrowing the current-password vocabulary.

    This reads the message off the live responses. The previous version asserted
    `"current" not in REUSE_DETAIL.lower()` — a literal defined in this file,
    compared against itself — which stays green no matter what either handler
    actually returns, and so could never have caught the drift it was written to
    catch. Both surfaces are checked, because a guard on one is how the two
    drift apart.
    """
    s = make_staff(role="viewer")
    staff = staff_login(s["email"], s["password"]).post(
        "/admin/v1/auth/change-password",
        json={"current_password": s["password"], "new_password": s["password"]})

    org = make_org()
    customer = login(org["admin_email"], org["admin_password"]).post(
        "/v1/auth/change-password",
        json={"current_password": org["admin_password"],
              "new_password": org["admin_password"]})

    for surface, r in (("staff", staff), ("customer", customer)):
        assert r.status_code == 400, f"{surface}: {r.status_code}"
        detail = r.json()["detail"]
        assert "current" not in detail.lower(), f"{surface} reuse message: {detail!r}"
        assert "new" in detail.lower(), f"{surface} reuse message: {detail!r}"


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
