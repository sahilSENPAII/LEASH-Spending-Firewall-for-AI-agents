"""Agent settings from the repo-root .env. Agent-only names start with AGENT_.

`leash_connection()` is the one place that decides how the agent reaches Leash: stdio now (it starts
the backend's MCP server, like Claude Desktop does), HTTP after the Render deploy. Switching is one
line in .env: AGENT_LEASH_URL=https://<service>.onrender.com/mcp
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from mcp.client.stdio import get_default_environment
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_prefix="AGENT_", extra="ignore"
    )

    # Empty: start Leash locally over stdio. A URL: use Leash's HTTP MCP endpoint instead.
    leash_url: str = ""
    backend_dir: Path = REPO_ROOT / "backend"
    mandate_id: str = "m_demo"

    soundhub_url: str = "http://localhost:8101"
    megadeals_url: str = "http://localhost:8102"
    max_pages_per_store: int = 10
    # Optional Chromium binary; empty uses the one `playwright install chromium` downloaded.
    chromium_path: str = ""

    brain: Literal["scripted", "groq"] = "scripted"
    groq_model: str = "llama-3.3-70b-versatile"
    groq_api_key: str = Field("", validation_alias="GROQ_API_KEY")

    def stores(self) -> dict[str, str]:
        return {"soundhub": self.soundhub_url, "megadeals": self.megadeals_url}


def leash_connection(settings: AgentSettings) -> dict:
    """MCP connection for langchain-mcp-adapters' MultiServerMCPClient."""
    if settings.leash_url:
        return {"transport": "streamable_http", "url": settings.leash_url}
    return {
        "transport": "stdio",
        "command": "uv",
        "args": [
            "run", "--frozen", "--directory", str(settings.backend_dir),
            "python", "-m", "app.mcp_server",
        ],
        # MCP's minimal default environment, plus: no per-request INFO lines from the server.
        "env": {**get_default_environment(), "FASTMCP_LOG_LEVEL": "WARNING"},
    }


@lru_cache
def get_settings() -> AgentSettings:
    return AgentSettings()
