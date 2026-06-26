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


@lru_cache
def get_settings() -> Settings:
    return Settings()
