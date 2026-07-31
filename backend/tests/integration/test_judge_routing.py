"""The worker routes each org's grading to THEIR judge, on THEIR key.

Every provider call is mocked — no network. What is proved here:
  * provider choice (gemini / openai / both) is honoured per org,
  * BYOK mode decrypts the org's own key and passes it to that call only,
  * platform mode is allowed for premium and IGNORED for everyone else
    (a downgraded org cannot keep spending Foxy's key),
  * a missing/blank key degrades to evaluator_unavailable → the deterministic
    engine, never a crash and never a silent charge to the platform key,
  * enforcement events still skip the judge entirely,
  * no key ever reaches the verdict, the event row, or the chain.
"""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import text

from app import judge_routing, worker as workermod
from app.crypto_secrets import encrypt_secret
from app.db import SessionLocal, engine
from app.models import OrgPolicy, Organization
from app.schemas import Verdict

_h = lambda s: hashlib.sha256(s.encode()).hexdigest()  # noqa: E731

TENANT_GEMINI = "AIzaSy-TENANT-OWN-GEMINI"
TENANT_OPENAI = "sk-proj-TENANT-OWN-OPENAI"


def _configure(org_id: str, *, provider: str = "gemini", key_mode: str = "own",
               gemini_key: str | None = None, openai_key: str | None = None,
               plan_tier: str | None = None) -> None:
    """Write the org's live judge routing straight to the DB (API gating is tested
    separately in test_judge_policies_api.py)."""
    db = SessionLocal()
    try:
        oid = uuid.UUID(org_id)
        row = db.get(OrgPolicy, oid) or OrgPolicy(org_id=oid)
        row.judge_provider = provider
        row.judge_key_mode = key_mode
        row.gemini_key_enc = encrypt_secret(gemini_key, oid, "gemini") if gemini_key else None
        row.openai_key_enc = encrypt_secret(openai_key, oid, "openai") if openai_key else None
        db.add(row)
        db.get(Organization, oid).plan_tier = plan_tier
        db.commit()
    finally:
        db.close()


def _spy(monkeypatch, *, gemini_verdict: Verdict | None = None,
         openai_verdict: Verdict | None = None) -> dict:
    """Record every judge call (and the key it was handed) instead of calling out.

    ``models`` records the model id each call was routed to (P6f), so a test can
    assert the org's pick reached the provider and not just that a call happened.
    """
    calls: dict = {"gemini": [], "openai": [], "models": {}}

    def fake_gemini(meta, policy_config=None, history=None, api_key=None, model=None):
        calls["gemini"].append(api_key)
        calls["models"]["gemini"] = model
        return gemini_verdict or Verdict(policy_breach=False, reason="gemini says fine",
                                         risk_score=1, decision="clean")

    def fake_openai(meta, policy_config=None, history=None, api_key=None, model=None):
        calls["openai"].append(api_key)
        calls["models"]["openai"] = model
        return openai_verdict or Verdict(policy_breach=False, reason="openai says fine",
                                         risk_score=2, decision="clean")

    monkeypatch.setattr(workermod.gemini, "evaluate", fake_gemini)
    monkeypatch.setattr(workermod.openai_judge, "evaluate", fake_openai)
    return calls


def _ingest(client, org, seed: str = "a", **extra) -> None:
    body = {"prompt_hash": _h(f"p-{seed}"), "response_hash": _h(f"r-{seed}"),
            "token_count": 42, "policy_tag": "chat"}
    body.update(extra)
    assert client.post("/v1/logs/batch", headers=org["auth"], json=[body]).status_code == 202


def _grade_all() -> None:
    db = SessionLocal()
    try:
        for row in workermod._claim_batch(db, 10, 300):
            workermod._grade_one(db, row)
    finally:
        db.close()


def _verdicts() -> list[dict]:
    with engine.begin() as conn:
        return [r[0] for r in conn.execute(
            text("SELECT gemini_verdict FROM audit_logs ORDER BY seq")).all()]


# ── provider choice ──────────────────────────────────────────────────────────

def test_gemini_only_org_calls_only_gemini(make_org, client, monkeypatch):
    org = make_org()
    _configure(org["org_id"], provider="gemini", gemini_key=TENANT_GEMINI)
    calls = _spy(monkeypatch)
    _ingest(client, org)
    _grade_all()
    assert calls["gemini"] == [TENANT_GEMINI]
    assert calls["openai"] == []


def test_openai_only_org_calls_only_openai(make_org, client, monkeypatch):
    org = make_org()
    _configure(org["org_id"], provider="openai", openai_key=TENANT_OPENAI)
    calls = _spy(monkeypatch)
    _ingest(client, org)
    _grade_all()
    assert calls["openai"] == [TENANT_OPENAI]
    assert calls["gemini"] == []


def test_both_org_calls_both_and_combines(make_org, client, monkeypatch):
    org = make_org()
    _configure(org["org_id"], provider="both",
               gemini_key=TENANT_GEMINI, openai_key=TENANT_OPENAI)
    calls = _spy(
        monkeypatch,
        gemini_verdict=Verdict(policy_breach=False, reason="gemini clean",
                               risk_score=1, decision="clean"),
        openai_verdict=Verdict(policy_breach=True, reason="openai breach",
                               risk_score=90, decision="breach"))
    _ingest(client, org)
    _grade_all()
    assert calls["gemini"] == [TENANT_GEMINI]
    assert calls["openai"] == [TENANT_OPENAI]
    verdict = _verdicts()[0]
    assert verdict["decision"] == "breach"            # any known breach wins
    assert verdict["reason"].startswith("multi_judge_breach")


# ── key source: own (BYOK) vs platform, and the tier gate ────────────────────

def test_premium_platform_mode_uses_the_platform_key(make_org, client, monkeypatch):
    org = make_org()
    _configure(org["org_id"], provider="both", key_mode="platform",
               plan_tier="premium")
    calls = _spy(monkeypatch)
    _ingest(client, org)
    _grade_all()
    # api_key=None → the provider falls back to settings.* (Foxy's platform key).
    assert calls["gemini"] == [None]
    assert calls["openai"] == [None]


def test_platform_mode_is_ignored_for_a_non_premium_org(make_org, client, monkeypatch):
    """Defence in depth: a premium org that set platform mode and then DOWNGRADED
    must stop spending Foxy's key at grading time, not just at the API."""
    org = make_org()
    _configure(org["org_id"], provider="gemini", key_mode="platform",
               gemini_key=TENANT_GEMINI, plan_tier="pro")
    calls = _spy(monkeypatch)
    _ingest(client, org)
    _grade_all()
    assert calls["gemini"] == [TENANT_GEMINI]         # their own key, not the platform's


def test_downgraded_org_without_a_key_is_unavailable_not_billed_to_the_platform(
        make_org, client, monkeypatch):
    org = make_org()
    _configure(org["org_id"], provider="gemini", key_mode="platform", plan_tier="free")
    calls = _spy(monkeypatch)
    _ingest(client, org)
    _grade_all()
    assert calls["gemini"] == []                      # never called on the platform key
    # No AI grade was claimed: the row carries the deterministic metadata verdict,
    # which is explicit that semantic content was not evaluated.
    assert "semantic content not evaluated" in _verdicts()[0]["reason"]


def test_missing_key_produces_evaluator_unavailable_before_the_fallback(
        make_org, monkeypatch):
    """The raw judge step (pre-fallback) is an honest evaluator_unavailable —
    never an exception, never a fabricated grade."""
    org = make_org()
    _configure(org["org_id"], provider="both", key_mode="own")
    _spy(monkeypatch)
    db = SessionLocal()
    try:
        verdict = workermod._judge_verdict(
            db, org["org_id"], {"token_count": 1}, None, {})
    finally:
        db.close()
    assert verdict.decision == "unknown"
    assert verdict.reason.startswith("evaluator_unavailable:no_byok_key")


# ── missing keys degrade honestly ────────────────────────────────────────────

def test_missing_byok_key_yields_evaluator_unavailable_without_a_crash(
        make_org, client, monkeypatch):
    org = make_org()
    _configure(org["org_id"], provider="gemini", key_mode="own", gemini_key=None)
    calls = _spy(monkeypatch)
    _ingest(client, org)
    _grade_all()
    assert calls["gemini"] == []
    with engine.begin() as conn:
        status = conn.execute(text("SELECT grading_status FROM audit_logs")).scalar()
    assert status == "graded"                          # graded deterministically, not crashed


def test_blank_byok_key_is_treated_as_missing(make_org, client, monkeypatch):
    org = make_org()
    _configure(org["org_id"], provider="openai", key_mode="own")
    db = SessionLocal()
    try:
        row = db.get(OrgPolicy, uuid.UUID(org["org_id"]))
        row.openai_key_enc = "   "                     # blank/garbage ciphertext
        db.commit()
    finally:
        db.close()
    calls = _spy(monkeypatch)
    _ingest(client, org)
    _grade_all()
    assert calls["openai"] == []
    with engine.begin() as conn:
        assert conn.execute(text("SELECT grading_status FROM audit_logs")).scalar() == "graded"


def test_both_with_one_missing_key_still_grades_on_the_other(make_org, client, monkeypatch):
    org = make_org()
    _configure(org["org_id"], provider="both", gemini_key=TENANT_GEMINI)
    calls = _spy(monkeypatch)
    _ingest(client, org)
    _grade_all()
    assert calls["gemini"] == [TENANT_GEMINI]
    assert calls["openai"] == []
    assert _verdicts()[0]["decision"] == "clean"


def test_routing_is_resolved_per_org_not_globally(make_org, client, monkeypatch):
    a, b = make_org(), make_org()
    _configure(a["org_id"], provider="gemini", gemini_key=TENANT_GEMINI)
    _configure(b["org_id"], provider="openai", openai_key=TENANT_OPENAI)
    calls = _spy(monkeypatch)
    _ingest(client, a, seed="a")
    _ingest(client, b, seed="b")
    _grade_all()
    assert calls["gemini"] == [TENANT_GEMINI]
    assert calls["openai"] == [TENANT_OPENAI]


# ── the judge stays content-blind and key-blind ──────────────────────────────

def test_enforcement_events_still_skip_the_judge(make_org, client, monkeypatch):
    org = make_org()
    _configure(org["org_id"], provider="both",
               gemini_key=TENANT_GEMINI, openai_key=TENANT_OPENAI)
    calls = _spy(monkeypatch)
    _ingest(client, org, event_type="blocked", token_count=0,
            event_metadata={"decision": "blocked", "blocked_reason": "pii_ssn_detected",
                            "policy_rules": ["pii.ssn"]})
    _grade_all()
    assert calls["gemini"] == [] and calls["openai"] == []
    assert _verdicts()[0]["decision"] == "blocked"


def test_no_key_reaches_the_verdict_event_or_chain(make_org, client, monkeypatch):
    org = make_org()
    _configure(org["org_id"], provider="both",
               gemini_key=TENANT_GEMINI, openai_key=TENANT_OPENAI)
    _spy(monkeypatch)
    _ingest(client, org)
    _grade_all()
    with engine.begin() as conn:
        blob = " ".join(str(r) for r in conn.execute(text(
            "SELECT gemini_verdict::text, event_metadata::text, chain_hash "
            "FROM audit_logs")).all())
        events = " ".join(str(r) for r in conn.execute(text(
            "SELECT payload::text, event_hash FROM audit_events")).all())
    for secret in (TENANT_GEMINI, TENANT_OPENAI):
        assert secret not in blob
        assert secret not in events


# ── the tier matrix lives in ONE constant ────────────────────────────────────

def test_platform_key_tiers_is_the_single_source_of_truth():
    assert judge_routing.PLATFORM_KEY_TIERS == {"premium"}
    assert judge_routing.platform_keys_allowed("premium") is True
    for tier in ("pro", "free", "trial", "max", None, "other"):
        assert judge_routing.platform_keys_allowed(tier) is False
