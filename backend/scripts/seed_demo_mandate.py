"""Create the signed demo mandate `m_demo` (uses DATABASE_URL and MANDATE_SIGNING_KEY from .env).

Same terms as the orchestrator's DEMO_MANDATE. Run once per database, after `alembic upgrade head`:
cd backend && uv run python -m scripts.seed_demo_mandate
"""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from app.db import get_sessionmaker
from app.mandates import MandateTerms, create_mandate, get_mandate

DEMO_TERMS = dict(
    mandate_id="m_demo",
    intent="Sony or Bose noise-cancelling headphones",
    category="electronics",
    max_payment_amount=Decimal("200.00"),
    total_budget=Decimal("500.00"),
    max_payment_count=5,
    allowed_merchant_ids=frozenset({"soundhub"}),
    expires_at=datetime(2026, 12, 31, tzinfo=UTC),
)


async def main() -> None:
    async with get_sessionmaker()() as session:
        if await get_mandate(session, "m_demo") is not None:
            print("m_demo already exists; nothing to do")
            return
        record = await create_mandate(
            session, MandateTerms(**DEMO_TERMS, created_at=datetime.now(UTC))
        )
        await session.commit()
        print(f"created {record.terms.mandate_id}, signature {record.signature[:16]}...")


if __name__ == "__main__":
    asyncio.run(main())
