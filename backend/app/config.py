"""Settings loaded from the repo-root .env (never committed)."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_base_url: str = "https://api-m.sandbox.paypal.com"
    # Saved buyer wallet (Vault v3 payment token) for the single demo user. M2 moves this to the DB.
    paypal_vault_id: str = ""
    platform_merchant_email: str = ""

    database_url: str = ""
    groq_api_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
