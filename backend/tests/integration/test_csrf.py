"""Double-submit CSRF — enforced on cookie-session writes, exempt for Bearer + public."""


def test_session_write_without_token_is_blocked(client, make_staff):
    s = make_staff(role="operator")
    assert client.post("/admin/v1/auth/login",
                       json={"email": s["email"], "password": s["password"]}).status_code == 200
    # a state-changing admin POST WITHOUT the X-CSRF-Token header -> 403 (cookie alone isn't enough)
    r = client.post("/admin/v1/inbox/00000000-0000-0000-0000-000000000000/read")
    assert r.status_code == 403


def test_session_write_with_token_passes_csrf(client, make_staff):
    s = make_staff(role="operator")
    client.post("/admin/v1/auth/login", json={"email": s["email"], "password": s["password"]})
    csrf = client.cookies.get("foxy_csrf")
    assert csrf                                            # minted on login
    r = client.post("/admin/v1/inbox/00000000-0000-0000-0000-000000000000/read",
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code != 403                           # 404 (no such lead) = past the CSRF gate


def test_bearer_ingest_is_exempt(client, make_org):
    org = make_org()
    r = client.post("/v1/logs/batch", json={"logs": []}, headers=org["auth"])
    assert r.status_code != 403                           # SDK Bearer path never carries a session cookie


def test_public_posts_are_exempt(client):
    assert client.post("/v1/consent", json={}).status_code != 403
    assert client.post("/v1/leads", json={"email": "csrf@test.dev"}).status_code != 403
