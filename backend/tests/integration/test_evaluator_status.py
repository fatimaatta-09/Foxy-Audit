"""Evaluator availability must never be represented as a clean verdict."""

from types import SimpleNamespace


def test_health_reports_unavailable_evaluator(make_org, client, monkeypatch):
    from app.routers import health

    monkeypatch.setattr(
        health,
        "get_settings",
        lambda: SimpleNamespace(gemini_api_key="", gemini_model="gemini-2.5-flash"),
    )
    org = make_org()
    response = client.get("/v1/health", headers=org["auth"])

    assert response.status_code == 200
    assert response.json()["evaluator"] == {
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "status": "unavailable",
        "configured": False,
        "verdicts_advisory": True,
    }


def test_gemini_fallback_is_unknown_not_clean(monkeypatch):
    from app import gemini

    monkeypatch.setattr(
        gemini,
        "get_settings",
        lambda: SimpleNamespace(gemini_api_key="", gemini_fail_closed=False),
    )
    verdict = gemini.evaluate({"policy_tag": "judge_smoke", "token_count": 1})

    assert verdict.policy_breach is False
    assert verdict.decision == "unknown"
    assert verdict.reason.startswith("evaluator_unavailable:")
