"""P11 — export history/jobs (POST/GET /v1/exports).

Covers: auth-gating; recording a job writes an account_actions row; unknown type → 422; history is
newest-first; get-by-id; org isolation (RLS-invisible cross-org → 404).
"""

from __future__ import annotations


def test_exports_require_auth(client):
    assert client.get("/v1/exports").status_code == 401
    assert client.post("/v1/exports", json={"type": "passport"}).status_code in (401, 403)


def test_create_records_job_and_audit(make_org, login):
    o = make_org()
    c = login(o["admin_email"], o["admin_password"])
    r = c.post("/v1/exports", json={"type": "passport", "params": {"days": 30}})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["type"] == "passport" and j["status"] == "completed"
    assert j["requested_by"] == o["admin_email"] and j["params"] == {"days": 30}
    # history lists it, get-by-id resolves it
    items = c.get("/v1/exports").json()["items"]
    assert len(items) == 1 and items[0]["id"] == j["id"]
    assert c.get("/v1/exports/" + j["id"]).json()["type"] == "passport"
    # audited via account_actions
    audit = c.get("/v1/account/audit").json()
    assert any(a.get("action") == "export.create" for a in audit)


def test_unknown_type_rejected(make_org, login):
    o = make_org()
    c = login(o["admin_email"], o["admin_password"])
    assert c.post("/v1/exports", json={"type": "nope"}).status_code == 422


def test_history_newest_first(make_org, login):
    o = make_org()
    c = login(o["admin_email"], o["admin_password"])
    assert c.post("/v1/exports", json={"type": "logs_csv"}).status_code == 200
    assert c.post("/v1/exports", json={"type": "passport"}).status_code == 200
    items = c.get("/v1/exports").json()["items"]
    assert len(items) == 2 and items[0]["type"] == "passport"      # newest first
    assert {i["type"] for i in items} == {"logs_csv", "passport"}


def test_org_isolation(make_org, login):
    a = make_org()
    b = make_org()
    aid = login(a["admin_email"], a["admin_password"]).post(
        "/v1/exports", json={"type": "passport"}).json()["id"]
    cb = login(b["admin_email"], b["admin_password"])
    assert cb.get("/v1/exports").json()["items"] == []             # org B sees none
    assert cb.get("/v1/exports/" + aid).status_code == 404         # cross-org invisible
