import sys
from decimal import Decimal
from pathlib import Path

import pytest

from leash_agent.leash import CartItem, DryRunLeash, McpLeash, parse_result, payment_arguments

ITEMS = [
    CartItem("md-sony-xm5", 1, Decimal("149.00")),
    CartItem("md-giftcard-100", 3, Decimal("100")),
]
STUB = str(Path(__file__).with_name("stub_leash_server.py"))


def _stub(shape):
    return {"transport": "stdio", "command": sys.executable, "args": [STUB, shape]}


def test_new_shape_sends_skus_and_quantities_only():
    schema = {"properties": {"mandate_id": {}, "merchant_id": {}, "items": {"type": "array"}}}
    assert payment_arguments(schema, "m_demo", "megadeals", ITEMS) == {
        "mandate_id": "m_demo",
        "merchant_id": "megadeals",
        "items": [{"sku": "md-sony-xm5", "quantity": 1}, {"sku": "md-giftcard-100", "quantity": 3}],
    }


def test_old_shape_sends_the_page_total_and_an_item_string():
    schema = {"properties": {"mandate_id": {}, "merchant_id": {}, "amount": {}, "items": {}}}
    assert payment_arguments(schema, "m_demo", "megadeals", ITEMS) == {
        "mandate_id": "m_demo",
        "merchant_id": "megadeals",
        "amount": "449.00",
        "items": "1 x md-sony-xm5, 3 x md-giftcard-100",
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


async def test_mcp_new_shape_end_to_end_over_stdio():
    async with McpLeash(_stub("new")) as leash:
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


async def test_mcp_old_shape_end_to_end_over_stdio():
    async with McpLeash(_stub("old")) as leash:
        result = await leash.request_payment("m_demo", "megadeals", ITEMS)
    assert result["echo"] == [
        "m_demo",
        "megadeals",
        "449.00",
        "1 x md-sony-xm5, 3 x md-giftcard-100",
    ]


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
