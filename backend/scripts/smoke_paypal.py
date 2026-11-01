"""Smoke test the real PayPal client against sandbox (run on your laptop, not in CI).

cd backend && uv run python -m scripts.smoke_paypal
"""

import asyncio
import uuid
from decimal import Decimal

from app.config import get_settings
from app.paypal import PayPalClient


async def main() -> None:
    s = get_settings()
    if not s.paypal_vault_id:
        raise SystemExit("Set PAYPAL_VAULT_ID in .env (the saved wallet token from spike 1)")
    pp = PayPalClient(s.paypal_client_id, s.paypal_client_secret, s.paypal_base_url)
    try:
        key = str(uuid.uuid4())
        a = await pp.authorize(
            vault_id=s.paypal_vault_id, amount=Decimal("1.00"), request_id=key, custom_id="soundhub"
        )
        print(f"authorized  order={a.order_id} auth={a.authorization_id}")
        again = await pp.authorize(
            vault_id=s.paypal_vault_id, amount=Decimal("1.00"), request_id=key, custom_id="soundhub"
        )
        print(f"idempotent  same order on retry: {again.order_id == a.order_id}")
        print(f"captured    {await pp.capture(a.authorization_id, key + '-capture')}")

        b = await pp.authorize(
            vault_id=s.paypal_vault_id, amount=Decimal("2.00"), request_id=str(uuid.uuid4())
        )
        await pp.void(b.authorization_id, str(uuid.uuid4()))
        print(f"voided      auth={b.authorization_id}")
    finally:
        await pp.aclose()


if __name__ == "__main__":
    asyncio.run(main())
