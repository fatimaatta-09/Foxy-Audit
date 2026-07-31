"""The SDK-facing enforcement field (P4 §B, migration 0056).

`enforcement_mode` and `sdk_enforcement` are different settings that share a
word. The first says what to do with a verdict AFTER the judge grades an
interaction; the second says what to do BEFORE the model is called. This file
pins that they stay independent, and that the new one starts NULL for everybody.

NULL is the whole point. `enforcement_mode` defaults to "block" and a default
policy row is written on first read, so every existing workspace already stores
"block" whether or not a human chose it. A nullable field with no default is the
only way "nobody has decided" is expressible at all.
"""

from __future__ import annotations

import uuid

from app.db import SessionLocal
from app.models import OrgPolicy


def _policy_row(org_id):
    with SessionLocal() as db:
        return db.get(OrgPolicy, uuid.UUID(str(org_id)))


def _put(client_or_session, body, org=None):
    if org is not None:
        return client_or_session.put("/v1/policies", headers=org["auth"], json=body)
    return client_or_session.put("/v1/policies", json=body)


def _base_body(**over):
    body = {
        "pii_detection": True, "prompt_injection": True,
        "regulated_data_mode": False, "max_token_threshold": 50_000,
        "enforcement_mode": "block", "confidence_threshold": "balanced",
        "notify_on_breach": "immediate", "judge_provider": "gemini",
        "judge_key_mode": "own",
    }
    body.update(over)
    return body


# ══ it starts unset for everybody ══════════════════════════════════════════

def test_a_new_org_has_no_sdk_enforcement(make_org, client):
    """Every workspace reads NULL on upgrade, so shipping this changes nothing
    for anyone until an owner deliberately chooses."""
    org = make_org()
    body = client.get("/v1/policies", headers=org["auth"]).json()
    assert "sdk_enforcement" in body, "the SDK cannot read a field that is absent"
    assert body["sdk_enforcement"] is None


def test_the_column_has_no_server_default(make_org, client):
    """A default would recreate the exact problem the field exists to avoid.

    The contrast beside it is the argument: `_get_or_create` writes a policy row
    on first read, so a column WITH a default hands every workspace a value
    nobody chose. That is why sdk_enforcement is nullable — and P5's migration
    0059 is the same lesson collected late, moving enforcement_mode's inherited
    default off `block` before anything started acting on it. What this test
    guards is the presence of the asymmetry, not the value on the right of it."""
    org = make_org()
    client.get("/v1/policies", headers=org["auth"])      # forces _get_or_create
    row = _policy_row(org["org_id"])
    assert row is not None
    assert row.sdk_enforcement is None
    assert row.enforcement_mode == "flag", (
        "enforcement_mode still has a server default — 'flag' since 0059 — and "
        "this test documents WHY sdk_enforcement had to be a separate field")


# ══ the two fields are independent ═════════════════════════════════════════

def test_setting_the_judge_field_never_moves_the_sdk_field(make_org, login, client):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    for judge_value in ("block", "flag", "monitor"):
        r = _put(c, _base_body(enforcement_mode=judge_value))
        assert r.status_code == 200, r.text
        assert r.json()["enforcement_mode"] == judge_value
        assert r.json()["sdk_enforcement"] is None


def test_setting_the_sdk_field_never_moves_the_judge_field(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    r = _put(c, _base_body(enforcement_mode="monitor", sdk_enforcement="block"))
    assert r.status_code == 200, r.text
    assert r.json()["sdk_enforcement"] == "block"
    assert r.json()["enforcement_mode"] == "monitor"


# ══ omission means KEEP — the field must survive an unaware client ═════════

def test_a_body_without_the_field_does_not_wipe_it(make_org, login):
    """The desktop and the dashboard both build their PUT body from a fixed key
    list that predates this field. If absent meant None, either of them would
    silently erase the owner's choice on an unrelated save."""
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    assert _put(c, _base_body(sdk_enforcement="block")).json()["sdk_enforcement"] == "block"

    body = _base_body()                       # exactly what an older client sends
    assert "sdk_enforcement" not in body
    r = _put(c, body)
    assert r.status_code == 200, r.text
    assert r.json()["sdk_enforcement"] == "block", "an unaware client wiped the field"


def test_an_explicit_null_does_clear_it(make_org, login):
    """Absent means keep; present-and-null means the owner chose to stop."""
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    _put(c, _base_body(sdk_enforcement="block"))
    r = _put(c, _base_body(sdk_enforcement=None))
    assert r.status_code == 200, r.text
    assert r.json()["sdk_enforcement"] is None


def test_only_the_three_sdk_modes_are_accepted(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    for good in ("observe", "redact", "block"):
        assert _put(c, _base_body(sdk_enforcement=good)).status_code == 200
    for bad in ("flag", "monitor", "BLOCK", "", "yes"):
        assert _put(c, _base_body(sdk_enforcement=bad)).status_code == 422, bad


# ══ the SDK can actually read it with its own key ══════════════════════════

def test_the_sdk_bearer_key_can_read_the_field(make_org, client):
    """GET /v1/policies takes either a session or the SDK key (resolve_org), which
    is why P4 §B needed no new endpoint."""
    org = make_org()
    r = client.get("/v1/policies", headers=org["auth"])
    assert r.status_code == 200
    assert "sdk_enforcement" in r.json()


def test_writing_still_requires_an_admin_human(make_org, client):
    """A bare SDK key must not be able to rewrite the policy that governs it."""
    org = make_org()
    r = client.put("/v1/policies", headers=org["auth"],
                   json=_base_body(sdk_enforcement="observe"))
    assert r.status_code in (401, 403), r.text


def test_a_member_cannot_change_it(make_org, add_user, login):
    org = make_org()
    add_user(org["org_id"], "mem@test.dev", "memberpass1", role="member")
    c = login("mem@test.dev", "memberpass1")
    assert _put(c, _base_body(sdk_enforcement="block")).status_code == 403


# ══ audit trail ════════════════════════════════════════════════════════════

def test_the_change_is_recorded_in_account_history(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    _put(c, _base_body(sdk_enforcement="block"))
    rows = c.get("/v1/account/audit").json()
    entries = rows if isinstance(rows, list) else rows.get("items", [])
    policy_rows = [e for e in entries if e.get("action") == "policy.update"]
    assert policy_rows, "a policy change was not recorded"
    assert policy_rows[0].get("detail", {}).get("sdk_enforcement") == "block"


# ══ the evidence snapshot is untouched ═════════════════════════════════════

def test_the_policy_snapshot_schema_did_not_change(make_org, login):
    """foxy-policy-v1 has to stay structurally stable, or new snapshots stop
    being comparable with every snapshot already handed to an auditor. This field
    is deliberately NOT in it — it governs the SDK, not the assessment."""
    from app.policy_snapshot import POLICY_SNAPSHOT_SCHEMA, capture_policy_snapshot
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    _put(c, _base_body(sdk_enforcement="block"))
    snapshot = capture_policy_snapshot(_policy_row(org["org_id"]))
    assert snapshot["schema"] == POLICY_SNAPSHOT_SCHEMA == "foxy-policy-v1"
    assert "sdk_enforcement" not in snapshot
    assert set(snapshot) == {
        "schema", "pii_detection", "prompt_injection", "regulated_data_mode",
        "max_token_threshold", "enforcement_mode", "confidence_threshold",
        "notify_on_breach"}
