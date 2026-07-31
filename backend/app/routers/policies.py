"""GET /v1/policies  — fetch the org's active policy configuration.
PUT /v1/policies  — update policy toggles.

Core Requirement #1: Active Policy Configuration.

A row is auto-created with safe defaults on first GET so the table is never
empty. Gemini grading reads these flags via gemini.evaluate(meta, policy_config).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import account_audit
from ..auth import require_role, resolve_org
from ..crypto_secrets import SecretsNotConfigured, encrypt_secret
from ..db import get_db
from ..judge_routing import allowed_models, platform_keys_allowed, resolve_model
from ..models import OrgPolicy, Organization, User

router = APIRouter()


class PolicyConfig(BaseModel):
    """The org's policy as READ (response) and WRITTEN (request).

    Provider keys are write-only: they can be submitted here, but a read only
    ever reports whether one is set. No response model in this service exposes
    key material.
    """

    pii_detection: bool = True
    prompt_injection: bool = True
    regulated_data_mode: bool = False
    max_token_threshold: int = Field(default=50_000, ge=1, le=10_000_000)
    # Judge sensitivity (Phase 5) — how a finding is RESPONDED to + how you are
    # notified. Never how it is graded: nothing in the judge path reads this.
    # "flag" since migration 0059, matching the column default — "block"
    # escalates breach email, so it has to be chosen, never inherited (P5 §A.1).
    enforcement_mode: Literal["block", "flag", "monitor"] = "flag"
    # SDK PREFLIGHT enforcement — a different vocabulary from enforcement_mode
    # above, and a different moment: before the model is called, not after a
    # verdict. None means the workspace has expressed no opinion and the SDK
    # ignores org policy entirely (P4 §B, migration 0056).
    #
    # OMITTING this key on a PUT means "keep what is stored", the same contract
    # the provider keys already use. That is load-bearing: the desktop and the
    # dashboard both build their PUT body from a fixed key list that predates
    # this field, so treating absent as None would let either of them silently
    # wipe an owner's choice on an unrelated save.
    sdk_enforcement: Literal["observe", "redact", "block"] | None = None
    confidence_threshold: Literal["high", "balanced", "low"] = "balanced"
    notify_on_breach: Literal["immediate", "digest", "none"] = "immediate"
    # Optional destinations for the breach notifier (P2 · §F).
    notify_email: str | None = Field(default=None, max_length=320)
    notify_webhook_url: str | None = Field(default=None, max_length=1024)
    # ── Per-tenant AI Judge selection (0053) ──
    judge_provider: Literal["gemini", "openai", "both"] = "gemini"
    judge_key_mode: Literal["own", "platform"] = "own"
    # WRITE-ONLY. Submit a key to store it (encrypted); submit "" to clear it;
    # omit to leave it untouched. Never populated on a response.
    gemini_api_key: str | None = Field(default=None, max_length=512, exclude=True)
    openai_api_key: str | None = Field(default=None, max_length=512, exclude=True)
    # READ-ONLY, derived. Presence booleans + what this plan is allowed to do.
    gemini_key_set: bool = False
    openai_key_set: bool = False
    plan_tier: str | None = None
    platform_keys_allowed: bool = False
    # WRITABLE (P6f · 0058). Which model of the chosen provider grades this org.
    # None or "" means inherit the deployment default, and that is the resting
    # state for every tenant — see migration 0058 for why there is no default here.
    judge_gemini_model: str | None = Field(default=None, max_length=64)
    judge_openai_model: str | None = Field(default=None, max_length=64)
    # §7.6 · read-only. Which model each provider will ACTUALLY grade with —
    # the org's pick where it has one, the deployment default otherwise. It
    # resolves through the same function the worker routes with, so this can
    # never advertise a model the judge would not use.
    # Additive: the desktop reads this endpoint (desktop/dashboard.py:3177) and
    # takes named keys, so a new key is inert there.
    judge_models: dict[str, str] = {}
    # READ-ONLY, derived. The choices the dashboard's model <select> offers.
    judge_models_available: dict[str, list[str]] = {}


def _to_config(row: OrgPolicy, org: Organization | None = None) -> "PolicyConfig":
    """Project a policy row for the API — presence booleans only, never a key."""
    tier = org.plan_tier if org is not None else None
    return PolicyConfig(
        pii_detection=row.pii_detection,
        prompt_injection=row.prompt_injection,
        regulated_data_mode=row.regulated_data_mode,
        max_token_threshold=row.max_token_threshold,
        enforcement_mode=row.enforcement_mode,
        sdk_enforcement=row.sdk_enforcement,
        confidence_threshold=row.confidence_threshold,
        notify_on_breach=row.notify_on_breach,
        notify_email=row.notify_email,
        notify_webhook_url=row.notify_webhook_url,
        judge_provider=row.judge_provider,
        judge_key_mode=row.judge_key_mode,
        gemini_key_set=bool((row.gemini_key_enc or "").strip()),
        openai_key_set=bool((row.openai_key_enc or "").strip()),
        plan_tier=tier,
        platform_keys_allowed=platform_keys_allowed(tier),
        judge_gemini_model=row.gemini_judge_model,
        judge_openai_model=row.openai_judge_model,
        # Resolved, not reported raw: an org pinned to a model that has since been
        # withdrawn would otherwise be shown a name the worker no longer calls.
        judge_models={
            "gemini": resolve_model("gemini", row.gemini_judge_model),
            "openai": resolve_model("openai", row.openai_judge_model),
        },
        judge_models_available={"gemini": list(allowed_models("gemini")),
                                "openai": list(allowed_models("openai"))},
    )


def _checked_model(provider: str, submitted: str | None,
                   current: str | None = None) -> str | None:
    """Validate a submitted model id; "" and None both mean "inherit the default".

    Storing NULL rather than the resolved id is deliberate — a tenant who never
    expressed a preference keeps following the deployment forward (0058).

    A value already on the row always passes, even if it has since left the
    allow-list. Otherwise an org pinned to a withdrawn model could not save ANY
    policy change — unrelated edits would 422 on a field they never touched —
    which is a worse failure than letting a stale pin sit there resolving to the
    default. The dashboard flags it; grading already falls back (resolve_model).
    """
    value = (submitted or "").strip()
    if not value:
        return None
    if value == (current or "").strip():
        return value
    if value not in allowed_models(provider):
        raise HTTPException(
            status_code=422,
            detail=f"unknown {provider} judge model; choose one of "
                   + ", ".join(allowed_models(provider)))
    return value


def _store_key(submitted: str | None, current: str | None,
               org_id, provider: str) -> str | None:
    """Encrypt a submitted BYOK key, clear it on "", keep it when omitted.

    The ciphertext is bound to ``(org_id, provider)`` so it can never be replayed
    into another tenant's row or the other provider slot. Fails closed (503) if the
    deployment cannot encrypt: a provider key is never written to the DB in plaintext.
    """
    if submitted is None:
        return current
    value = submitted.strip()
    if not value:
        return None
    try:
        return encrypt_secret(value, org_id, provider)
    except SecretsNotConfigured as exc:
        raise HTTPException(
            status_code=503,
            detail="provider key encryption is not configured on this deployment",
        ) from exc


def _get_or_create(org: Organization, db: Session) -> OrgPolicy:
    """Return the org's policy row, creating it with defaults if missing."""
    row = db.get(OrgPolicy, org.id)
    if row is None:
        row = OrgPolicy(org_id=org.id)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


@router.get("/v1/policies", response_model=PolicyConfig)
def get_policies(
    org: Organization = Depends(resolve_org),
    db: Session = Depends(get_db),
) -> PolicyConfig:
    """Return the org's current compliance policy settings.

    Provider keys are reported as booleans (gemini_key_set / openai_key_set) and
    never returned — not even to the org that owns them.
    """
    row = _get_or_create(org, db)
    return _to_config(row, org)


@router.put("/v1/policies", response_model=PolicyConfig)
def update_policies(
    body: PolicyConfig,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> PolicyConfig:
    """Update the org's compliance policy settings — ADMIN humans only.

    Hardened (Phase 4 #1): previously used resolve_org, which let a bare SDK
    Bearer key or any 'member' rewrite compliance policy. Writing the rules that
    govern the auditor is a privileged act, so it now requires an admin dashboard
    session. require_role already scoped RLS to the admin's org.

    Changes take effect for all interactions processed AFTER this call.
    The Gemini evaluator reads these flags on every grading job.
    """
    org = db.get(Organization, admin.org_id)
    row = _get_or_create(org, db)
    row.pii_detection = body.pii_detection
    row.prompt_injection = body.prompt_injection
    row.regulated_data_mode = body.regulated_data_mode
    row.max_token_threshold = body.max_token_threshold
    row.enforcement_mode = body.enforcement_mode
    # Absent means "leave it alone"; present-and-null means "clear it". Anything
    # else would let a client that has never heard of this field erase it.
    if "sdk_enforcement" in body.model_fields_set:
        row.sdk_enforcement = body.sdk_enforcement
    row.confidence_threshold = body.confidence_threshold
    row.notify_on_breach = body.notify_on_breach
    email = (body.notify_email or "").strip() or None
    if email is not None and ("@" not in email or "." not in email.split("@")[-1]):
        raise HTTPException(status_code=422, detail="notify_email must be a valid email")
    hook = (body.notify_webhook_url or "").strip() or None
    if hook is not None and not hook.startswith(("https://", "http://")):
        raise HTTPException(status_code=422, detail="notify_webhook_url must be an http(s) URL")
    row.notify_email = email
    row.notify_webhook_url = hook
    # ── Per-tenant AI Judge selection. Foxy's platform keys are a paid privilege:
    #    the tier check is SERVER-SIDE and authoritative (judge_routing re-checks
    #    it again at grading time, so a later downgrade also takes effect). ──
    if body.judge_key_mode == "platform" and not platform_keys_allowed(org.plan_tier):
        raise HTTPException(
            status_code=403,
            detail="Foxy's managed provider keys require the premium plan; "
                   "use your own Gemini/OpenAI key on this plan")
    row.judge_provider = body.judge_provider
    row.judge_key_mode = body.judge_key_mode
    # The model pick, on the same absent/present rule as sdk_enforcement above and
    # for the same reason — the desktop client (desktop/policy_data.py::put_body)
    # does not send these fields, and a desktop save must not erase a model the
    # web dashboard pinned.
    #
    # An unrecognised value is rejected rather than silently corrected: the
    # dashboard offers exactly judge_models_available, so a value outside it means
    # the caller meant something we cannot honour, and quietly grading with a
    # different model than the one the org asked for is the failure this phase
    # exists to fix.
    if "judge_gemini_model" in body.model_fields_set:
        row.gemini_judge_model = _checked_model("gemini", body.judge_gemini_model,
                                                row.gemini_judge_model)
    if "judge_openai_model" in body.model_fields_set:
        row.openai_judge_model = _checked_model("openai", body.judge_openai_model,
                                                row.openai_judge_model)
    row.gemini_key_enc = _store_key(body.gemini_api_key, row.gemini_key_enc, org.id, "gemini")
    row.openai_key_enc = _store_key(body.openai_api_key, row.openai_key_enc, org.id, "openai")
    account_audit.record_account_action(
        db, org_id=admin.org_id, actor_email=admin.email, action="policy.update",
        # Records THAT a key changed, never the key itself.
        detail={"enforcement_mode": body.enforcement_mode,
                "sdk_enforcement": row.sdk_enforcement,
                "notify_on_breach": body.notify_on_breach,
                "judge_provider": body.judge_provider,
                "judge_key_mode": body.judge_key_mode,
                # A model id is not a secret, so unlike the keys beside it this
                # records the value, not merely that it changed.
                "judge_gemini_model": row.gemini_judge_model,
                "judge_openai_model": row.openai_judge_model,
                "gemini_key_set": bool((row.gemini_key_enc or "").strip()),
                "openai_key_set": bool((row.openai_key_enc or "").strip())})
    db.commit()
    db.refresh(row)
    return _to_config(row, org)
