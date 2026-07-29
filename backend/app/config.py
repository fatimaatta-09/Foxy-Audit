"""Backend configuration (pydantic-settings, loaded from backend/.env)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Deployment environment. "prod" makes insecure secret defaults fail fast at
    # startup (so a real deploy can never silently run on the dev session secret).
    foxy_env: str = "dev"
    database_url: str = "postgresql+psycopg://foxy:foxy@localhost:5432/foxy"
    # Confined, NOLOGIN/NOBYPASSRLS role the app assumes (via `SET LOCAL ROLE`) for
    # every org-scoped transaction, so the existing FORCE RLS policies actually
    # filter rows instead of being bypassed by the superuser connection (5B.2).
    # Empty string disables the role switch (e.g. a DB where the role is absent).
    db_app_role: str = "foxy_app"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"   # current free-tier model; retired 1.5-pro rejects AQ-prefixed keys
    gemini_timeout: float = 12.0
    # Optional OpenAI Responses API judge. Blank key disables this provider.
    # Use gpt-5.6 for the production API or chat-latest when the ChatGPT alias
    # is specifically required by the deployment.
    openai_api_key: str = ""
    openai_model: str = "gpt-5.6"
    openai_timeout: float = 12.0
    # Fernet key (urlsafe-base64, 32 bytes) that encrypts customer-supplied BYOK
    # provider keys at rest. Empty = BYOK unavailable on this deployment; every
    # store/read of a tenant key then fails closed rather than touching plaintext.
    # SecretStr so the master KEK is masked in repr()/model_dump() and never
    # captured by an error reporter that dumps Settings. Unwrap only at point of
    # use (crypto_secrets._load_keys via .get_secret_value()).
    provider_key_encryption_key: SecretStr = SecretStr("")
    # When the judge is unreachable: False = fail-open (write row, no breach),
    # True = fail-closed (flag for human review). The chain row is always written.
    gemini_fail_closed: bool = False
    # Stripe billing integration
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    # Stripe Checkout price IDs per paid plan — self-serve onboarding (6A).
    stripe_price_pro: str = ""
    stripe_price_max: str = ""
    stripe_price_companion: str = ""
    stripe_price_guardian: str = ""    # one-time (lifetime) — Checkout mode=payment
    # Google reCAPTCHA v2 — server-side verification of the demo form. Empty = skip
    # (the frontend widget still gates UX; set RECAPTCHA_SECRET_KEY in the server env
    # for real bot protection — the site key is public and lives in book-a-demo.html).
    recaptcha_secret_key: str = ""
    # Google SSO — the OAuth 2.0 Web Client ID (public). Empty = "Sign in with
    # Google" disabled (POST /v1/auth/google returns 503). No client secret needed:
    # we only verify Google ID tokens, whose audience must equal this client id.
    google_oauth_client_id: str = ""
    # Human session auth (dashboard login) — signs the CUSTOMER session cookie.
    # MUST be overridden with a strong random value in production (SESSION_SECRET).
    session_secret: str = "dev-insecure-session-secret-change-me"
    session_max_age: int = 60 * 60 * 12          # default (non-remember) session lifetime (12h)
    # Cookie max-age ceiling. remember-me sessions live this long (30d); the cookie is minted at this
    # ceiling and the per-session `user_sessions.expires_at` is the authoritative gate (12h vs 30d).
    session_remember_max_age: int = 60 * 60 * 24 * 30   # 30 days
    # Platform-STAFF session (admin site "site 3") — a SEPARATE cookie signed with a
    # DISTINCT secret so a customer cookie can never satisfy a staff route, and
    # vice-versa. Required (and must differ from session_secret) in prod.
    staff_session_secret: str = "dev-insecure-staff-session-secret-change-me"
    staff_session_max_age: int = 60 * 60 * 2     # staff cookie lifetime (2h — shorter)
    # Scope the staff cookie to the admin host ONLY (e.g. "admin.foxyaudit.com") so it
    # is NEVER sent to the customer app origin. Empty = host-only (dev/localhost).
    staff_cookie_domain: str = ""
    # Server-side pepper mixed into the API-key HMAC (Phase 3 A2). Required in prod.
    # Also used to HMAC-hash IPs / user-agents in traffic_events (never stored raw).
    api_key_pepper: str = ""
    # Time-limited, no-card evaluation access. Keep JUDGE_OFFER_CODE only in the
    # deployment secret store; a blank code disables redemption entirely.
    judge_offer_code: str = ""
    judge_offer_id: str = "judge-evaluation"
    judge_offer_credits: int = 2000
    judge_offer_days: int = 30
    judge_offer_max_redemptions: int = 25
    # Public-chain anchoring (Phase 3 A1) — periodically publish each org's chain
    # head (latest audit_logs.chain_hash) to a public chain so tampering is
    # externally detectable, not just internally recomputable. Provider is
    # pluggable: 'stub' (no external chain, for dev/tests) or 'evm' (web3 ->
    # AnchorRegistry on Sepolia/any EVM).
    anchor_enabled: bool = False
    anchor_provider: str = "stub"              # stub | evm
    anchor_interval_seconds: int = 3600
    # Per-tier anchor cadence (6E) — the automatic worker sweep publishes an org's
    # chain head at most once per its plan tier's cadence (seconds). Manual "anchor
    # now" is NOT gated. Unknown/unset tiers use anchor_cadence_default. NOTE:
    # bounded by anchor_interval_seconds (the sweep frequency) — a sub-hour cadence
    # needs a shorter sweep too.
    anchor_cadence_free: int = 86400          # 24h
    anchor_cadence_pro: int = 21600           # 6h
    anchor_cadence_max: int = 3600            # 1h
    anchor_cadence_enterprise: int = 3600     # 1h
    anchor_cadence_default: int = 86400       # fallback for unknown/unset tiers
    # Product entitlements. One credit is one captured model call, not a token.
    # A zero quota means unlimited under a contract, not an unknown plan.
    trial_days: int = 7
    # P3 §4 · signup payment gate. OFF by default, and it must stay that way until
    # the owner decides otherwise: every existing org reads as "no card on file",
    # so turning this on locks every current customer out of their dashboard until
    # they complete a card setup. That is a business decision, not a deploy.
    require_card_on_file: bool = False
    # ── The grandfather clause (P3 §4) ───────────────────────────────────────
    # Organisations created BEFORE this instant are permanently exempt from the
    # card gate. UNSET (the default) exempts every organisation, whenever it was
    # created — the fail-safe direction, and the thing that makes
    # `require_card_on_file` flippable at all. Without an exemption, enabling the
    # gate locks out every customer the product already has, all at once, for a
    # card none of them were ever asked for: `card_on_file` is false on every row
    # written before that column existed.
    #
    # Deliberately NOT a hardcoded ship date. A date baked in here is wrong the
    # moment it passes — an org created an hour after midnight on that date would
    # be gated, which is precisely the lockout this exists to prevent. Absence of
    # configuration means "no cutoff has been chosen", and choosing one is the
    # deliberate act of narrowing the exemption.
    #
    # Turning the gate on therefore means TWO decisions: set this to the moment
    # you enable it, then set require_card_on_file. One without the other is
    # either a no-op or an outage.
    card_gate_grandfather_before: str = ""
    # Days of warning before a billing change. The owner asked for "3-4 days before
    # anything changes"; 4 leaves a working day of slack.
    billing_change_notice_days: int = 4
    monthly_quota_free: int = 500
    monthly_quota_companion: int = 25000       # legacy alias for pro
    monthly_quota_pro: int = 25000
    monthly_quota_max: int = 250000
    monthly_quota_guardian: int = 0           # 0 = unlimited (lifetime tier)
    monthly_quota_premium: int = 0            # 0 = unlimited by contract
    seat_limit_free: int = 2
    seat_limit_companion: int = 5
    seat_limit_pro: int = 5
    seat_limit_max: int = 20
    seat_limit_guardian: int = 20
    seat_limit_premium: int = 0               # 0 = negotiated/unlimited
    api_key_limit_free: int = 1
    api_key_limit_companion: int = 5
    api_key_limit_pro: int = 5
    api_key_limit_max: int = 20
    api_key_limit_guardian: int = 20
    api_key_limit_premium: int = 0            # 0 = negotiated/unlimited
    anchor_evm_rpc_url: str = ""
    anchor_evm_chain: str = "sepolia"
    anchor_evm_private_key: str = ""           # funded testnet key; required for 'evm'
    anchor_evm_contract: str = ""              # deployed AnchorRegistry address
    # Anchoring safety rails (7C) — keep a live EVM anchor from silently failing:
    #  * refuse to submit when the funded wallet is below this floor (wei); 0 = off.
    #  * alert (log + email alert_email) when the newest confirmed anchor is older
    #    than this many seconds (0 = off), or when an org's latest anchor is 'failed',
    #    at most once per anchor_alert_cooldown seconds.
    anchor_wallet_min_balance_wei: int = 0
    anchor_stale_alert_seconds: int = 0
    anchor_alert_cooldown: int = 3600
    # Per-org usage rollup + traffic-partition maintenance (worker thread, app/usage.py)
    usage_rollup_interval: int = 300
    # Per-user notification emails (worker thread, app/user_notifications.py):
    # breach alerts drained off a queue on the short tick, weekly digest +
    # key-rotation reminders swept on the long one (both date-gated and marker-
    # deduped, so extra passes are no-ops). Set enabled=false to mute all
    # per-user email without touching per-user preferences.
    user_notifications_enabled: bool = True
    user_notifications_interval: int = 3600
    breach_alert_drain_interval: int = 5
    # Durable grading queue (Postgres outbox poller — app/worker.py)
    grading_poll_interval: float = 2.0
    grading_batch_size: int = 16
    grading_max_attempts: int = 5
    grading_stuck_seconds: int = 300
    # Dead-letter alerting (5E.1): when >= threshold rows sit in
    # grading_status='failed', the worker logs a WARNING and emails alert_email
    # (empty = log only), at most once per cooldown window (seconds).
    grading_failure_alert_threshold: int = 1
    grading_failure_alert_cooldown: int = 3600
    alert_email: str = ""
    # Grading circuit-breaker (5E.2): after this many consecutive all-failed
    # batches, stop grading for `cooldown` seconds; backoff is capped at max.
    grading_breaker_threshold: int = 3
    grading_breaker_cooldown: float = 60.0
    grading_max_backoff: float = 60.0
    # In/out traffic tracking (Phase 4 #1). The middleware writes one row per request
    # OFF the hot path; disable for perf tests or to pause capture. Raw partitions
    # older than the retention window are dropped by the worker.
    traffic_tracking_enabled: bool = True
    traffic_retention_days: int = 90
    # admin_actions (staff audit trail) retention — purged by the worker's
    # maintenance pass (5D.5). Longer than traffic since it's compliance-relevant.
    admin_actions_retention_days: int = 365
    # CORS — comma-separated list of allowed origins.
    # NEVER set to * in production; wildcard CORS contradicts our security USP.
    cors_origins: str = (
        "http://localhost:3000,"
        "http://localhost:5173,"
        "http://127.0.0.1:5173,"
        "http://127.0.0.1:3000"
    )
    # Admin site ("site 3") origins — a SEPARATE allow-list from the customer CORS
    # so the admin origin is never accepted on the customer /v1 API and vice-versa.
    admin_cors_origins: str = (
        "http://localhost:5174,"
        "http://127.0.0.1:5174"
    )
    # Optional IP allow-list for the admin site (comma-separated). Empty = allow all
    # (dev). In prod, restrict admin.foxyaudit.com to office/VPN ranges.
    admin_ip_allowlist: str = ""
    # Max request body size (bytes); larger POSTs are rejected with 413 (anti-DoS).
    max_request_bytes: int = 2 * 1024 * 1024
    # Transactional email via Brevo (staff MFA codes; password resets/invites in 5D).
    brevo_api_key: str = ""
    brevo_sender_email: str = "no-reply@foxyaudit.tech"
    brevo_sender_name: str = "Foxy Audit"
    # Public base URLs where the customer / staff login pages load — used to build
    # password-reset links (5D). The reset page detects ?reset_token=… on these.
    dashboard_url: str = "https://app.foxyaudit.tech/dashboard"
    admin_url: str = "https://admin.foxyaudit.tech/admin/"

    def get_cors_origins(self) -> list[str]:
        # A literal "*" is never allowed — wildcard CORS contradicts the security USP.
        return [o.strip() for o in self.cors_origins.split(",") if o.strip() and o.strip() != "*"]

    def get_admin_cors_origins(self) -> list[str]:
        return [o.strip() for o in self.admin_cors_origins.split(",")
                if o.strip() and o.strip() != "*"]

    def get_admin_ip_allowlist(self) -> list[str]:
        return [o.strip() for o in self.admin_ip_allowlist.split(",") if o.strip()]

    def anchor_cadence_for(self, plan_tier: str | None) -> int:
        """Seconds between automatic anchors for a plan tier (6E); default fallback."""
        return {
            "free": self.anchor_cadence_free,
            "pro": self.anchor_cadence_pro,
            "max": self.anchor_cadence_max,
            "companion": self.anchor_cadence_pro,
            "guardian": self.anchor_cadence_enterprise,
            "premium": self.anchor_cadence_enterprise,
            "enterprise": self.anchor_cadence_enterprise,
        }.get((plan_tier or "").strip().lower(), self.anchor_cadence_default)

    def quota_for(self, plan_tier: str | None) -> int | None:
        """Monthly captured-event credits. None means unlimited by contract."""
        q = {
            "free": self.monthly_quota_free,
            "companion": self.monthly_quota_companion,
            "pro": self.monthly_quota_pro,
            "max": self.monthly_quota_max,
            "guardian": self.monthly_quota_guardian,
            "premium": self.monthly_quota_premium,
            "enterprise": self.monthly_quota_premium,
        }.get((plan_tier or "").strip().lower(), 0)
        return None if q <= 0 else q

    @staticmethod
    def canonical_plan(plan_tier: str | None) -> str:
        """Normalize public plan names while accepting names from old checkouts."""
        return {
            "companion": "pro",
            "guardian": "premium",
            "enterprise": "premium",
        }.get((plan_tier or "free").strip().lower(),
               (plan_tier or "free").strip().lower())

    def seat_limit_for(self, plan_tier: str | None) -> int | None:
        """Maximum active human dashboard users. None means contract-defined."""
        key = (plan_tier or "").strip().lower()
        limit = {
            "free": self.seat_limit_free,
            "companion": self.seat_limit_companion,
            "pro": self.seat_limit_pro,
            "max": self.seat_limit_max,
            "guardian": self.seat_limit_guardian,
            "premium": self.seat_limit_premium,
            "enterprise": self.seat_limit_premium,
        }.get(key, 0)
        return None if limit <= 0 else limit

    def api_key_limit_for(self, plan_tier: str | None) -> int | None:
        """Maximum active machine keys. None means contract-defined."""
        key = (plan_tier or "").strip().lower()
        limit = {
            "free": self.api_key_limit_free,
            "companion": self.api_key_limit_companion,
            "pro": self.api_key_limit_pro,
            "max": self.api_key_limit_max,
            "guardian": self.api_key_limit_guardian,
            "premium": self.api_key_limit_premium,
            "enterprise": self.api_key_limit_premium,
        }.get(key, 0)
        return None if limit <= 0 else limit

    @property
    def is_prod(self) -> bool:
        return self.foxy_env.lower() == "prod"

    @model_validator(mode="after")
    def _require_secure_prod(self):
        """Fail fast if a prod deploy is left on insecure secret defaults."""
        if self.foxy_env.lower() == "prod":
            missing = []
            if self.session_secret in ("", "dev-insecure-session-secret-change-me"):
                missing.append("SESSION_SECRET")
            if self.staff_session_secret in ("", "dev-insecure-staff-session-secret-change-me"):
                missing.append("STAFF_SESSION_SECRET")
            if not self.api_key_pepper:
                missing.append("API_KEY_PEPPER")
            # Live EVM anchoring must not start prod without its funded signing key —
            # fail fast here instead of lazily on the first anchor submit (anchor.py).
            if (self.anchor_enabled and self.anchor_provider.lower() == "evm"
                    and not self.anchor_evm_private_key):
                missing.append("ANCHOR_EVM_PRIVATE_KEY")
            if missing:
                raise ValueError(
                    "FOXY_ENV=prod requires strong values for: " + ", ".join(missing))
            # A shared staff/customer signing secret would let one cookie be forged
            # into the other channel — must be impossible in prod.
            if self.staff_session_secret == self.session_secret:
                raise ValueError(
                    "FOXY_ENV=prod requires STAFF_SESSION_SECRET to differ from SESSION_SECRET")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
