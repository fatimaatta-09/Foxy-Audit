"""Per-tenant AI Judge routing: which judge grades an org's events, on whose key.

Two independent choices per org, held on the LIVE OrgPolicy row (never on the
chain snapshot — routing is an operational/billing decision, not evidence):

  judge_provider  gemini | openai | both
  judge_key_mode  own (BYOK, the tenant's encrypted key) | platform (Foxy's keys)

``platform`` is a paid privilege. The allowed set is ONE constant —
:data:`PLATFORM_KEY_TIERS` — so changing who gets it is a one-line change.

The tier is re-checked HERE, at grading time, against the org's live plan_tier.
A premium org that selects platform keys and later downgrades therefore stops
spending Foxy's key immediately: it falls back to its own key, and with no own
key the provider is simply skipped (an honest ``evaluator_unavailable``), never
silently billed to the platform.

Decrypted keys exist only inside the returned :class:`JudgeRouting`, for the
duration of one grading call. They are never logged, persisted, or serialised.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from .config import get_settings
from .crypto_secrets import SecretDecryptionError, SecretsNotConfigured, decrypt_secret
from .models import OrgPolicy, Organization

log = logging.getLogger("foxy.judge_routing")

# The ONLY place the privileged tier set is defined. Orgs on any other tier
# (pro, free, trial, max, NULL, anything unknown) are BYOK-only.
PLATFORM_KEY_TIERS = {"premium"}

PROVIDERS = ("gemini", "openai", "both")
KEY_MODES = ("own", "platform")

DEFAULT_PROVIDER = "gemini"
DEFAULT_KEY_MODE = "own"

# Which model versions an org may pin, per provider (P6f). One constant, in the
# same spirit as PLATFORM_KEY_TIERS above: adding a model is a one-line change.
#
# The DEPLOYMENT DEFAULT IS NOT LISTED HERE and is not required to be. It comes
# from settings.gemini_model / settings.openai_model and is always allowed, so a
# deployment can move to a model this list has not heard of without anyone having
# to edit it first. That ordering matters — the operator's setting outranks a
# constant in source.
JUDGE_MODELS = {
    "gemini": ("gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"),
    "openai": ("gpt-5.6", "gpt-5.6-mini", "chat-latest"),
}


def allowed_models(provider: str) -> tuple[str, ...]:
    """The models this deployment will accept for a provider, default first.

    The default is prepended rather than assumed present, and de-duplicated, so
    the list is correct whether or not the running default happens to appear in
    JUDGE_MODELS."""
    settings = get_settings()
    default = settings.gemini_model if provider == "gemini" else settings.openai_model
    out = [default] if default else []
    for m in JUDGE_MODELS.get(provider, ()):
        if m not in out:
            out.append(m)
    return tuple(out)


def resolve_model(provider: str, stored: str | None) -> str:
    """The model id to grade with: the org's pin if we still honour it, else the
    deployment default.

    AN UNKNOWN STORED VALUE FALLS BACK; IT DOES NOT FAIL THE GRADE. A model that
    was valid when an admin chose it can be retired by the provider months later,
    and the org finds out through a grading outage it cannot diagnose. The same
    posture the BYOK path takes when a key will not decrypt: record the problem,
    carry on with something that works."""
    settings = get_settings()
    default = settings.gemini_model if provider == "gemini" else settings.openai_model
    if stored and stored in allowed_models(provider):
        return stored
    if stored:
        log.warning("org pinned an unknown %s judge model %r; using the "
                    "deployment default %r", provider, stored, default)
    return default


def platform_keys_allowed(plan_tier: str | None) -> bool:
    """True when this plan may grade on Foxy's platform provider keys."""
    return (plan_tier or "") in PLATFORM_KEY_TIERS


@dataclass(frozen=True)
class JudgeRouting:
    """One org's resolved judge configuration for a single grading call.

    ``gemini_key`` / ``openai_key`` are plaintext BYOK keys held in memory only.
    ``None`` means "no tenant key for this provider": in platform mode that is
    correct (the provider falls back to settings.*), and in own mode it means the
    provider must be SKIPPED rather than charged to the platform — which is what
    :meth:`can_call` encodes.
    """

    provider: str = DEFAULT_PROVIDER
    key_mode: str = DEFAULT_KEY_MODE
    gemini_key: str | None = None
    openai_key: str | None = None
    # The RESOLVED model id per provider — an org pin if it survived validation,
    # otherwise the deployment default. Never None once resolve_judge_routing has
    # run. Unlike the keys above these are safe to record: a model id identifies
    # a public product, not a credential, and the verdict carries it so the ledger
    # can say which model graded each event.
    gemini_model: str | None = None
    openai_model: str | None = None
    # Why a chosen provider had to be skipped (e.g. "no_byok_key"), for the
    # evaluator_unavailable reason string. Never contains key material.
    problems: dict[str, str] = field(default_factory=dict)

    @property
    def uses_gemini(self) -> bool:
        return self.provider in ("gemini", "both")

    @property
    def uses_openai(self) -> bool:
        return self.provider in ("openai", "both")

    def key_for(self, provider: str) -> str | None:
        return self.gemini_key if provider == "gemini" else self.openai_key

    def model_for(self, provider: str) -> str | None:
        return self.gemini_model if provider == "gemini" else self.openai_model

    def can_call(self, provider: str) -> bool:
        """False when this provider is chosen but has no usable key in own mode."""
        if self.key_mode == "platform":
            return True
        return bool(self.key_for(provider))


def _decrypt_optional(ciphertext: str | None, org_id, provider: str,
                      problems: dict[str, str]) -> str | None:
    """Decrypt a stored BYOK key, or record why we could not. Never raises."""
    if not (ciphertext or "").strip():
        problems[provider] = "no_byok_key"
        return None
    try:
        plaintext = decrypt_secret(ciphertext, org_id, provider).strip()
    except SecretsNotConfigured:
        # Deployment has no PROVIDER_KEY_ENCRYPTION_KEY — fail closed.
        log.warning("BYOK key for org %s unusable: encryption not configured", org_id)
        problems[provider] = "byok_encryption_unavailable"
        return None
    except SecretDecryptionError:
        log.warning("BYOK key for org %s could not be decrypted", org_id)
        problems[provider] = "byok_key_undecryptable"
        return None
    if not plaintext:
        problems[provider] = "no_byok_key"
        return None
    return plaintext


def resolve_judge_routing(db: Session, org_id) -> JudgeRouting:
    """Read the org's LIVE routing choice and materialise the keys for one call.

    Reads the live OrgPolicy row deliberately: the policy snapshot bound to an
    event governs HOW the event is judged, but never WHO judges it or whose key
    pays — a key must never touch the chain.
    """
    oid = org_id if isinstance(org_id, uuid.UUID) else uuid.UUID(str(org_id))
    policy = db.get(OrgPolicy, oid)
    if policy is None:
        return JudgeRouting(gemini_model=resolve_model("gemini", None),
                            openai_model=resolve_model("openai", None))

    provider = policy.judge_provider if policy.judge_provider in PROVIDERS else DEFAULT_PROVIDER
    key_mode = policy.judge_key_mode if policy.judge_key_mode in KEY_MODES else DEFAULT_KEY_MODE

    if key_mode == "platform":
        org = db.get(Organization, oid)
        if not platform_keys_allowed(org.plan_tier if org else None):
            # Server-side tier gate, re-checked at grading time (e.g. after a
            # downgrade): fall back to BYOK rather than spending Foxy's key.
            key_mode = "own"

    # Resolved for BOTH key modes: whose key pays and which version runs are
    # independent choices, and a platform-key org still gets to pick a model.
    gemini_model = resolve_model("gemini", policy.gemini_judge_model)
    openai_model = resolve_model("openai", policy.openai_judge_model)

    if key_mode == "platform":
        return JudgeRouting(provider=provider, key_mode=key_mode,
                            gemini_model=gemini_model, openai_model=openai_model)

    problems: dict[str, str] = {}
    gemini_key = (_decrypt_optional(policy.gemini_key_enc, oid, "gemini", problems)
                  if provider in ("gemini", "both") else None)
    openai_key = (_decrypt_optional(policy.openai_key_enc, oid, "openai", problems)
                  if provider in ("openai", "both") else None)
    return JudgeRouting(provider=provider, key_mode=key_mode,
                        gemini_key=gemini_key, openai_key=openai_key,
                        gemini_model=gemini_model, openai_model=openai_model,
                        problems=problems)
