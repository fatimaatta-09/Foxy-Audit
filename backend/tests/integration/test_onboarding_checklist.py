"""P4 — dashboard onboarding checklist (GET/PUT /v1/onboarding).

Covers: auth-gating; the checklist is computed LIVE from real data (active key / first logged call /
team>1) — never fabricated; `complete` flips once key+logged; PUT persists the dismissal across a fresh
login; org isolation (each org sees only its own completion).
"""

from __future__ import annotations

import hashlib

_h = lambda s: hashlib.sha256(s.encode()).hexdigest()  # noqa: E731


def _ingest_one(client, org):
    rows = [{"prompt_hash": _h("p1"), "response_hash": _h("r1"),
             "token_count": 10, "policy_tag": "test"}]
    assert client.post("/v1/logs/batch", json=rows, headers=org["auth"]).status_code == 202


def test_onboarding_requires_auth(client):
    assert client.get("/v1/onboarding").status_code == 401
    assert client.put("/v1/onboarding", json={"dismissed": True}).status_code in (401, 403)


def test_fresh_org_checklist(make_org, login):
    o = make_org()          # make_org mints one ACTIVE api key + a solo admin
    c = login(o["admin_email"], o["admin_password"])
    d = c.get("/v1/onboarding").json()
    assert d["total"] == 3 and d["dismissed"] is False and d["complete"] is False
    st = {s["key"]: s["done"] for s in d["steps"]}
    assert st == {"api_key": True, "first_log": False, "invite_team": False}
    assert d["done"] == 1


def test_first_log_flips_complete(make_org, login, client):
    o = make_org()
    c = login(o["admin_email"], o["admin_password"])
    _ingest_one(client, o)
    d = c.get("/v1/onboarding").json()
    st = {s["key"]: s["done"] for s in d["steps"]}
    assert st["first_log"] is True
    assert d["complete"] is True          # key + logged → essentials live
    assert d["done"] >= 2


def test_invite_team_flips(make_org, login, add_user):
    o = make_org()
    add_user(o["org_id"], "mate@test.dev", "matepass123", role="member")
    c = login(o["admin_email"], o["admin_password"])
    st = {s["key"]: s["done"] for s in c.get("/v1/onboarding").json()["steps"]}
    assert st["invite_team"] is True


def test_dismiss_persists_across_login(make_org, login):
    o = make_org()
    c = login(o["admin_email"], o["admin_password"])
    r = c.put("/v1/onboarding", json={"dismissed": True})
    assert r.status_code == 200 and r.json()["dismissed"] is True
    c2 = login(o["admin_email"], o["admin_password"])     # a fresh session/device
    assert c2.get("/v1/onboarding").json()["dismissed"] is True


def test_org_isolation(make_org, login, client, add_user):
    a = make_org()
    b = make_org()
    _ingest_one(client, a)
    add_user(a["org_id"], "am@test.dev", "ampass123")
    da = login(a["admin_email"], a["admin_password"]).get("/v1/onboarding").json()
    db = login(b["admin_email"], b["admin_password"]).get("/v1/onboarding").json()
    assert da["complete"] is True and da["done"] == 3
    assert db["complete"] is False and db["done"] == 1    # org B is untouched
