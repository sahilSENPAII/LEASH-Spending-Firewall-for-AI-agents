"""Serve the stand-in store pages over HTTP until Sahil's real stores run on :8101 / :8102."""

import threading
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STANDIN_DIR = Path(__file__).resolve().parents[1] / "standin_stores"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@contextmanager
def serve_standin_stores(names=("soundhub", "megadeals")):
    """Yield {merchant_id: base_url}, each store on its own free localhost port."""
    servers, urls = [], {}
    try:
        for name in names:
            handler = partial(_QuietHandler, directory=str(STANDIN_DIR / name))
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            servers.append(server)
            urls[name] = f"http://127.0.0.1:{server.server_address[1]}/index.html"
        yield urls
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
