"""Leash's product catalog (stores/catalog.json): the only prices and categories Leash trusts.

The agent sends SKUs and quantities. Leash prices each line from here and never takes a price,
an amount or a category from the agent.
"""

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from app.config import REPO_ROOT, get_settings

DEFAULT_CATALOG_PATH = REPO_ROOT / "stores" / "catalog.json"
MAX_QUANTITY = 10


@dataclass(frozen=True)
class Product:
    sku: str
    merchant_id: str
    name: str
    price: Decimal
    category: str


@dataclass(frozen=True)
class Cart:
    amount: Decimal  # total of the lines Leash could price
    category: str  # the single category of the cart, "" when there is none
    lines: list[dict]  # what was asked, with Leash's unit_price on each line it priced
    reasons: list[dict] = field(default_factory=list)  # empty when the cart is valid


def load_catalog(path: Path) -> dict[str, Product]:
    """Parse and check the catalog. Any problem raises, so a bad file blocks every payment."""
    catalog = {}
    for p in json.loads(path.read_text())["products"]:
        if not (isinstance(p["price"], str) and re.fullmatch(r"\d+\.\d\d", p["price"])):
            raise ValueError(f'catalog price for {p["sku"]} must be a string such as "10.00"')
        product = Product(
            sku=p["sku"],
            merchant_id=p["merchant_id"],
            name=p["name"],
            price=Decimal(p["price"]),
            category=p["category"],
        )
        if not (product.sku and product.merchant_id and product.category):
            raise ValueError(f"catalog entry {p!r} is incomplete")
        if product.price <= 0:
            raise ValueError(f"catalog price for {product.sku} must be positive")
        if product.sku in catalog:
            raise ValueError(f"catalog has {product.sku} twice")
        catalog[product.sku] = product
    return catalog


@lru_cache
def get_catalog() -> dict[str, Product]:
    path = get_settings().catalog_path
    return load_catalog(REPO_ROOT / path if path else DEFAULT_CATALOG_PATH)


def _reason(code: str, message: str) -> dict:
    return {"code": code, "message": message}


def _as_sent(value):
    # Canonical JSON (the audit chain) refuses floats; keep anything odd as text.
    return value if value is None or isinstance(value, str | int) else repr(value)


def price_cart(catalog: dict[str, Product], merchant_id: str, items: list[dict]) -> Cart:
    if not items:
        return Cart(Decimal("0.00"), "", [], [_reason("empty_cart", "The cart has no items.")])

    amount, categories, lines, reasons = Decimal("0.00"), set(), [], []
    for item in items:
        sku, quantity = item.get("sku"), item.get("quantity")
        line = {"sku": _as_sent(sku), "quantity": _as_sent(quantity)}
        lines.append(line)
        product = catalog.get(sku) if isinstance(sku, str) else None
        if product is None:
            reasons.append(_reason("unknown_sku", f"Item {sku!r} is not in Leash's catalog."))
            continue
        if product.merchant_id != merchant_id:
            reasons.append(
                _reason(
                    "sku_wrong_merchant",
                    f"Item {sku} is sold by {product.merchant_id}, not {merchant_id}.",
                )
            )
            continue
        # bool is an int in Python; True must not pass as quantity 1.
        if not (type(quantity) is int and 1 <= quantity <= MAX_QUANTITY):
            reasons.append(
                _reason(
                    "invalid_quantity",
                    f"Quantity for {sku} must be a whole number from 1 to {MAX_QUANTITY}.",
                )
            )
            continue
        line["unit_price"] = str(product.price)
        amount += product.price * quantity
        categories.add(product.category)

    if len(categories) > 1:
        reasons.append(
            _reason(
                "mixed_categories",
                f"Items are from more than one category: {', '.join(sorted(categories))}.",
            )
        )
    category = categories.pop() if len(categories) == 1 else ""
    return Cart(amount, category, lines, reasons)
