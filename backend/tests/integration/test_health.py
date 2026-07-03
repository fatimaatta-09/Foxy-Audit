"""GET /health/ready — DB + worker-liveness + backlog readiness probe."""

from __future__ import annotations

from sqlalchemy import text

from app.db import engine


def test_ready_503_without_heartbeat(client):
    """Fresh DB: worker_heartbeat.beat_at is NULL -> not_ready, but DB is ok."""
    r = client.get("/health/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["db"] == "ok"
    assert body["checks"]["worker"] == "no heartbeat yet"


def test_ready_200_with_fresh_heartbeat(client):
    with engine.begin() as c:
        c.execute(text("UPDATE worker_heartbeat SET beat_at = now() WHERE id = 1"))
    r = client.get("/health/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


def test_ready_503_when_stale(client):
    with engine.begin() as c:
        c.execute(text("UPDATE worker_heartbeat SET beat_at = now() - interval '10 minutes' WHERE id = 1"))
    r = client.get("/health/ready")
    assert r.status_code == 503
    assert "STALE" in r.json()["checks"]["worker"]
