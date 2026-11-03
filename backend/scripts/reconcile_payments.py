"""Settle payments stuck in REQUESTED or AUTHORIZED, with PayPal as the source of truth.

This writes: it voids holds at PayPal, can create orders while looking one up, and updates the
database. Run it on a laptop (PayPal is not reachable from Claude's sandbox):
cd backend && uv run python -m scripts.reconcile_payments --older-than-minutes 15

Exits 1 if any payment was left unchanged, so a person can look at it.
"""

import argparse
import asyncio
import sys
from datetime import timedelta

from app.config import get_settings
from app.db import get_sessionmaker
from app.paypal import PayPalClient
from app.reconcile import MIN_AGE, ReconcileResult, reconcile_payments


def report(results: list[ReconcileResult]) -> str:
    lines = [
        f"{r.payment_id}  {r.before} -> {r.after or 'unchanged'}  ({r.detail})" for r in results
    ]
    settled = sum(r.after is not None for r in results)
    lines.append(f"{settled}/{len(results)} stuck payments settled")
    return "\n".join(lines)


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--older-than-minutes",
        type=int,
        default=15,
        help=f"only touch payments unchanged this long (at least {MIN_AGE.seconds // 60})",
    )
    args = parser.parse_args(argv)

    s = get_settings()
    if not s.database_url:
        print("DATABASE_URL is not set in .env")
        return 1
    paypal = PayPalClient(s.paypal_client_id, s.paypal_client_secret, s.paypal_base_url)
    try:
        results = await reconcile_payments(
            get_sessionmaker(),
            paypal,
            s.paypal_vault_id,
            older_than=timedelta(minutes=args.older_than_minutes),
        )
    finally:
        await paypal.aclose()
    print(report(results))
    return 0 if all(r.after is not None for r in results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
