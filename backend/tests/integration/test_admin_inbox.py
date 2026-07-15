"""Admin support inbox — list / read / claim-lock / reply(email) over marketing_leads messages."""
from __future__ import annotations

from app.db import SessionLocal
from app.models import MarketingLead


def _msg_lead(client, email, message="Please help me set up the SDK."):
    client.post("/v1/leads", json={"email": email, "source": "support",
                                   "subject": "support request", "message": message})
    db = SessionLocal()
    lead_id = str(db.query(MarketingLead).filter(MarketingLead.email == email).one().id)
    db.close()
    return lead_id


def test_requires_staff_session(client):
    assert client.get("/admin/v1/inbox").status_code == 401
    assert client.get("/admin/v1/inbox/unread-count").status_code == 401


def test_list_shows_only_messages(client, make_staff, staff_login):
    lid = _msg_lead(client, "inbox-a@corp.com")
    client.post("/v1/leads", json={"email": "signup-only@corp.com"})   # no message -> excluded
    s = make_staff(role="operator")
    cs = staff_login(s["email"], s["password"])
    data = cs.get("/admin/v1/inbox").json()
    emails = [i["email"] for i in data["items"]]
    assert "inbox-a@corp.com" in emails
    assert "signup-only@corp.com" not in emails
    item = next(i for i in data["items"] if i["id"] == lid)
    assert item["read"] is False and item["message"]


def test_read_marks_read_and_drops_unread(client, make_staff, staff_login):
    lid = _msg_lead(client, "inbox-read@corp.com")
    s = make_staff(role="operator")
    cs = staff_login(s["email"], s["password"])
    before = cs.get("/admin/v1/inbox/unread-count").json()["unread"]
    assert cs.post(f"/admin/v1/inbox/{lid}/read").status_code == 200
    assert cs.get("/admin/v1/inbox/unread-count").json()["unread"] == before - 1


def test_claim_locks_to_one_staff(client, make_staff, staff_login):
    lid = _msg_lead(client, "inbox-claim@corp.com")
    a = make_staff(role="operator")
    ca = staff_login(a["email"], a["password"])
    b = make_staff(role="operator")
    cb = staff_login(b["email"], b["password"])
    assert ca.post(f"/admin/v1/inbox/{lid}/claim").status_code == 200
    assert cb.post(f"/admin/v1/inbox/{lid}/claim").status_code == 409          # locked to A
    assert cb.post(f"/admin/v1/inbox/{lid}/reply", json={"message": "hi"}).status_code == 403
    item = next(i for i in cb.get("/admin/v1/inbox").json()["items"] if i["id"] == lid)
    assert item["claimed_by"] == a["email"]                                    # B can still read it


def test_reply_records_and_emails(client, make_staff, staff_login, monkeypatch):
    import app.routers.admin_inbox as inbox
    sent = {}
    monkeypatch.setattr(inbox.email_mod, "send_email", lambda **kw: sent.update(kw) or True)
    lid = _msg_lead(client, "inbox-reply@corp.com")
    s = make_staff(role="operator")
    cs = staff_login(s["email"], s["password"])
    r = cs.post(f"/admin/v1/inbox/{lid}/reply", json={"message": "Here are the setup steps."})
    assert r.status_code == 200 and r.json()["emailed"] is True
    assert sent["to"] == "inbox-reply@corp.com" and "setup steps" in sent["text"]
    db = SessionLocal()
    lead = db.query(MarketingLead).filter(MarketingLead.email == "inbox-reply@corp.com").one()
    assert lead.reply and lead.replied_at is not None and lead.claimed_by is not None
    db.close()


def test_release_frees_the_lock(client, make_staff, staff_login):
    lid = _msg_lead(client, "inbox-release@corp.com")
    a = make_staff(role="operator")
    ca = staff_login(a["email"], a["password"])
    ca.post(f"/admin/v1/inbox/{lid}/claim")
    assert ca.post(f"/admin/v1/inbox/{lid}/release").status_code == 200
    b = make_staff(role="operator")
    cb = staff_login(b["email"], b["password"])
    assert cb.post(f"/admin/v1/inbox/{lid}/claim").status_code == 200          # free again


def test_enterprise_lead_is_priority_and_sorts_first(client, make_staff, staff_login):
    _msg_lead(client, "normal@corp.com")                                       # ordinary support note
    client.post("/v1/leads", json={"email": "biz@corp.com", "name": "Big Co",
                                   "source": "enterprise", "subject": "custom",
                                   "message": "We need SSO + on-prem."})       # priority
    s = make_staff(role="operator")
    cs = staff_login(s["email"], s["password"])
    items = cs.get("/admin/v1/inbox").json()["items"]
    assert items[0]["email"] == "biz@corp.com" and items[0]["priority"] is True  # sorts to the top
    assert any(i["email"] == "normal@corp.com" and i["priority"] is False for i in items)


def test_enterprise_contact_notifies_superadmins(client, make_staff, monkeypatch):
    import app.routers.leads as leads
    sent = []
    monkeypatch.setattr(leads.email_mod, "send_email", lambda **kw: sent.append(kw) or True)
    boss = make_staff(role="superadmin")
    client.post("/v1/leads", json={"email": "founder-lead@corp.com", "name": "Ada",
                                   "source": "enterprise", "message": "Need a custom SLA."})
    tos = [k["to"] for k in sent]
    assert boss["email"] in tos                                                # superadmin paged
    assert "founder-lead@corp.com" in tos                                      # sender confirmation
    assert any("custom SLA" in (k.get("text") or "") for k in sent)
