"""The agent against an MCP server over HTTP behind a bearer token, like Leash's /mcp on Render."""

import socket
import threading
import time

import pytest
import uvicorn
from mcp.server.fastmcp import FastMCP

from leash_agent.config import AgentSettings, leash_connection
from leash_agent.leash import CartItem, McpLeash

TOKEN = "test-token"


def _server_app():
    mcp = FastMCP("stub-leash-http", stateless_http=True)

    @mcp.tool()
    async def request_payment(mandate_id: str, merchant_id: str, items: list[dict]) -> dict:
        return {"decision": "BLOCK", "reasons": [], "echo": [mandate_id, merchant_id, items]}

    inner = mcp.streamable_http_app()

    async def app(scope, receive, send):  # the same check as Leash's RequireBearerToken
        if scope["type"] == "http":
            sent = dict(scope["headers"]).get(b"authorization", b"")
            if sent != f"Bearer {TOKEN}".encode():
                await send({"type": "http.response.start", "status": 401, "headers": []})
                await send({"type": "http.response.body", "body": b"no"})
                return
        await inner(scope, receive, send)

    return app


@pytest.fixture(scope="module")
def url():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(_server_app(), port=port, log_level="error",
                                           lifespan="on"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=5)


async def test_pays_over_http_with_the_token(url):
    settings = AgentSettings(_env_file=None, leash_url=url, leash_token=TOKEN)
    async with McpLeash(leash_connection(settings)) as leash:
        result = await leash.request_payment("m_demo", "megadeals", [CartItem("md-sony-xm5", 1)])
    assert result["echo"] == ["m_demo", "megadeals", [{"sku": "md-sony-xm5", "quantity": 1}]]


async def test_without_the_token_the_server_refuses(url):
    settings = AgentSettings(_env_file=None, leash_url=url)
    with pytest.raises(BaseException):  # noqa: B017 - the MCP client wraps the 401 in a group
        async with McpLeash(leash_connection(settings)):
            pass
