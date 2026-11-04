import sys
from pathlib import Path

import pytest

from leash_agent.leash import CartItem, DryRunLeash, McpLeash, parse_result, payment_arguments

ITEMS = [CartItem("md-sony-xm5", 1), CartItem("md-giftcard-100", 3)]
STUB = str(Path(__file__).with_name("stub_leash_server.py"))


def _stub():
    return {"transport": "stdio", "command": sys.executable, "args": [STUB]}


def test_sends_skus_and_quantities_only():
    assert payment_arguments("m_demo", "megadeals", ITEMS) == {
        "mandate_id": "m_demo",
        "merchant_id": "megadeals",
        "items": [{"sku": "md-sony-xm5", "quantity": 1}, {"sku": "md-giftcard-100", "quantity": 3}],
    }


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ('{"decision": "ALLOW"}', {"decision": "ALLOW"}),
        ([{"type": "text", "text": '{"decision": "BLOCK"}'}], {"decision": "BLOCK"}),
        ({"decision": "STEP_UP"}, {"decision": "STEP_UP"}),
        ("not json", {"decision": "UNKNOWN", "raw": "not json"}),
        ("[1, 2]", {"decision": "UNKNOWN", "raw": [1, 2]}),
    ],
)
def test_parse_result(content, expected):
    assert parse_result(content) == expected


async def test_request_payment_end_to_end_over_stdio():
    async with McpLeash(_stub()) as leash:
        blocked = await leash.request_payment("m_demo", "megadeals", ITEMS)
        allowed = await leash.request_payment("m_demo", "soundhub", ITEMS[:1])
    assert blocked["decision"] == "BLOCK"
    assert blocked["reasons"][0]["code"] == "merchant_not_allowed"
    assert blocked["echo"] == [
        "m_demo",
        "megadeals",
        [{"sku": "md-sony-xm5", "quantity": 1}, {"sku": "md-giftcard-100", "quantity": 3}],
    ]
    assert allowed["decision"] == "ALLOW"


async def test_server_without_request_payment_is_refused(tmp_path):
    server = tmp_path / "empty_server.py"
    server.write_text(
        "from mcp.server.fastmcp import FastMCP\n"
        "mcp = FastMCP('empty')\n"
        "@mcp.tool()\n"
        "def hello() -> str:\n"
        "    return 'hi'\n"
        "mcp.run()\n"
    )
    leash = McpLeash({"transport": "stdio", "command": sys.executable, "args": [str(server)]})
    with pytest.raises(RuntimeError, match="no request_payment tool"):
        async with leash:
            pass


async def test_dry_run_sends_nothing_and_says_so():
    async with DryRunLeash() as leash:
        result = await leash.request_payment("m_demo", "megadeals", ITEMS)
    assert result["decision"] == "NOT SENT"
    assert leash.calls == [("m_demo", "megadeals", ITEMS)]


async def test_failed_setup_still_closes_the_mcp_session(monkeypatch):
    closed = []

    class SpySession:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc):
            closed.append(exc)

    class SpyClient:
        def session(self, name):
            return SpySession()

    async def broken_load(session):
        raise ConnectionError("server died while listing tools")

    monkeypatch.setattr("leash_agent.leash.load_mcp_tools", broken_load)
    leash = McpLeash(_stub())
    leash._client = SpyClient()
    with pytest.raises(ConnectionError):
        async with leash:
            pass
    assert closed == [(None, None, None)]
