"""Policy CRUD, and proof that the org's configured flags actually reach the
Gemini judge (worker._grade_one passes the org's policy_config to gemini.evaluate)."""

from __future__ import annotations

import hashlib

from sqlalchemy import text

from app.db import SessionLocal, engine
from app.policy_snapshot import policy_snapshot_hash


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


def test_policy_flags_reach_the_judge(make_org, client, login, monkeypatch, configure_judge):
    org = make_org()
    configure_judge(org["org_id"])      # per-tenant judge routing: this org brings its own key
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

    def fake_eval(meta, policy_config=None, history=None, api_key=None, model=None):
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
        # P4 §A: judge_policy_config now projects this too, so it reaches the
        # judge. Asserting it here is what proves the wiring — the old 4-key
        # assertion would pass even if the field were dropped again.
        "confidence_threshold": "balanced",
    }


def test_policy_snapshot_is_bound_and_used_after_a_later_policy_change(
    make_org, client, login, monkeypatch, configure_judge,
):
    org = make_org()
    configure_judge(org["org_id"])
    admin = login(org["admin_email"], org["admin_password"])
    initial = {
        "pii_detection": False, "prompt_injection": True,
        "regulated_data_mode": True, "max_token_threshold": 777,
    }
    assert admin.put("/v1/policies", json=initial).status_code == 200
    _h = lambda s: hashlib.sha256(s.encode()).hexdigest()  # noqa: E731
    assert client.post("/v1/logs/batch", headers=org["auth"], json=[
        {"prompt_hash": _h("snapshot-p"), "response_hash": _h("snapshot-r"),
         "token_count": 50, "policy_tag": "chat"},
    ]).status_code == 202
    item = client.get("/v1/logs?limit=1", headers=org["auth"]).json()["items"][0]
    metadata = item["event_metadata"]
    assert item["chain_version"] == 3
    assert metadata["policy_snapshot_hash"] == policy_snapshot_hash(metadata["policy_snapshot"])
    assert metadata["policy_snapshot"]["max_token_threshold"] == 777

    updated = dict(initial, pii_detection=True, regulated_data_mode=False,
                   max_token_threshold=9_999)
    assert admin.put("/v1/policies", json=updated).status_code == 200

    from app import worker as workermod
    from app.schemas import Verdict
    captured = {}

    def fake_eval(meta, policy_config=None, history=None, api_key=None, model=None):
        captured["policy_config"] = policy_config
        return Verdict(decision="clean", reason="stub", risk_score=0)

    monkeypatch.setattr(workermod.gemini, "evaluate", fake_eval)
    db = SessionLocal()
    try:
        rows = workermod._claim_batch(db, 10, 300)
        assert len(rows) == 1
        workermod._grade_one(db, rows[0])
    finally:
        db.close()
    assert captured["policy_config"] == {
        "pii_detection": False, "prompt_injection": True,
        "regulated_data_mode": True, "max_token_threshold": 777,
        # The BOUND snapshot carries it too, so the judge grades this event
        # under the policy in force when it was recorded — not the later one.
        "confidence_threshold": "balanced",
    }

    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE audit_logs SET event_metadata = jsonb_set("
            "event_metadata, '{policy_snapshot,max_token_threshold}', '999'::jsonb) "
            "WHERE org_id = :org_id"
        ), {"org_id": org["org_id"]})
    verified = client.get("/v1/verify", headers=org["auth"]).json()
    assert verified["ok"] is False


# ── Judge-sensitivity fields are now REAL persisted policy, not cosmetic (Phase 5) ──

def test_judge_sensitivity_fields_persist(make_org, client, login):
    org = make_org()
    p = client.get("/v1/policies", headers=org["auth"]).json()
    # Safe defaults on first read. "flag" since 0059, not "block": a fresh org
    # must land on the ordinary path, never on the one that escalates email.
    assert p["enforcement_mode"] == "flag"
    assert p["confidence_threshold"] == "balanced"
    assert p["notify_on_breach"] == "immediate"

    ca = login(org["admin_email"], org["admin_password"])
    updated = ca.put("/v1/policies", json={
        "pii_detection": True, "prompt_injection": True,
        "regulated_data_mode": False, "max_token_threshold": 50000,
        "enforcement_mode": "flag", "confidence_threshold": "high",
        "notify_on_breach": "digest",
    }).json()
    assert updated["enforcement_mode"] == "flag"
    assert updated["confidence_threshold"] == "high"
    assert updated["notify_on_breach"] == "digest"
    assert client.get("/v1/policies", headers=org["auth"]).json() == updated   # persisted


def test_invalid_judge_sensitivity_value_rejected(make_org, login):
    org = make_org()
    ca = login(org["admin_email"], org["admin_password"])
    r = ca.put("/v1/policies", json={
        "pii_detection": True, "prompt_injection": True,
        "regulated_data_mode": False, "max_token_threshold": 50000,
        "enforcement_mode": "explode",                # not an allowed value
    })
    assert r.status_code == 422
