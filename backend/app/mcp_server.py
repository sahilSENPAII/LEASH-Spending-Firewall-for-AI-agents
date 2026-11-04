"""Agent-facing MCP server (stdio). Run from backend/: uv run python -m app.mcp_server"""

import os
from functools import lru_cache

from mcp.server.fastmcp import FastMCP

from app import orchestrator
from app.config import get_settings
from app.db import get_sessionmaker
from app.paypal import PayPalClient

# FastMCP 1.x ignores FASTMCP_LOG_LEVEL when built in code; honour it so a caller (the demo agent)
# can drop the per-request INFO lines. Errors are logged either way.
mcp = FastMCP("leash", log_level=os.environ.get("FASTMCP_LOG_LEVEL", "INFO"))


@lru_cache
def get_paypal() -> PayPalClient:
    s = get_settings()
    return PayPalClient(s.paypal_client_id, s.paypal_client_secret, s.paypal_base_url)


@mcp.tool()
async def request_payment(mandate_id: str, merchant_id: str, items: list[dict]) -> dict:
    """Ask Leash to pay a store under a mandate for `items`, e.g. [{"sku": "sh-sony-xm5",
    "quantity": 1}]. Send no price or amount: Leash prices each sku from its own catalog.

    Leash checks the cart, the mandate and the payment's risk, then pays only on ALLOW. STEP_UP
    means a person must approve first. Returns the decision with its reasons.
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
        items=items,
    )


if __name__ == "__main__":
    mcp.run()
