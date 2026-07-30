"""POST /v1/passport — the date_from/date_to window overrides the rolling `days`
cutoff (wires the dashboard's date pickers, which were previously ignored).

Forces the HTML fallback (weasyprint set to None) so we can assert on the rendered
report text; weasyprint's native PDF path isn't available in CI anyway.
"""
from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timezone

from sqlalchemy import text

from app.db import engine
_h = lambda s: hashlib.sha256(s.encode()).hexdigest()  # noqa: E731


def _ingest(client, org, n):
    payload = [{"prompt_hash": _h(f"p{i}"), "response_hash": _h(f"r{i}"),
                "token_count": 10 + i, "policy_tag": "chat"} for i in range(n)]
    assert client.post("/v1/logs/batch", headers=org["auth"], json=payload).status_code == 202


def _sdk_event(client_id, client_seq):
    return {
        "prompt_hash": _h(f"sdk-prompt-{client_id}-{client_seq}"),
        "response_hash": _h(f"sdk-response-{client_id}-{client_seq}"),
        "token_count": 10,
        "policy_tag": "chat",
        "client_id": client_id,
        "client_seq": client_seq,
    }


def _stub_renderer(monkeypatch) -> dict:
    """Install a weasyprint stand-in that RECORDS the html it is handed.

    /v1/passport returns a PDF and nothing else — the HTML degrade these tests
    used to read from was removed, because a caller asking for a PDF must not be
    handed HTML under a 200. The document is inspected from the renderer's input
    instead, which still exercises the real route and the real context assembly.
    """
    captured: dict = {}

    class _FakeHTML:
        def __init__(self, string=None, **kw):
            captured["html"] = string

        def write_pdf(self):
            return b"%PDF-1.7\nstub\n%%EOF"

    fake = type(sys)("weasyprint")
    fake.HTML = _FakeHTML
    monkeypatch.setitem(sys.modules, "weasyprint", fake)
    return captured


def test_passport_default_days_still_works(make_org, client, monkeypatch):
    doc = _stub_renderer(monkeypatch)
    org = make_org()
    _ingest(client, org, 3)
    r = client.post("/v1/passport", headers=org["auth"])
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"


def test_passport_honors_custom_date_range(make_org, client, monkeypatch):
    """A custom date_from is echoed in the report, proving the explicit range is
    applied rather than the default 30-day cutoff (which would never show 2020)."""
    doc = _stub_renderer(monkeypatch)
    org = make_org()
    _ingest(client, org, 2)
    r = client.post("/v1/passport?date_from=2020-01-01&date_to=2999-12-31",
                    headers=org["auth"])
    assert r.status_code == 200, r.text
    assert "2020" in doc["html"]


def test_passport_reports_sdk_capture_gap_and_evidence_boundary(make_org, client, monkeypatch):
    doc = _stub_renderer(monkeypatch)
    org = make_org()
    response = client.post(
        "/v1/logs/batch",
        headers=org["auth"],
        json=[_sdk_event("billing-worker", 1), _sdk_event("billing-worker", 3)],
    )
    assert response.status_code == 202, response.text

    report = client.post("/v1/passport", headers=org["auth"])

    assert report.status_code == 200, report.text
    assert "SDK Capture Coverage" in doc["html"]
    assert "Policy Configuration Evidence" in doc["html"]
    assert "bound into the V3 chain payload" in doc["html"]
    assert "PARTIAL" in doc["html"]
    assert "Missing Client Sequence Events</dt><dd>1</dd>" in doc["html"]
    assert "calls that bypass the SDK" in doc["html"]


def test_passport_does_not_infer_prior_history_outside_report_range(make_org, client, monkeypatch):
    doc = _stub_renderer(monkeypatch)
    org = make_org()
    response = client.post(
        "/v1/logs/batch",
        headers=org["auth"],
        json=[_sdk_event("production-sdk", 1), _sdk_event("production-sdk", 2)],
    )
    assert response.status_code == 202, response.text
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE audit_logs SET created_at = now() - interval '2 days' "
                 "WHERE org_id = :org_id AND seq = 1"),
            {"org_id": org["org_id"]},
        )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report = client.post(
        f"/v1/passport?date_from={today}&date_to={today}", headers=org["auth"]
    )

    assert report.status_code == 200, report.text
    assert "Observed Events</dt>      <dd>1</dd>" in doc["html"]
    assert "Missing Client Sequence Events</dt><dd>0</dd>" in doc["html"]
    assert "VERIFIED" in doc["html"]
