"""Security headers on every response + a request body-size cap (Phase 5 · 5B.6)."""

from __future__ import annotations


def test_security_headers_present(client):
    r = client.get("/health/ready")
    assert r.headers.get("x-frame-options") == "DENY"
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert "referrer-policy" in r.headers
    assert "content-security-policy" in r.headers


def test_oversized_request_body_rejected(client):
    from app.config import get_settings

    s = get_settings()
    original = s.max_request_bytes
    s.max_request_bytes = 50
    try:
        r = client.post("/v1/logs/batch", content=b"x" * 200)  # 200 > 50
        assert r.status_code == 413
    finally:
        s.max_request_bytes = original
