"""Backend configuration (pydantic-settings, loaded from backend/.env)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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
    # CORS — comma-separated list of allowed origins.
    # NEVER set to * in production; wildcard CORS contradicts our security USP.
    cors_origins: str = (
        "http://localhost:3000,"
        "http://localhost:5173,"
        "http://127.0.0.1:5173,"
        "http://127.0.0.1:3000"
    )

    def get_cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
