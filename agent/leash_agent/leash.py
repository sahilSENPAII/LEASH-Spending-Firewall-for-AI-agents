"""Paying through Leash's MCP tool `request_payment`. The agent never sees PayPal.

The agreed tool shape is request_payment(mandate_id, merchant_id, items=[{sku, quantity}]): the
agent sends no price, Leash prices each sku from its own catalog. Until that lands on main, the live
tool still takes (mandate_id, merchant_id, amount, items: str); `payment_arguments` reads the tool's
schema and fills whichever shape the server has, so the agent works against both.
"""

import json
from dataclasses import dataclass
from decimal import Decimal

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools


@dataclass(frozen=True)
class CartItem:
    sku: str
    quantity: int
    unit_price: Decimal  # page price; only sent to the old tool shape, never to the new one


def payment_arguments(
    schema: dict, mandate_id: str, merchant_id: str, items: list[CartItem]
) -> dict:
    properties = (schema or {}).get("properties", {})
    args = {"mandate_id": mandate_id, "merchant_id": merchant_id}
    if "amount" in properties:  # old shape: the agent states the total (removed by the switch)
        total = sum((i.unit_price * i.quantity for i in items), Decimal("0"))
        args["amount"] = f"{total:.2f}"
        args["items"] = ", ".join(f"{i.quantity} x {i.sku}" for i in items)
    else:
        args["items"] = [{"sku": i.sku, "quantity": i.quantity} for i in items]
    return args


def parse_result(content) -> dict:
    """Tool output as a dict. MCP returns the tool's dict as JSON text."""
    if isinstance(content, dict):
        return content
    if isinstance(content, list):
        texts = [b.get("text", "") for b in content if isinstance(b, dict)]
        content = next((t for t in texts if t), "")
    try:
        value = json.loads(content)
    except (TypeError, ValueError):
        return {"decision": "UNKNOWN", "raw": str(content)}
    return value if isinstance(value, dict) else {"decision": "UNKNOWN", "raw": value}


class McpLeash:
    """One MCP session to Leash for the whole run. Use as `async with McpLeash(connection)`."""

    def __init__(self, connection: dict):
        self._client = MultiServerMCPClient({"leash": connection})
        self._tools = {}

    async def __aenter__(self):
        self._session_cm = self._client.session("leash")
        session = await self._session_cm.__aenter__()
        try:
            self._tools = {t.name: t for t in await load_mcp_tools(session)}
            if "request_payment" not in self._tools:
                raise RuntimeError(
                    f"Leash has no request_payment tool (tools: {sorted(self._tools)})"
                )
        except BaseException:
            # `async with` does not call __aexit__ when __aenter__ fails, so close the session
            # here (normally, so the MCP client shuts down cleanly), then re-raise the error.
            await self._session_cm.__aexit__(None, None, None)
            raise
        return self

    async def __aexit__(self, *exc):
        await self._session_cm.__aexit__(*exc)

    async def request_payment(
        self, mandate_id: str, merchant_id: str, items: list[CartItem]
    ) -> dict:
        tool = self._tools["request_payment"]
        schema = tool.args_schema if isinstance(tool.args_schema, dict) else {}
        args = payment_arguments(schema, mandate_id, merchant_id, items)
        return parse_result(await tool.ainvoke(args))


class DryRunLeash:
    """Sends nothing; records what would have been sent. For demos with no backend running."""

    def __init__(self):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def request_payment(self, mandate_id, merchant_id, items) -> dict:
        self.calls.append((mandate_id, merchant_id, items))
        return {"decision": "NOT SENT", "reasons": [{"code": "dry_run", "message": "dry run"}]}
