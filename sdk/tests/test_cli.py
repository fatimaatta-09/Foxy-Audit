"""`foxy doctor` CLI — backend / pet / chain checks, with the network mocked."""
from __future__ import annotations

import requests

import foxy_audit.cli as cli


class _Resp:
    def __init__(self, status=202, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._payload


def test_doctor_all_green(monkeypatch, capsys):
    monkeypatch.setattr(cli.requests, "post", lambda *a, **k: _Resp(202))
    monkeypatch.setattr(cli.requests, "get", lambda *a, **k: _Resp(200, {"ok": True, "count": 1}))
    monkeypatch.setattr(cli.udp, "send_ping", lambda *a, **k: True)
    assert cli.main(["doctor", "--api-key", "foxy_sk_test", "--backend", "http://x"]) == 0
    out = capsys.readouterr().out
    assert "Backend reachable" in out and "Ledger verified" in out


def test_doctor_bad_key(monkeypatch):
    monkeypatch.setattr(cli.requests, "post", lambda *a, **k: _Resp(401, text="Invalid API key"))
    monkeypatch.setattr(cli.udp, "send_ping", lambda *a, **k: True)
    assert cli.main(["doctor", "--api-key", "bad", "--backend", "http://x"]) == 1


def test_doctor_no_key(monkeypatch):
    monkeypatch.delenv("FOXY_API_KEY", raising=False)
    assert cli.main(["doctor", "--backend", "http://x"]) == 1


def test_doctor_backend_unreachable(monkeypatch):
    def _boom(*a, **k):
        raise requests.RequestException("no route to host")
    monkeypatch.setattr(cli.requests, "post", _boom)
    monkeypatch.setattr(cli.udp, "send_ping", lambda *a, **k: True)
    assert cli.main(["doctor", "--api-key", "foxy_sk_test", "--backend", "http://x"]) == 1
