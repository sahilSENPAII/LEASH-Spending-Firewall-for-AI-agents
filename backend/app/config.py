"""Settings loaded from the repo-root .env (never committed)."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
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
    # Base64 Ed25519 private key; the server signs mandates for the demo user. See app/mandates.py.
    mandate_signing_key: str = ""
    velocity_max_payments: int = 3
    velocity_window_seconds: int = 600
    # Anomaly score cut-offs after a policy ALLOW (CONTRACTS 5.2); final values agreed Nov 8.
    anomaly_step_up_threshold: float = Field(default=0.5, ge=0, le=1)
    anomaly_block_threshold: float = Field(default=0.9, ge=0, le=1)
    # Product catalog file, relative to the repo root; empty means stores/catalog.json.
    catalog_path: str = ""
    # Bearer token for MCP over HTTP (/mcp). Empty turns /mcp off.
    mcp_auth_token: str = ""
    # Comma-separated origins allowed to call the API from a browser (the audit console).
    cors_origins: str = "http://localhost:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()
