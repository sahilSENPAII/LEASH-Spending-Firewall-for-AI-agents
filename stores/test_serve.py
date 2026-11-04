"""Run from the repo root: python -m unittest stores.test_serve"""

import re
import threading
import unittest
from decimal import Decimal
from urllib.error import HTTPError
from urllib.request import urlopen

from stores.serve import HIDDEN_TEXT, STORES, load_products, make_server

PRODUCTS = load_products()


def _skus(html: str) -> list[tuple[str, str]]:
    return re.findall(r'data-sku="([^"]+)" data-price="([^"]+)"', html)


class CatalogTest(unittest.TestCase):
    def test_every_product_is_complete_and_priced_to_the_cent(self):
        for p in PRODUCTS:
            self.assertIn(p["merchant_id"], STORES)
            self.assertTrue(p["sku"] and p["name"] and p["category"])
            self.assertRegex(p["price"], r"^\d+\.\d\d$")
            self.assertGreater(Decimal(p["price"]), 0)

    def test_skus_are_unique(self):
        skus = [p["sku"] for p in PRODUCTS]
        self.assertEqual(len(skus), len(set(skus)))

    def test_demo_products_are_present(self):
        found = {p["sku"]: (p["merchant_id"], p["category"]) for p in PRODUCTS}
        self.assertEqual(found["sh-sony-xm5"], ("soundhub", "electronics"))
        self.assertEqual(found["sh-bose-qc45"], ("soundhub", "electronics"))
        self.assertEqual(found["md-sony-xm5"], ("megadeals", "electronics"))
        self.assertEqual(found["md-giftcard-100"], ("megadeals", "gift_cards"))


class ServeTest(unittest.TestCase):
    def setUp(self):
        self.urls = {}
        for merchant_id in STORES:
            server = make_server(merchant_id, PRODUCTS, port=0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            self.urls[merchant_id] = f"http://127.0.0.1:{server.server_address[1]}"

    def _get(self, url: str) -> str:
        with urlopen(url) as r:
            return r.read().decode()

    def test_each_store_lists_and_links_only_its_own_products(self):
        for merchant_id, url in self.urls.items():
            own = [(p["sku"], p["price"]) for p in PRODUCTS if p["merchant_id"] == merchant_id]
            index = self._get(url + "/")
            self.assertEqual(_skus(index), own)
            self.assertEqual(self._get(url + "/index.html"), index)
            for sku, price in own:
                page = self._get(f"{url}/p/{sku}.html")
                self.assertEqual(_skus(page), [(sku, price)])
                self.assertIn(f'href="p/{sku}.html"', index)
                self.assertIn(f"${price}", page)
                self.assertIn(f"SKU {sku}", page)

    def test_hidden_instruction_is_only_on_the_megadeals_sony_page(self):
        for p in PRODUCTS:
            page = self._get(f"{self.urls[p['merchant_id']]}/p/{p['sku']}.html")
            hidden = '<p style="color:white;font-size:1px">' in page
            self.assertEqual(hidden, p["sku"] == "md-sony-xm5")
        page = self._get(self.urls["megadeals"] + "/p/md-sony-xm5.html")
        self.assertIn(HIDDEN_TEXT["md-sony-xm5"], page)

    def test_unknown_paths_and_other_stores_products_are_404(self):
        for path in ("/p/md-giftcard-100.html", "/p/nope.html", "/catalog.json", "/../serve.py"):
            with self.assertRaises(HTTPError) as caught:
                self._get(self.urls["soundhub"] + path)
            self.assertEqual(caught.exception.code, 404)
            caught.exception.close()


if __name__ == "__main__":
    unittest.main()
