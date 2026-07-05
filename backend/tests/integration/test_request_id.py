"""Request correlation IDs (Phase 5 · 5E): every response carries an X-Request-ID,
generated when absent and echoed when the caller/proxy supplies one — on both the
customer and admin apps."""

from __future__ import annotations


def test_request_id_generated_when_absent(client):
    r = client.get("/")
    rid = r.headers.get("x-request-id")
    assert rid and len(rid) >= 8


def test_request_id_echoed_from_inbound_header(client):
    r = client.get("/", headers={"X-Request-ID": "trace-abc-123"})
    assert r.headers.get("x-request-id") == "trace-abc-123"


def test_request_id_present_on_admin_app(client):
    r = client.get("/admin/")
    assert r.headers.get("x-request-id")
