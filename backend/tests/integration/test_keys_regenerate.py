"""Regenerate an API key behind an emailed 2FA code (dashboard admin session)."""
import app.mfa as mfa
import app.routers.keys as keys_router


def test_regenerate_request_then_confirm_rotates(make_org, login, monkeypatch):
    org = make_org()
    sent = {}
    monkeypatch.setattr(keys_router.email_mod, "send_email", lambda **kw: sent.update(kw) or True)
    monkeypatch.setattr(mfa, "new_otp", lambda: "424242")     # deterministic code
    c = login(org["admin_email"], org["admin_password"])

    # step 1 — request the code
    r = c.post("/v1/keys/regenerate/request")
    assert r.status_code == 200 and r.json()["status"] == "code_sent"
    assert "424242" in (sent.get("text") or "")

    # wrong code is rejected, no rotation
    assert c.post("/v1/keys/regenerate/confirm", json={"code": "000000"}).status_code == 401

    # correct code -> a fresh key is returned once
    r2 = c.post("/v1/keys/regenerate/confirm", json={"code": "424242"})
    assert r2.status_code == 200
    assert r2.json()["api_key"].startswith("foxy_sk_")

    # the code is single-use — replaying it now fails
    assert c.post("/v1/keys/regenerate/confirm", json={"code": "424242"}).status_code == 401


def test_regenerate_confirm_without_code_request_fails(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    # no code issued yet -> nothing valid to confirm
    assert c.post("/v1/keys/regenerate/confirm", json={"code": "123456"}).status_code == 401


def test_regenerate_request_needs_a_session(client):
    # unauthenticated (no session cookie) -> require_role rejects (not 200)
    assert client.post("/v1/keys/regenerate/request").status_code in (401, 403)
