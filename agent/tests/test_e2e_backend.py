"""The agent against Leash's REAL MCP server, orchestrator, check_and_reserve and audit chain.

Only the database (throwaway SQLite instead of Neon), PayPal (a fake that captures) and the intent
judge (a fake that says the cart matches) are swapped, in tests/backend_harness.py. Needs `uv` and a
synced backend (`cd backend && uv sync`); skipped otherwise.
"""

import base64
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import HttpFetcher

from leash_agent.__main__ import parse_args, run
from leash_agent.config import REPO_ROOT, AgentSettings
from leash_agent.leash import McpLeash

BACKEND = REPO_ROOT / "backend"
HARNESS = str(Path(__file__).with_name("backend_harness.py"))

pytestmark = pytest.mark.skipif(
    shutil.which("uv") is None or not (BACKEND / ".venv").exists(),
    reason="needs uv and a synced backend (cd backend && uv sync)",
)


@pytest.fixture
def backend(tmp_path):
    db = str(tmp_path / "leash.db")
    env = {
        **{k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"},
        "FASTMCP_LOG_LEVEL": "WARNING",
        "PYTHONPATH": str(BACKEND),
        "MANDATE_SIGNING_KEY": base64.b64encode(os.urandom(32)).decode(),
        "PAYPAL_VAULT_ID": "VAULT-TEST",
        # Fixed here so a developer's own .env values cannot change the result.
        "ANOMALY_STEP_UP_THRESHOLD": "0.5",
        "ANOMALY_BLOCK_THRESHOLD": "0.9",
        "VELOCITY_MAX_PAYMENTS": "3",
        "VELOCITY_WINDOW_SECONDS": "600",
    }
    args = ["run", "--frozen", "--directory", str(BACKEND), "python", HARNESS, db]
    connection = {"transport": "stdio", "command": "uv", "args": args, "env": env}

    def chain_status():
        out = subprocess.run(["uv", *args, "--check-chain"], env=env, capture_output=True,
                             text=True, timeout=120)
        return out.stdout.strip().splitlines()[-1]

    return connection, chain_status


async def _scenario(name, connection):
    return await run(parse_args([name, "--standin"]), AgentSettings(_env_file=None),
                     fetcher=HttpFetcher(), leash=McpLeash(connection))


async def test_attack_is_blocked_and_normal_purchase_goes_through(backend):
    connection, chain_status = backend

    attack = await _scenario("attack", connection)
    (blocked,) = attack["attempts"]
    codes = [r["code"] for r in blocked.response["reasons"]]
    assert blocked.merchant_id == "megadeals"
    assert blocked.response["decision"] == "BLOCK" and blocked.response["status"] == "BLOCKED"
    # Before the catalog switch the policy blocks it (merchant_not_allowed); after it, the cart
    # check blocks it first because headphones + gift cards are two categories (mixed_categories).
    assert {"merchant_not_allowed", "mixed_categories"} & set(codes), codes

    normal = await _scenario("normal", connection)
    (allowed,) = normal["attempts"]
    assert allowed.merchant_id == "soundhub"
    assert allowed.response["decision"] == "ALLOW" and allowed.response["status"] == "CAPTURED"

    # Every decision above went through append_audit: the chain must still verify.
    assert chain_status() == "chain ok"
