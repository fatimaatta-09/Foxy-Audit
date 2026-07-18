from __future__ import annotations

import uuid

import pytest

from foxy_audit import FoxyClient, hashing
from foxy_audit.client import AuditRequiredError
from foxy_audit.spool import EventSpool


def test_spool_allocates_sequences_and_retries_without_dropping(tmp_path):
    spool = EventSpool(str(tmp_path / "events.sqlite3"))
    first = spool.enqueue("http://backend/v1/logs/batch", "secret", {
        "event_id": str(uuid.uuid4()), "client_id": "worker-a",
    })
    second = spool.enqueue("http://backend/v1/logs/batch", "secret", {
        "event_id": str(uuid.uuid4()), "client_id": "worker-a",
    })
    assert (first["client_seq"], second["client_seq"]) == (1, 2)
    rows = spool.due()
    spool.retry(rows, "connection refused")
    assert spool.has(first["event_id"]) and spool.has(second["event_id"])


def test_client_identity_survives_process_restart(tmp_path):
    path = str(tmp_path / "events.sqlite3")
    first = FoxyClient(api_key="foxy_sk_test", desktop_ping=False, spool_path=path)
    second = FoxyClient(api_key="foxy_sk_test", desktop_ping=False, spool_path=path)
    assert first.cfg.client_id == second.cfg.client_id
    assert first.cfg.client_id


def test_explicit_client_identity_wins(tmp_path):
    client = FoxyClient(api_key="foxy_sk_test", client_id="service-prod",
                        desktop_ping=False, spool_path=str(tmp_path / "events.sqlite3"))
    assert client.cfg.client_id == "service-prod"


def test_keyed_commitment_is_not_public_sha256():
    assert hashing.commitment_hex("patient prompt", "customer-secret") != hashing.sha256_hex(
        hashing.canonical_json("patient prompt"))


def test_exception_is_captured_without_masking_host_error(monkeypatch):
    from foxy_audit import dispatch
    captured = {}
    monkeypatch.setattr(dispatch, "submit", lambda cfg, payload: captured.update(payload))
    foxy = FoxyClient(api_key="foxy_sk_test", desktop_ping=False)

    @foxy.audit("demo")
    def boom(prompt):
        raise ValueError("host error")

    with pytest.raises(ValueError, match="host error"):
        boom("secret prompt")
    assert captured["event_type"] == "exception"


def test_audit_required_fails_closed(monkeypatch):
    from foxy_audit import dispatch
    monkeypatch.setattr(dispatch, "submit", lambda *args, **kwargs: (_ for _ in ()).throw(
        OSError("backend unavailable")))
    foxy = FoxyClient(api_key="foxy_sk_test", desktop_ping=False, audit_required=True)

    @foxy.audit("demo")
    def ask(prompt):
        return "response"

    with pytest.raises(AuditRequiredError):
        ask("prompt")


def test_audit_required_does_not_mask_host_exception(monkeypatch):
    from foxy_audit import dispatch
    monkeypatch.setattr(dispatch, "submit", lambda *args, **kwargs: (_ for _ in ()).throw(
        OSError("backend unavailable")))
    foxy = FoxyClient(api_key="foxy_sk_test", desktop_ping=False, audit_required=True)

    @foxy.audit("demo")
    def boom(prompt):
        raise ValueError("host error")

    with pytest.raises(ValueError, match="host error"):
        boom("prompt")


def test_structured_messages_and_provider_metadata_are_captured(monkeypatch):
    from foxy_audit import dispatch
    captured = {}
    monkeypatch.setattr(dispatch, "submit", lambda cfg, payload: captured.update(payload))

    class Usage:
        prompt_tokens = 4
        completion_tokens = 6
        total_tokens = 10

    class Response:
        id = "resp_123"
        model = "gpt-test"
        usage = Usage()
        choices = []

    foxy = FoxyClient(api_key="foxy_sk_test", desktop_ping=False)

    @foxy.audit("demo")
    def ask(messages):
        return Response()

    ask([{"role": "user", "content": "secret patient message"}])
    assert captured["commitment_alg"] == "hmac-sha256"
    assert captured["event_metadata"]["id"] == "resp_123"
    assert captured["event_metadata"]["usage"]["total_tokens"] == 10
