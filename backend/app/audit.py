"""Hash-chained audit log over `audit_events` (docs/CONTRACTS.md section 3).

Each entry stores the hash of the entry before it, so editing, deleting or reordering any entry
breaks every link after it, and `verify_chain` reports the first bad one. Every write must go
through `append_audit`: a row written directly (no hash) makes the chain fail verification.

Functions take the caller's session and never commit; the entry commits with the change it records.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.canonical import canonical_json
from app.models import AuditEvent

GENESIS_HASH = "0" * 64
# Any fixed 64-bit number works; it only has to be the same for every append.
_CHAIN_LOCK_KEY = 0x4C45415348  # "LEASH"


@dataclass(frozen=True, slots=True)
class AuditEntry:
    id: int
    created_at: datetime
    event_type: str
    payment_id: str | None
    payload: dict
    prev_hash: str
    hash: str


@dataclass(frozen=True, slots=True)
class ChainCheck:
    ok: bool
    checked: int
    first_bad_id: int | None


def entry_hash(
    prev_hash: str,
    *,
    created_at: datetime,
    event_type: str,
    payment_id: str | None,
    payload: dict,
) -> str:
    """sha256(prev_hash + canonical JSON of the entry), as 64 hex characters."""
    body = canonical_json(
        {
            "created_at": created_at,
            "event_type": event_type,
            "payment_id": payment_id,
            "payload": payload,
        }
    )
    return hashlib.sha256(prev_hash.encode("ascii") + body).hexdigest()


async def append_audit(
    session: AsyncSession,
    *,
    event_type: str,
    payment_id: str | None,
    payload: dict,
    now: datetime,
) -> AuditEntry:
    """Add one entry linked to the latest one.

    Raises TypeError/ValueError for a payload or time that cannot be hashed exactly (floats,
    naive datetimes), and ValueError if the latest entry has no hash (a row written around
    this function), so a broken chain is never extended silently.
    """
    if not isinstance(event_type, str) or not event_type:
        raise ValueError("event_type must be a non-empty string")
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dict")
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    # Store UTC: SQLite would otherwise keep the local wall time and drop the offset.
    now = now.astimezone(UTC)
    # Store exactly what is hashed: Decimal and datetime become strings, floats are refused.
    stored_payload = json.loads(canonical_json(payload))

    await _lock_chain(session)
    last = await session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    if last is None:
        prev_hash = GENESIS_HASH
    elif last.hash is None:
        raise ValueError(f"audit entry {last.id} has no hash; the chain is broken")
    else:
        prev_hash = last.hash

    digest = entry_hash(
        prev_hash,
        created_at=now,
        event_type=event_type,
        payment_id=payment_id,
        payload=stored_payload,
    )
    row = AuditEvent(
        created_at=now,
        event_type=event_type,
        payment_id=payment_id,
        payload=stored_payload,
        prev_hash=prev_hash,
        hash=digest,
    )
    session.add(row)
    await session.flush()
    return AuditEntry(
        id=row.id,
        created_at=now,
        event_type=event_type,
        payment_id=payment_id,
        payload=stored_payload,
        prev_hash=prev_hash,
        hash=digest,
    )


async def verify_chain(session: AsyncSession) -> ChainCheck:
    """Recompute every hash in id order. Never raises for bad data; it reports the first bad id."""
    expected_prev = GENESIS_HASH
    checked = 0
    rows = await session.stream_scalars(select(AuditEvent).order_by(AuditEvent.id))
    async for row in rows:
        try:
            good = row.prev_hash == expected_prev and row.hash == entry_hash(
                expected_prev,
                created_at=_utc(row.created_at),
                event_type=row.event_type,
                payment_id=row.payment_id,
                payload=row.payload,
            )
        except (TypeError, ValueError):
            good = False
        if not good:
            return ChainCheck(ok=False, checked=checked, first_bad_id=row.id)
        expected_prev = row.hash
        checked += 1
    return ChainCheck(ok=True, checked=checked, first_bad_id=None)


async def _lock_chain(session: AsyncSession) -> None:
    # Postgres: one append at a time until this transaction ends, so two appends cannot both link
    # to the same previous entry. SQLite (tests) allows one writer anyway and has no such function.
    if session.get_bind().dialect.name == "postgresql":
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _CHAIN_LOCK_KEY})


def _utc(value: datetime | None) -> datetime | None:
    # SQLite returns DateTime(timezone=True) columns without tzinfo; entries are written in UTC.
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)
