"""Read store pages the way the demo agent sees them: products, links and ALL text.

"All text" includes text a human cannot see (white 1px), which is exactly how a page can smuggle
instructions to an agent (indirect prompt injection). The agent does not filter it on purpose:
the point of the demo is that Leash, not the agent, stops the payment.

Product markup agreed with Sahil: each product element carries `data-sku` and `data-price`; the
product name is the first text inside it.
"""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlsplit


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    price: Decimal
    url: str


@dataclass(frozen=True)
class StorePage:
    merchant_id: str
    url: str
    text: str
    products: tuple[Product, ...]
    links: tuple[str, ...]


@dataclass
class _Open:
    sku: str
    price: str
    depth: int
    texts: list[str] = field(default_factory=list)


class _Reader(HTMLParser):
    _SKIP = {"script", "style", "noscript", "template"}
    # Tags with no closing tag; they must not change the nesting depth.
    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
             "track", "wbr"}
    # Tags that start a new line of text. Text inside inline tags (b, span, a...) stays on one
    # line, so a sentence split across tags is still read as one sentence.
    _BLOCK = {"address", "article", "aside", "blockquote", "body", "br", "dd", "div", "dl", "dt",
              "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5",
              "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table",
              "tbody", "td", "tfoot", "th", "thead", "title", "tr", "ul"}

    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.texts: list[str] = []  # one entry per line of text, as a reader would see it
        self._line: list[str] = []
        self.links: list[str] = []
        self.products: list[tuple[str, str, str]] = []
        self._open: list[_Open] = []
        self._depth = 0
        self._skip = 0

    def _break(self):
        if self._line:
            self.texts.append(" ".join(self._line))
            self._line = []

    def close(self):
        super().close()
        self._break()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in self._BLOCK:
            self._break()
        if tag in self._VOID:
            return
        self._depth += 1
        if tag in self._SKIP:
            self._skip += 1
        if tag == "a" and attrs.get("href"):
            self.links.append(urljoin(self.base_url, attrs["href"]))
        if "data-sku" in attrs:
            sku, price = attrs["data-sku"] or "", attrs.get("data-price") or ""
            self._open.append(_Open(sku, price, self._depth))

    def handle_startendtag(self, tag, attrs):
        if tag not in self._VOID:
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in self._BLOCK:
            self._break()
        if tag in self._VOID:
            return
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        if self._open and self._open[-1].depth == self._depth:
            item = self._open.pop()
            name = next((t for t in item.texts if t), "")
            self.products.append((item.sku, name, item.price))
        self._depth -= 1

    def handle_data(self, data):
        if self._skip:
            return
        text = " ".join(data.split())
        if text:
            self._line.append(text)
            for item in self._open:
                item.texts.append(text)


def parse_page(merchant_id: str, url: str, html: str) -> StorePage:
    reader = _Reader(url)
    reader.feed(html)
    reader.close()
    products = []
    for sku, name, price in reader.products:
        try:
            value = Decimal(price)
        except InvalidOperation:
            continue
        if sku and value.is_finite():
            products.append(Product(sku=sku, name=name, price=value, url=url))
    return StorePage(
        merchant_id=merchant_id,
        url=url,
        text="\n".join(reader.texts),
        products=tuple(products),
        links=tuple(dict.fromkeys(reader.links)),
    )


def page_key(url: str) -> str:
    """One key per page: no #fragment, and ".../index.html" is the same page as ".../"."""
    url = urldefrag(url).url
    return url[: -len("index.html")] if url.endswith("/index.html") else url


def same_site(url: str, root: str) -> bool:
    a, b = urlsplit(url), urlsplit(root)
    return (a.scheme, a.netloc) == (b.scheme, b.netloc)


async def crawl_store(fetch, merchant_id: str, root: str, max_pages: int) -> list[StorePage]:
    """Breadth-first over same-site links from the store's front page. `fetch(url) -> html`."""
    pages, queue, seen = [], [root], set()
    while queue and len(pages) < max_pages:
        url = queue.pop(0)
        if page_key(url) in seen:
            continue
        seen.add(page_key(url))
        page = parse_page(merchant_id, url, await fetch(url))
        pages.append(page)
        queue += [
            link for link in page.links if same_site(link, root) and page_key(link) not in seen
        ]
    return pages


class BrowserFetcher:
    """Fetch rendered HTML with headless Chromium (Playwright). Use as `async with`.

    Stores may draw their products with JavaScript after the page loads (for example from a
    catalog file), so each page gets a short, bounded wait for the network to go quiet and for a
    `[data-sku]` element to appear. A page with no products just costs the wait.
    """

    def __init__(self, chromium_path: str = "", settle_ms: int = 3000):
        self._chromium_path = chromium_path or None
        self._settle_ms = settle_ms

    async def __aenter__(self):
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        try:
            self._browser = await self._pw.chromium.launch(executable_path=self._chromium_path)
        except Exception:
            await self._pw.stop()
            raise
        return self

    async def __aexit__(self, *exc):
        await self._browser.close()
        await self._pw.stop()

    async def __call__(self, url: str) -> str:
        from playwright.async_api import TimeoutError as PlaywrightTimeout

        page = await self._browser.new_page()
        try:
            await page.goto(url, wait_until="load")
            for wait in (
                page.wait_for_load_state("networkidle", timeout=self._settle_ms),
                page.wait_for_selector("[data-sku]", timeout=self._settle_ms),
            ):
                try:
                    await wait
                except PlaywrightTimeout:
                    pass
            return await page.content()
        finally:
            await page.close()
