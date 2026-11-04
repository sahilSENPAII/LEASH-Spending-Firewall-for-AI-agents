"""A stand-in Leash MCP server for tests: `python stub_leash_server.py new|old`.

new: request_payment(mandate_id, merchant_id, items=[{sku, quantity}])   (agreed shape)
old: request_payment(mandate_id, merchant_id, amount, items: str)        (shape on main today)
It echoes the arguments back so tests can check exactly what the agent sent.
"""

import sys

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("stub-leash")

if sys.argv[1:] == ["old"]:

    @mcp.tool()
    async def request_payment(mandate_id: str, merchant_id: str, amount: str, items: str) -> dict:
        """Old shape."""
        echo = [mandate_id, merchant_id, amount, items]
        return {"decision": "BLOCK", "reasons": [], "echo": echo}

else:

    @mcp.tool()
    async def request_payment(mandate_id: str, merchant_id: str, items: list[dict]) -> dict:
        """Agreed shape: no price, no amount."""
        decision = "BLOCK" if merchant_id == "megadeals" else "ALLOW"
        reasons = [{"code": "merchant_not_allowed", "message": "no"}] if decision == "BLOCK" else []
        return {"decision": decision, "reasons": reasons, "echo": [mandate_id, merchant_id, items]}


if __name__ == "__main__":
    mcp.run()
