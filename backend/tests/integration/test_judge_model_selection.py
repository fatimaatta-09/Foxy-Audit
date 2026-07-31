"""P6f — an org picks WHICH model of its chosen provider grades its events.

What these prove, in the order the risk sits:

  * the chosen model actually reaches the provider call (otherwise the whole
    feature is a setting that does nothing),
  * a provider KEY never rides along with it into the verdict, the persisted
    payload, or the chain — a model id is public, a key is a crown jewel,
  * an unknown stored model still grades, falling back rather than failing,
  * the API reports the EFFECTIVE model, not the deployment setting, so the
    dashboard cannot show a model the worker would not call,
  * a client that has never heard of these fields cannot erase them.
"""

from __future__ import annotations

import json
import sys
import uuid
from types import SimpleNamespace

from app import gemini as gemini_mod
from app import judge, judge_routing
from app.config import get_settings
from app.db import SessionLocal
from app.models import OrgPolicy
from app.schemas import Verdict

_CLEAN = {"policy_breach": False, "reason": "nothing of concern in this metadata",
          "risk_score": 0, "decision": "clean", "rules": []}


class _FakeGenAI:
    """Stand-in for google.generativeai; records the model id it was asked for.

    Mirrors test_judge_byok_providers._FakeGenAI, which records the KEY — the two
    together cover both halves of "the right model, and not the key".
    """

    def __init__(self):
        self.model_id = None
        self.configured_with = None

    def configure(self, api_key=None, **_kw):
        self.configured_with = api_key

    def GenerativeModel(self, model_id, *_a, **_kw):  # noqa: N802 — SDK's name
        self.model_id = model_id
        return SimpleNamespace(
            generate_content=lambda *_args, **_kwargs:
                SimpleNamespace(text=json.dumps(_CLEAN)))


def _install_fake_genai(monkeypatch) -> _FakeGenAI:
    fake = _FakeGenAI()
    monkeypatch.setitem(sys.modules, "google.generativeai", fake)
    return fake

_BASE = {"pii_detection": True, "prompt_injection": True,
         "regulated_data_mode": False, "max_token_threshold": 50000}

TENANT_GEMINI = "AIzaSy-tenant-gemini-key-for-model-tests"


def _put(admin, **fields):
    return admin.put("/v1/policies", json={**_BASE, **fields})


def _row(org_id: str) -> OrgPolicy:
    db = SessionLocal()
    try:
        row = db.get(OrgPolicy, uuid.UUID(org_id))
        db.expunge(row)
        return row
    finally:
        db.close()


def _pin(org_id: str, **columns) -> None:
    """Write a model pin straight to the row, bypassing the API's allow-list.

    Used only to manufacture the state the API refuses to create — a pin for a
    model that has since been withdrawn.
    """
    db = SessionLocal()
    try:
        row = db.get(OrgPolicy, uuid.UUID(org_id))
        for name, value in columns.items():
            setattr(row, name, value)
        db.commit()
    finally:
        db.close()


# ── resolution: an unknown model must never stop a grade ─────────────────────

def test_a_withdrawn_model_falls_back_instead_of_failing_the_grade():
    # The failure mode this guards: a provider retires a model id, and every
    # event for the orgs pinned to it silently stops being graded.
    assert judge_routing.resolve_model("gemini", "gemini-1.0-retired") == \
        get_settings().gemini_model
    assert judge_routing.resolve_model("openai", "gpt-1") == \
        get_settings().openai_model


def test_a_known_pin_is_honoured_and_absence_means_the_default():
    pinned = judge_routing.allowed_models("gemini")[-1]
    assert judge_routing.resolve_model("gemini", pinned) == pinned
    assert judge_routing.resolve_model("gemini", None) == get_settings().gemini_model
    assert judge_routing.resolve_model("gemini", "") == get_settings().gemini_model


def test_the_deployment_default_is_always_offered():
    # allowed_models prepends the running default, so a deployment can move to a
    # model the constant has never heard of without this list going stale.
    assert judge_routing.allowed_models("gemini")[0] == get_settings().gemini_model
    assert judge_routing.allowed_models("openai")[0] == get_settings().openai_model
    assert len(set(judge_routing.allowed_models("gemini"))) == \
        len(judge_routing.allowed_models("gemini")), "duplicated ids"


# ── the pin reaches the provider call ────────────────────────────────────────

def test_the_pinned_model_is_the_model_sent_to_the_provider(monkeypatch):
    fake = _install_fake_genai(monkeypatch)
    verdict = gemini_mod.evaluate({"token_count": 10}, None,
                                  api_key="AIza-test-key", model="gemini-2.5-pro")

    assert fake.model_id == "gemini-2.5-pro"
    # ...and the record agrees with the call. These two being the same value is
    # the entire point of recording it.
    assert verdict.judge_model == "gemini-2.5-pro"
    assert verdict.judge_provider == "gemini"


def test_without_a_pin_the_deployment_default_is_called_and_recorded(monkeypatch):
    fake = _install_fake_genai(monkeypatch)
    verdict = gemini_mod.evaluate({"token_count": 10}, None, api_key="AIza-test-key")
    assert fake.model_id == get_settings().gemini_model
    assert verdict.judge_model == get_settings().gemini_model


def test_an_unavailable_evaluator_names_no_model(monkeypatch):
    # Nothing graded this, so nothing may be credited with grading it.
    monkeypatch.setattr(gemini_mod, "get_settings",
                        lambda: SimpleNamespace(gemini_api_key="", gemini_fail_closed=False,
                                                gemini_model="gemini-2.5-flash",
                                                gemini_timeout=5))
    verdict = gemini_mod.evaluate({"token_count": 10}, None, api_key=None)
    assert verdict.decision == "unknown"
    assert verdict.judge_model is None
    assert verdict.judge_provider is None


# ── the key must not travel with the model ───────────────────────────────────

def test_no_provider_key_reaches_the_verdict(monkeypatch):
    secret = "AIzaSy-this-key-must-never-be-recorded"
    fake = _install_fake_genai(monkeypatch)
    verdict = gemini_mod.evaluate({"token_count": 10}, None,
                                  api_key=secret, model="gemini-2.5-flash")
    assert fake.configured_with == secret      # the key WAS used for the call...

    # ...and still appears nowhere in what gets persisted. Every field, not just
    # the two added here — a key must not appear anywhere on this object.
    assert secret not in str(verdict.model_dump())
    assert not any(field.endswith(("_key", "_key_enc", "api_key"))
                   for field in verdict.model_dump())


def test_a_failure_message_cannot_leak_the_key_through_the_verdict(monkeypatch):
    # Google puts the key in the request URL, and several exception types in that
    # stack embed the URL in str(exc). The verdict carries the exception TYPE only.
    secret = "AIzaSy-leaky-key-in-the-url"

    class _Boom:
        def configure(self, **_kw):
            raise RuntimeError("400 from https://api/v1?key=" + secret)

    monkeypatch.setitem(sys.modules, "google.generativeai", _Boom())
    verdict = gemini_mod.evaluate({"token_count": 10}, None,
                                  api_key=secret, model="gemini-2.5-flash")
    assert secret not in str(verdict.model_dump())


# ── provenance survives the paths that rebuild a verdict ─────────────────────

def test_quarantine_keeps_the_model_that_produced_the_bad_answer():
    bad = Verdict(policy_breach=True, reason="x", decision="clean",
                  judge_provider="gemini", judge_model="gemini-2.5-pro")
    out = judge.validate(bad)
    assert out.decision == "unknown"
    assert out.judge_model == "gemini-2.5-pro"


def test_both_providers_are_credited_and_a_dead_one_is_not():
    good = Verdict(policy_breach=False, reason="metadata looks ordinary",
                   decision="clean", judge_provider="gemini",
                   judge_model="gemini-2.5-flash")
    other = Verdict(policy_breach=True, reason="pii signalled in the prompt",
                    risk_score=70, decision="breach", judge_provider="openai",
                    judge_model="gpt-5.6")
    dead = Verdict(reason="evaluator_unavailable:TimeoutError", decision="unknown")

    both = judge.combine(good, other)
    assert both.judge_provider == "gemini,openai"
    assert both.judge_model == "gemini-2.5-flash,gpt-5.6"

    # A provider that fell over did not grade this event and must not look as if
    # it did — this is an evidence record, not a roster of who was asked.
    one = judge.combine(good, dead)
    assert one.judge_provider == "gemini"
    assert one.judge_model == "gemini-2.5-flash"


def test_an_old_stored_verdict_still_parses():
    # Rows written before 0058 have no provenance keys at all.
    old = Verdict(**{"policy_breach": False, "reason": "clean enough",
                     "risk_score": 0, "decision": "clean", "rules": []})
    assert old.judge_provider is None and old.judge_model is None


# ── the API ──────────────────────────────────────────────────────────────────

def test_get_offers_the_choices_and_reports_the_effective_model(make_org, client):
    body = client.get("/v1/policies", headers=make_org()["auth"]).json()
    assert body["judge_gemini_model"] is None      # resting state: inherit
    assert body["judge_openai_model"] is None
    assert body["judge_models"]["gemini"] == get_settings().gemini_model
    assert get_settings().gemini_model in body["judge_models_available"]["gemini"]
    assert get_settings().openai_model in body["judge_models_available"]["openai"]


def test_a_pin_is_stored_and_reported_as_the_effective_model(make_org, client, login):
    org = make_org()
    admin = login(org["admin_email"], org["admin_password"])
    assert _put(admin, judge_gemini_model="gemini-2.5-pro").status_code == 200

    assert _row(org["org_id"]).gemini_judge_model == "gemini-2.5-pro"
    body = client.get("/v1/policies", headers=org["auth"]).json()
    assert body["judge_gemini_model"] == "gemini-2.5-pro"
    # The readout must follow the pin, not settings — this is the assertion that
    # catches a dashboard advertising a model the worker would never call.
    assert body["judge_models"]["gemini"] == "gemini-2.5-pro"


def test_an_empty_string_unpins_back_to_the_default(make_org, client, login):
    org = make_org()
    admin = login(org["admin_email"], org["admin_password"])
    _put(admin, judge_gemini_model="gemini-2.5-pro")
    assert _put(admin, judge_gemini_model="").status_code == 200

    assert _row(org["org_id"]).gemini_judge_model is None
    body = client.get("/v1/policies", headers=org["auth"]).json()
    assert body["judge_models"]["gemini"] == get_settings().gemini_model


def test_an_unrecognised_model_is_rejected_not_silently_corrected(make_org, login):
    org = make_org()
    admin = login(org["admin_email"], org["admin_password"])
    response = _put(admin, judge_gemini_model="gpt-5.6")   # right id, wrong vendor
    assert response.status_code == 422
    assert _row(org["org_id"]).gemini_judge_model is None


def test_an_omitted_field_keeps_the_stored_pin(make_org, client, login):
    # The desktop client (desktop/policy_data.py::put_body) does not send these
    # fields. A desktop save must not erase what the web dashboard pinned.
    org = make_org()
    admin = login(org["admin_email"], org["admin_password"])
    _put(admin, judge_gemini_model="gemini-2.5-pro")

    assert _put(admin, judge_provider="gemini").status_code == 200
    assert _row(org["org_id"]).gemini_judge_model == "gemini-2.5-pro"


def test_a_withdrawn_pin_does_not_block_unrelated_policy_edits(make_org, login):
    org = make_org()
    admin = login(org["admin_email"], org["admin_password"])
    _put(admin, judge_gemini_model="gemini-2.5-pro")
    _pin(org["org_id"], gemini_judge_model="gemini-0.9-withdrawn")

    # Re-sending what is already stored is allowed, so an org pinned to a model
    # that has left the allow-list is not locked out of its own policy page.
    assert _put(admin, judge_gemini_model="gemini-0.9-withdrawn").status_code == 200
    assert _put(admin, judge_gemini_model="gemini-2.5-pro").status_code == 200


def test_recording_the_model_on_a_verdict_cannot_break_a_chain(make_org, client):
    """The load-bearing claim of P6f: the verdict is NOT hashed chain material.

    Proved end-to-end rather than argued. A real export is verified by the
    INDEPENDENT verifier; then the verdict is rewritten — including the model
    ids this phase added — and the chain must still verify. A hashed field is
    tampered with straight afterwards as the control, so a verifier that simply
    said "ok" to everything could not pass both halves.
    """
    import hashlib
    import importlib.util
    import os

    org = make_org()
    rows = [{"prompt_hash": hashlib.sha256(f"p{i}".encode()).hexdigest(),
             "response_hash": hashlib.sha256(f"r{i}".encode()).hexdigest(),
             "token_count": 10 * i + 5, "policy_tag": "chat"} for i in range(3)]
    assert client.post("/v1/logs/batch", json=rows,
                       headers=org["auth"]).status_code == 202

    export = client.get("/v1/logs/export", headers=org["auth"]).json()

    vpath = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                         "verifier", "foxy_verify.py")
    spec = importlib.util.spec_from_file_location("foxy_verify", vpath)
    fv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fv)

    assert fv.verify_export(export)["ok"] is True

    # Write a full verdict, provenance and all, onto every exported row.
    for entry in export["logs"]:
        entry["gemini_verdict"] = Verdict(
            policy_breach=False, reason="metadata looks ordinary",
            decision="clean", judge_provider="gemini",
            judge_model="gemini-2.5-pro").model_dump()
    assert fv.verify_export(export)["ok"] is True, (
        "adding provenance to the verdict changed the chain — the model must "
        "not be recorded anywhere the hash covers"
    )

    # Control: a genuinely hashed field still breaks it, at the right seq.
    export["logs"][1]["token_count"] = 999
    assert fv.verify_export(export)["first_broken_seq"] == 2


def test_the_pin_is_recorded_in_the_account_audit_but_the_key_is_not(make_org, client, login):
    org = make_org()
    admin = login(org["admin_email"], org["admin_password"])
    _put(admin, judge_gemini_model="gemini-2.5-pro", gemini_api_key=TENANT_GEMINI)

    events = admin.get("/v1/account/audit").json()
    text = str(events)
    assert "gemini-2.5-pro" in text          # a model id is not a secret
    assert TENANT_GEMINI not in text          # a key is
