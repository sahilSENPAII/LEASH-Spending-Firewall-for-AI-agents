"""Spike 2: can Claude Desktop call a Leash MCP tool?

A tiny MCP server over stdio with two tools. No PayPal, no DB.
Connect it from Claude Desktop (see spikes/README.md), then ask Claude:
  "Use leash to list my mandates, then request a $179 payment to SoundHub."
"""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("leash-spike")

MANDATES = [{"id": "m_demo", "intent": "Sony/Bose headphones", "max_amount": 200.0, "currency": "USD"}]


@mcp.tool()
def list_mandates() -> list[dict]:
    """List the spending mandates the user has signed."""
    return MANDATES


@mcp.tool()
def request_payment(mandate_id: str, merchant: str, amount: float, items: str) -> dict:
    """Ask Leash to pay a merchant under a mandate. Leash decides ALLOW / STEP_UP / BLOCK."""
    m = next((x for x in MANDATES if x["id"] == mandate_id), None)
    if m is None:
        return {"decision": "BLOCK", "reason": "unknown mandate"}
    if amount > m["max_amount"]:
        return {"decision": "BLOCK", "reason": f"amount {amount} exceeds mandate max {m['max_amount']}"}
    return {"decision": "ALLOW", "reason": "within mandate (spike stub, no money moved)"}


if __name__ == "__main__":
    mcp.run()
