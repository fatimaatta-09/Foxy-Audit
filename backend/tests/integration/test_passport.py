"""POST /v1/passport — the date_from/date_to window overrides the rolling `days`
cutoff (wires the dashboard's date pickers, which were previously ignored).

Forces the HTML fallback (weasyprint set to None) so we can assert on the rendered
report text; weasyprint's native PDF path isn't available in CI anyway.
"""
from __future__ import annotations

import hashlib
import sys

_h = lambda s: hashlib.sha256(s.encode()).hexdigest()  # noqa: E731


def _ingest(client, org, n):
    payload = [{"prompt_hash": _h(f"p{i}"), "response_hash": _h(f"r{i}"),
                "token_count": 10 + i, "policy_tag": "chat"} for i in range(n)]
    assert client.post("/v1/logs/batch", headers=org["auth"], json=payload).status_code == 202


def test_passport_default_days_still_works(make_org, client, monkeypatch):
    monkeypatch.setitem(sys.modules, "weasyprint", None)   # force HTML fallback
    org = make_org()
    _ingest(client, org, 3)
    r = client.post("/v1/passport", headers=org["auth"])
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers.get("content-type", "")


def test_passport_honors_custom_date_range(make_org, client, monkeypatch):
    """A custom date_from is echoed in the report, proving the explicit range is
    applied rather than the default 30-day cutoff (which would never show 2020)."""
    monkeypatch.setitem(sys.modules, "weasyprint", None)
    org = make_org()
    _ingest(client, org, 2)
    r = client.post("/v1/passport?date_from=2020-01-01&date_to=2999-12-31",
                    headers=org["auth"])
    assert r.status_code == 200, r.text
    assert "2020" in r.text
