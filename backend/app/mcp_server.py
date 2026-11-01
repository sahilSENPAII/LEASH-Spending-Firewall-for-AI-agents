"""Agent-facing MCP server (stdio). Run from backend/: PYTHONPATH=.. uv run python -m app.mcp_server

PYTHONPATH=.. makes `backend.policy` importable next to `app`, as pytest does via pyproject.
"""

from functools import lru_cache

from mcp.server.fastmcp import FastMCP

from app import orchestrator
from app.config import get_settings
from app.db import get_sessionmaker
from app.paypal import PayPalClient

mcp = FastMCP("leash")


@lru_cache
def get_paypal() -> PayPalClient:
    s = get_settings()
    return PayPalClient(s.paypal_client_id, s.paypal_client_secret, s.paypal_base_url)


@mcp.tool()
async def request_payment(mandate_id: str, merchant_id: str, amount: str, items: str) -> dict:
    """Ask Leash to pay a store under a mandate. `amount` is a USD decimal string, e.g. "179.00".

    Leash checks the mandate, then pays only on ALLOW. Returns the decision with its reasons.
    """
    try:
        sessions = get_sessionmaker()
        paypal = get_paypal()
        vault_id = get_settings().paypal_vault_id
    except Exception:
        orchestrator.log.exception("request_payment setup failed")
        return orchestrator.internal_error()
    return await orchestrator.request_payment(
        sessions,
        paypal,
        vault_id,
        mandate_id=mandate_id,
        merchant_id=merchant_id,
        amount=amount,
        items=items,
    )


if __name__ == "__main__":
    mcp.run()
