"""Smoke test the intent judge against real Groq (uses GROQ_API_KEY from .env; no database).

Asks twice with the m_demo intent: a Sony XM5 cart (should match) and the injected 3 x $100 gift
card cart (should not), and prints how long each took (JUDGE_TIMEOUT_SECONDS is per attempt).
Two Groq calls, on the free tier.
cd backend && uv run python -m scripts.smoke_judge
"""

import asyncio
import time

from app.catalog import DEFAULT_CATALOG_PATH, load_catalog
from app.judge import Cart, CartItem, judge_intent
from app.policy import Mandate
from scripts.seed_demo_mandate import DEMO_TERMS

MANDATE = Mandate(**{k: v for k, v in DEMO_TERMS.items() if k != "intent"}, revoked=False)


def _cart(merchant_id: str, sku: str, quantity: int) -> Cart:
    product = load_catalog(DEFAULT_CATALOG_PATH)[sku]
    item = CartItem(name=product.name, quantity=quantity, unit_price=product.price)
    return Cart(merchant_id=merchant_id, items=(item,), total=product.price * quantity)


async def main() -> None:
    for label, cart in (
        ("Sony XM5 (expect match)", _cart("soundhub", "sh-sony-xm5", 1)),
        ("3 x $100 gift card (expect mismatch)", _cart("megadeals", "md-giftcard-100", 3)),
    ):
        start = time.perf_counter()
        result = await judge_intent(intent=DEMO_TERMS["intent"], mandate=MANDATE, cart=cart)
        print(f"{label} ({time.perf_counter() - start:.2f} s):\n  {result}")


if __name__ == "__main__":
    asyncio.run(main())
