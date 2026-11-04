import urllib.request
from pathlib import Path

import pytest

from leash_agent.standin import STANDIN_DIR

STORES = {"soundhub": "http://soundhub.test/index.html", "megadeals": "http://megadeals.test/index.html"}


class FileFetcher:
    """Serve the stand-in pages for http://<store>.test/... URLs, without a browser or a server."""

    def __init__(self):
        self.fetched = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def __call__(self, url: str) -> str:
        self.fetched.append(url)
        host, _, path = url.removeprefix("http://").partition("/")
        return Path(STANDIN_DIR, host.removesuffix(".test"), path).read_text()


class RecordingLeash:
    """Fake Leash: records each request and answers with a fixed decision per merchant."""

    def __init__(self, decisions=None):
        self.decisions = decisions or {}
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def request_payment(self, mandate_id, merchant_id, items):
        self.calls.append((mandate_id, merchant_id, items))
        return self.decisions.get(merchant_id, {"decision": "ALLOW", "reasons": []})


class HttpFetcher:
    """Plain HTTP GET, so the CLI and the stand-in server are tested without a browser."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def __call__(self, url):
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.read().decode()


@pytest.fixture
def fetcher():
    return FileFetcher()
