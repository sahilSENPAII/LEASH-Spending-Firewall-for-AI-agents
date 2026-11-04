import json
from decimal import Decimal

import pytest

from app import catalog
from app.catalog import DEFAULT_CATALOG_PATH, Product, load_catalog, price_cart
from app.config import REPO_ROOT, Settings

GOOD = {
    "sku": "x-1",
    "merchant_id": "shop",
    "name": "Thing",
    "price": "10.00",
    "category": "electronics",
}


def _write(tmp_path, *products):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({"products": list(products)}))
    return path


@pytest.fixture
def settings(monkeypatch):
    settings = Settings(_env_file=None)
    monkeypatch.setattr(catalog, "get_settings", lambda: settings)
    catalog.get_catalog.cache_clear()
    yield settings
    catalog.get_catalog.cache_clear()


def test_shipped_catalog_prices_the_demo_skus():
    products = load_catalog(DEFAULT_CATALOG_PATH)
    assert DEFAULT_CATALOG_PATH == REPO_ROOT / "stores" / "catalog.json"
    assert products["sh-sony-xm5"] == Product(
        "sh-sony-xm5", "soundhub", "Sony WH-1000XM5", Decimal("179.00"), "electronics"
    )
    assert products["md-giftcard-100"].price == Decimal("100.00")
    assert products["md-giftcard-100"].category == "gift_cards"
    assert {p.merchant_id for p in products.values()} == {"soundhub", "megadeals"}


@pytest.mark.parametrize(
    "bad",
    [
        GOOD | {"price": "10"},
        GOOD | {"price": "10.000"},
        GOOD | {"price": "0.00"},
        GOOD | {"price": "-1.00"},
        GOOD | {"price": "NaN"},
        GOOD | {"price": "ten"},
        GOOD | {"price": 10.25},
        GOOD | {"category": ""},
        {k: v for k, v in GOOD.items() if k != "merchant_id"},
    ],
)
def test_bad_catalog_entry_refuses_to_load(tmp_path, bad):
    with pytest.raises((ValueError, KeyError)):
        load_catalog(_write(tmp_path, bad))


def test_duplicate_sku_refuses_to_load(tmp_path):
    with pytest.raises(ValueError, match="twice"):
        load_catalog(_write(tmp_path, GOOD, GOOD | {"merchant_id": "other"}))


def test_catalog_path_setting_is_relative_to_the_repo_root(settings, tmp_path):
    path = _write(tmp_path, GOOD)
    settings.catalog_path = str(path)  # an absolute path is used as it is
    assert set(catalog.get_catalog()) == {"x-1"}

    catalog.get_catalog.cache_clear()
    settings.catalog_path = "stores/catalog.json"
    assert catalog.get_catalog() == load_catalog(DEFAULT_CATALOG_PATH)


def test_empty_catalog_path_means_the_shipped_catalog(settings):
    settings.catalog_path = ""
    assert catalog.get_catalog() == load_catalog(DEFAULT_CATALOG_PATH)


def test_missing_catalog_file_raises(settings, tmp_path):
    settings.catalog_path = str(tmp_path / "missing.json")
    with pytest.raises(FileNotFoundError):
        catalog.get_catalog()


def test_cart_total_and_category_come_from_the_catalog():
    products = load_catalog(DEFAULT_CATALOG_PATH)
    items = [{"sku": "sh-sony-xm5", "quantity": 2}, {"sku": "sh-bose-qc45", "quantity": 1}]
    cart = price_cart(products, "soundhub", items)

    assert cart.reasons == []
    assert (cart.amount, cart.category) == (Decimal("557.00"), "electronics")
    assert [line["unit_price"] for line in cart.lines] == ["179.00", "199.00"]
