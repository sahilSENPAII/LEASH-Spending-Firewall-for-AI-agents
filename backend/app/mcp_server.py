"""Agent-facing MCP server (docs/CONTRACTS.md 5.5).

stdio, for Claude Desktop. Run from backend/: uv run python -m app.mcp_server
Streamable HTTP at /mcp on the FastAPI app (app.main), for the hosted server. It needs
`Authorization: Bearer <MCP_AUTH_TOKEN>` and is off while MCP_AUTH_TOKEN is empty.
"""

import hmac
import os
from datetime import UTC, datetime
from functools import lru_cache

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app import orchestrator
from app.config import get_settings
from app.db import get_sessionmaker
from app.mandates import list_mandates as stored_mandates
from app.models import Payment
from app.paypal import PayPalClient
from app.read_api import _decided, _time, payment_view
from app.times import as_utc

# FastMCP 1.x ignores FASTMCP_LOG_LEVEL when built in code; honour it so a caller (the demo agent)
# can drop the per-request INFO lines. Errors are logged either way.
mcp = FastMCP(
    "leash",
    log_level=os.environ.get("FASTMCP_LOG_LEVEL", "INFO"),
    # HTTP only. Stateless JSON: no session to lose when the hosted service restarts.
    stateless_http=True,
    json_response=True,
    # The SDK's localhost-only Host check would reject the hosted name; the bearer token is the
    # guard (a rebinding page cannot know it).
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


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


@mcp.tool()
async def list_mandates() -> dict:
    """The mandates this agent can pay under, with what is left of each. Money is a string.

    Only active mandates are listed: not revoked and not expired.

    Returns {"mandates": [...]}: one JSON object, since FastMCP splits a bare list into one text
    block per item.
    """
    try:
        async with get_sessionmaker()() as session:
            records = await stored_mandates(session)
    except Exception as exc:
        orchestrator.log.exception("list_mandates failed")
        raise ToolError("Leash could not read the mandates.") from exc
    now = datetime.now(UTC)
    mandates = [
        {
            "mandate_id": r.terms.mandate_id,
            "intent": r.terms.intent,
            "category": r.terms.category,
            "currency": "USD",
            "max_payment_amount": f"{r.terms.max_payment_amount:.2f}",
            "remaining_budget": f"{r.terms.total_budget - r.budget_used:.2f}",
            "remaining_count": r.terms.max_payment_count - r.payment_count,
            "expires_at": _time(r.terms.expires_at),
            "revoked": False,
        }
        for r in records
        if r.revoked_at is None and as_utc(r.terms.expires_at) > now
    ]
    return {"mandates": mandates}


@mcp.tool()
async def get_payment_status(payment_id: str) -> dict:
    """The current decision and status of a payment returned by request_payment."""
    try:
        async with get_sessionmaker()() as session:
            payment = await session.get(Payment, payment_id)
            decided = await _decided(session, [payment_id]) if payment else {}
    except Exception as exc:
        orchestrator.log.exception("get_payment_status failed")
        raise ToolError("Leash could not read the payment.") from exc
    if payment is None:
        raise ToolError(f"No payment with id {payment_id!r}.")
    # approval_url stays null here: only the token's hash is stored.
    return payment_view(payment, decided.get(payment_id)) | {
        "approval_expires_at": _time(payment.approval_expires_at)
    }


class RequireBearerToken:
    """ASGI wrapper for /mcp: 401 without the right bearer token, 503 while none is set."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        token = get_settings().mcp_auth_token
        sent = Headers(scope=scope).get("authorization", "")
        if not token:
            detail, status = "MCP over HTTP is off: MCP_AUTH_TOKEN is not set.", 503
        elif not hmac.compare_digest(sent.encode(), f"Bearer {token}".encode()):
            detail, status = "Missing or wrong bearer token.", 401
        else:
            await self.app(scope, receive, send)
            return
        headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
        await JSONResponse({"detail": detail}, status_code=status, headers=headers)(
            scope, receive, send
        )


def http_app() -> ASGIApp:
    """The tools over streamable HTTP at /mcp, behind the bearer token. app.main routes /mcp here
    and runs `mcp.session_manager` in its lifespan."""
    return RequireBearerToken(mcp.streamable_http_app())


if __name__ == "__main__":
    mcp.run()
