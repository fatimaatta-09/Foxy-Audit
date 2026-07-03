"""Policy CRUD, and proof that the org's configured flags actually reach the
Gemini judge (worker._grade_one passes the org's policy_config to gemini.evaluate)."""

from __future__ import annotations

import hashlib


def test_get_defaults_and_update(make_org, client, login):
    org = make_org()
    p = client.get("/v1/policies", headers=org["auth"]).json()
    assert p["pii_detection"] is True
    assert p["max_token_threshold"] == 50000

    # Policy WRITES are a privileged act: admin dashboard session, not the SDK key
    # (see test_staff_auth.py::test_policy_write_rejects_bare_bearer).
    ca = login(org["admin_email"], org["admin_password"])
    updated = ca.put("/v1/policies", json={
        "pii_detection": False, "prompt_injection": True,
        "regulated_data_mode": True, "max_token_threshold": 12345,
    }).json()
    assert updated["regulated_data_mode"] is True
    assert updated["max_token_threshold"] == 12345
    # Persisted.
    assert client.get("/v1/policies", headers=org["auth"]).json() == updated


def test_policy_flags_reach_the_judge(make_org, client, login, monkeypatch):
    org = make_org()
    ca = login(org["admin_email"], org["admin_password"])
    assert ca.put("/v1/policies", json={
        "pii_detection": False, "prompt_injection": False,
        "regulated_data_mode": True, "max_token_threshold": 777,
    }).status_code == 200
    _h = lambda s: hashlib.sha256(s.encode()).hexdigest()  # noqa: E731
    client.post("/v1/logs/batch", headers=org["auth"], json=[
        {"prompt_hash": _h("p"), "response_hash": _h("r"), "token_count": 50, "policy_tag": "chat"},
    ])

    # Capture the policy_config the worker hands to the judge.
    from app import worker as workermod
    from app.schemas import Verdict
    captured = {}

    def fake_eval(meta, policy_config=None):
        captured["policy_config"] = policy_config
        return Verdict(policy_breach=False, reason="stub", risk_score=0)

    monkeypatch.setattr(workermod.gemini, "evaluate", fake_eval)

    from app.db import SessionLocal
    db = SessionLocal()
    try:
        rows = workermod._claim_batch(db, 10, 300)
        assert len(rows) >= 1
        for row in rows:
            workermod._grade_one(db, row)
    finally:
        db.close()

    assert captured["policy_config"] == {
        "pii_detection": False, "prompt_injection": False,
        "regulated_data_mode": True, "max_token_threshold": 777,
    }
