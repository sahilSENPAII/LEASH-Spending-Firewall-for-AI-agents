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


async def test_products_drawn_by_javascript_after_load_are_seen(tmp_path):
    import threading
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    from leash_agent.pages import parse_page

    chromium = os.environ.get("AGENT_CHROMIUM_PATH", "")
    await _browser_or_skip(chromium)
    (tmp_path / "catalog.json").write_text('[{"sku": "x-1", "name": "Late", "price": "9.99"}]')
    (tmp_path / "index.html").write_text(
        "<html><body><div id=list>Loading</div><script>"
        "setTimeout(() => fetch('catalog.json').then(r => r.json()).then(items => {"
        "document.getElementById('list').innerHTML = items.map(p =>"
        " `<div data-sku=\"${p.sku}\" data-price=\"${p.price}\">${p.name}</div>`).join('');"
        "}), 400);</script></body></html>"
    )

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        async with BrowserFetcher(chromium) as fetch:
            html = await fetch(f"http://127.0.0.1:{server.server_address[1]}/index.html")
    finally:
        server.shutdown()
        server.server_close()
    assert [(p.sku, str(p.price)) for p in parse_page("s", "u", html).products] == [("x-1", "9.99")]
