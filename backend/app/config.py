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
    gemini_api_key: str = ""
    gemini_model: str = "gemini-1.5-pro"
    gemini_timeout: float = 12.0
    # When the judge is unreachable: False = fail-open (write row, no breach),
    # True = fail-closed (flag for human review). The chain row is always written.
    gemini_fail_closed: bool = False
    # Stripe billing integration
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    # Human session auth (dashboard login) — signs the session cookie.
    # MUST be overridden with a strong random value in production (SESSION_SECRET).
    session_secret: str = "dev-insecure-session-secret-change-me"
    # Server-side pepper mixed into the API-key HMAC (Phase 3 A2). Required in prod.
    api_key_pepper: str = ""
    # Public-chain anchoring (Phase 3 A1) — periodically publish each org's chain
    # head (latest audit_logs.chain_hash) to a public chain so tampering is
    # externally detectable, not just internally recomputable. Provider is
    # pluggable: 'stub' (no external chain, for dev/tests), 'evm' (web3 ->
    # AnchorRegistry on Sepolia/any EVM), or 'opentimestamps' (Bitcoin).
    anchor_enabled: bool = False
    anchor_provider: str = "stub"              # stub | evm | opentimestamps
    anchor_interval_seconds: int = 3600
    anchor_evm_rpc_url: str = ""
    anchor_evm_chain: str = "sepolia"
    anchor_evm_private_key: str = ""           # funded testnet key; required for 'evm'
    anchor_evm_contract: str = ""              # deployed AnchorRegistry address
    # Durable grading queue (Postgres outbox poller — app/worker.py)
    grading_poll_interval: float = 2.0
    grading_batch_size: int = 16
    grading_max_attempts: int = 5
    grading_stuck_seconds: int = 300
    # CORS — comma-separated list of allowed origins.
    # NEVER set to * in production; wildcard CORS contradicts our security USP.
    cors_origins: str = (
        "http://localhost:3000,"
        "http://localhost:5173,"
        "http://127.0.0.1:5173,"
        "http://127.0.0.1:3000"
    )

    def get_cors_origins(self) -> list[str]:
        # A literal "*" is never allowed — wildcard CORS contradicts the security USP.
        return [o.strip() for o in self.cors_origins.split(",") if o.strip() and o.strip() != "*"]

    @model_validator(mode="after")
    def _require_secure_prod(self):
        """Fail fast if a prod deploy is left on insecure secret defaults."""
        if self.foxy_env.lower() == "prod":
            missing = []
            if self.session_secret in ("", "dev-insecure-session-secret-change-me"):
                missing.append("SESSION_SECRET")
            if not self.api_key_pepper:
                missing.append("API_KEY_PEPPER")
            if missing:
                raise ValueError(
                    "FOXY_ENV=prod requires strong values for: " + ", ".join(missing))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
