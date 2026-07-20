"""Phase D — staff notifications center (generated from REAL events only).

Covers: honest empty state; broadcast fans out to other active staff (poster excluded); a staff
action targeting you notifies you; mark-read / read-all / unread-count; the notify_* preferences
are honoured; you can't mark another staff's row read; a session is required.

staff_login auto-grants step-up, so the gated broadcast / role mutations go through.
"""

from __future__ import annotations


def test_fresh_staff_has_no_notifications(make_staff, staff_login):
    cs = staff_login(*_c(make_staff(role="viewer")))
    d = cs.get("/admin/v1/notifications").json()
    assert d["unread"] == 0 and d["items"] == []


def test_broadcast_fans_out_and_excludes_poster(make_staff, staff_login):
    poster = make_staff(role="superadmin")
    recipient = make_staff(role="operator")
    cp = staff_login(*_c(poster))
    assert cp.post("/admin/v1/broadcast",
                   json={"title": "Maintenance", "body": "Tonight 10pm", "level": "warning"}).status_code == 200

    cr = staff_login(*_c(recipient))
    d = cr.get("/admin/v1/notifications").json()
    assert d["unread"] >= 1
    assert any(n["title"] == "Maintenance" and n["kind"] == "broadcast" and n["read"] is False
               for n in d["items"])
    # the poster does not notify themselves
    assert not any(n["title"] == "Maintenance" for n in cp.get("/admin/v1/notifications").json()["items"])


def test_role_change_notifies_the_target(make_staff, staff_login):
    admin = make_staff(role="superadmin")
    target = make_staff(role="viewer")
    ca = staff_login(*_c(admin))
    assert ca.post(f"/admin/v1/staff/{target['id']}/role",
                   json={"platform_role": "operator"}).status_code == 200
    ct = staff_login(*_c(target))
    assert any(n["kind"] == "staff_role" for n in ct.get("/admin/v1/notifications").json()["items"])


def test_mark_read_and_read_all(make_staff, staff_login):
    cp = staff_login(*_c(make_staff(role="superadmin")))
    rec = make_staff(role="operator")
    cp.post("/admin/v1/broadcast", json={"title": "A", "body": "a", "level": "info"})
    cp.post("/admin/v1/broadcast", json={"title": "B", "body": "b", "level": "info"})

    cr = staff_login(*_c(rec))
    d = cr.get("/admin/v1/notifications").json()
    assert d["unread"] == 2
    assert cr.post(f"/admin/v1/notifications/{d['items'][0]['id']}/read").status_code == 200
    assert cr.get("/admin/v1/notifications/unread-count").json()["unread"] == 1
    assert cr.post("/admin/v1/notifications/read-all").status_code == 200
    assert cr.get("/admin/v1/notifications/unread-count").json()["unread"] == 0


def test_broadcast_pref_off_is_skipped(make_staff, staff_login):
    optout = make_staff(role="operator")
    co = staff_login(*_c(optout))
    co.put("/admin/v1/auth/preferences", json={"preferences": {"notify_broadcasts": False}})
    cp = staff_login(*_c(make_staff(role="superadmin")))
    cp.post("/admin/v1/broadcast", json={"title": "Skip me", "body": "x", "level": "info"})
    assert not any(n["title"] == "Skip me" for n in co.get("/admin/v1/notifications").json()["items"])


def test_cannot_mark_another_staffs_notification(make_staff, staff_login):
    cp = staff_login(*_c(make_staff(role="superadmin")))
    a = make_staff(role="operator")
    cp.post("/admin/v1/broadcast", json={"title": "For A", "body": "x", "level": "info"})
    ca = staff_login(*_c(a))
    nid = ca.get("/admin/v1/notifications").json()["items"][0]["id"]
    # b was created AFTER the broadcast → not a recipient; can't touch a's row
    cb = staff_login(*_c(make_staff(role="operator")))
    assert cb.post(f"/admin/v1/notifications/{nid}/read").status_code == 404


def test_notifications_require_session(client):
    assert client.get("/admin/v1/notifications").status_code == 401
    assert client.post("/admin/v1/notifications/read-all").status_code == 401


def _c(s: dict):
    return s["email"], s["password"]
