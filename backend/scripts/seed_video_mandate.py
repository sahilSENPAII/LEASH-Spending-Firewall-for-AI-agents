"""Create the signed demo-video mandate `m_video` (uses DATABASE_URL and MANDATE_SIGNING_KEY from
.env). Same intent and limits per payment as `m_demo`, with a fresh, larger budget.

Run once per database, after `alembic upgrade head`:
cd backend && uv run python -m scripts.seed_video_mandate
"""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from app.db import get_sessionmaker
from app.mandates import MandateTerms, create_mandate, get_mandate

VIDEO_TERMS = dict(
    mandate_id="m_video",
    intent="Sony or Bose noise-cancelling headphones",
    category="electronics",
    max_payment_amount=Decimal("200.00"),
    total_budget=Decimal("1000.00"),
    max_payment_count=10,
    allowed_merchant_ids=frozenset({"soundhub"}),
    expires_at=datetime(2026, 12, 31, tzinfo=UTC),
)


async def main() -> None:
    async with get_sessionmaker()() as session:
        if await get_mandate(session, "m_video") is not None:
            print("m_video already exists; nothing to do")
            return
        record = await create_mandate(
            session, MandateTerms(**VIDEO_TERMS, created_at=datetime.now(UTC))
        )
        await session.commit()
        print(f"created {record.terms.mandate_id}, signature {record.signature[:16]}...")


if __name__ == "__main__":
    asyncio.run(main())
