"""The real browser path (Playwright + Chromium). Skipped where no Chromium is installed.

Set AGENT_CHROMIUM_PATH to use a specific Chromium; otherwise the one from
`uv run playwright install chromium` is used.
"""

import os

import pytest
from conftest import RecordingLeash

from leash_agent.__main__ import parse_args, run
from leash_agent.config import AgentSettings
from leash_agent.pages import BrowserFetcher


async def _browser_or_skip(path):
    try:
        async with BrowserFetcher(path):
            return
    except Exception as exc:
        pytest.skip(f"no usable Chromium: {type(exc).__name__}")


async def test_attack_through_a_real_browser():
    chromium = os.environ.get("AGENT_CHROMIUM_PATH", "")
    settings = AgentSettings(_env_file=None, chromium_path=chromium)
    await _browser_or_skip(settings.chromium_path)
    leash = RecordingLeash({"megadeals": {"decision": "BLOCK", "reasons": []}})
    state = await run(parse_args(["attack", "--standin"]), settings, leash=leash)
    ((_, merchant_id, items),) = leash.calls
    assert merchant_id == "megadeals"
    assert [(i.sku, i.quantity) for i in items] == [("md-sony-xm5", 1), ("md-giftcard-100", 3)]
    assert any("SYSTEM:" in p.text for p in state["pages"])
