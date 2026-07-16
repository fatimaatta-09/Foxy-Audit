"""Backend configuration (pydantic-settings, loaded from backend/.env)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
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
    # When the judge is unreachable: False = fail-open (write row, no breach),
    # True = fail-closed (flag for human review). The chain row is always written.
    gemini_fail_closed: bool = False
    # Stripe billing integration
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    # Stripe Checkout price IDs per paid plan — self-serve onboarding (6A).
    stripe_price_pro: str = ""
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
    session_max_age: int = 60 * 60 * 12          # customer cookie lifetime (12h)
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
    anchor_cadence_enterprise: int = 3600     # 1h
    anchor_cadence_default: int = 86400       # fallback for unknown/unset tiers
    # Per-tier MONTHLY LOG QUOTA (Phase 3, SOFT): surfaced via /v1/usage + warned on in
    # the dashboard, but NEVER blocks ingestion. 0 = unlimited. Assigned to
    # organizations.monthly_log_quota at provisioning.
    monthly_quota_free: int = 1000            # matches the sales page (1,000 calls / mo)
    monthly_quota_companion: int = 50000
    monthly_quota_pro: int = 50000
    monthly_quota_guardian: int = 0           # 0 = unlimited (lifetime tier)
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
            "enterprise": self.anchor_cadence_enterprise,
        }.get((plan_tier or "").strip().lower(), self.anchor_cadence_default)

    def quota_for(self, plan_tier: str | None) -> int | None:
        """Monthly log quota for a plan tier (Phase 3, soft). None = unlimited."""
        q = {
            "free": self.monthly_quota_free,
            "companion": self.monthly_quota_companion,
            "pro": self.monthly_quota_pro,
            "guardian": self.monthly_quota_guardian,
        }.get((plan_tier or "").strip().lower(), 0)
        return None if q <= 0 else q

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
