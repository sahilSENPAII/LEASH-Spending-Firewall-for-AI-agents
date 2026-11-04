"""A stand-in Leash MCP server for tests: request_payment(mandate_id, merchant_id, items).

It echoes the arguments back so tests can check exactly what the agent sent.
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("stub-leash")

@mcp.tool()
async def request_payment(mandate_id: str, merchant_id: str, items: list[dict]) -> dict:
    """No price, no amount: Leash prices the skus itself."""
    decision = "BLOCK" if merchant_id == "megadeals" else "ALLOW"
    reasons = [{"code": "merchant_not_allowed", "message": "no"}] if decision == "BLOCK" else []
    return {"decision": decision, "reasons": reasons, "echo": [mandate_id, merchant_id, items]}


if __name__ == "__main__":
    mcp.run()
