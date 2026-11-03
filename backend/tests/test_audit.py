import hashlib
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, update

from app import audit, mandates
from app.audit import GENESIS_HASH, append_audit, entry_hash, verify_chain
from app.config import Settings
from app.mandates import MandateTerms, create_mandate, generate_signing_key, revoke_mandate
from app.models import AuditEvent

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


async def _append(sessions, n=1, **overrides):
    entries = []
    async with sessions() as s:
        for i in range(n):
            args = {
                "event_type": "payment.decided",
                "payment_id": f"p{i}",
                "payload": {"decision": "ALLOW", "amount": Decimal("179.00"), "reasons": []},
                "now": NOW + timedelta(seconds=i),
            }
            entries.append(await append_audit(s, **(args | overrides)))
        await s.commit()
    return entries


async def _verify(sessions):
    async with sessions() as s:
        return await verify_chain(s)


async def _tamper(sessions, entry_id, **values):
    async with sessions() as s:
        await s.execute(update(AuditEvent).where(AuditEvent.id == entry_id).values(**values))
        await s.commit()


# --- appending ---------------------------------------------------------------


async def test_empty_chain_verifies(sessions):
    assert await _verify(sessions) == audit.ChainCheck(ok=True, checked=0, first_bad_id=None)


async def test_entries_link_to_the_previous_hash(sessions):
    first, second, third = await _append(sessions, 3)

    assert first.prev_hash == GENESIS_HASH
    assert second.prev_hash == first.hash
    assert third.prev_hash == second.hash
    assert len({first.hash, second.hash, third.hash}) == 3
    assert all(len(e.hash) == 64 for e in (first, second, third))


async def test_hash_is_recomputable_from_the_entry(sessions):
    (entry,) = await _append(sessions)
    assert entry.hash == entry_hash(
        GENESIS_HASH,
        created_at=NOW,
        event_type="payment.decided",
        payment_id="p0",
        payload={"decision": "ALLOW", "amount": "179.00", "reasons": []},
    )


async def test_hash_matches_an_independent_sha256(sessions):
    (entry,) = await _append(sessions, payload={"amount": Decimal("179.00")})
    body = (
        b'{"created_at":"2026-11-04T12:00:00Z","event_type":"payment.decided",'
        b'"payload":{"amount":"179.00"},"payment_id":"p0"}'
    )
    assert entry.hash == hashlib.sha256(b"0" * 64 + body).hexdigest()


async def test_same_content_after_a_different_entry_hashes_differently(sessions):
    first, second = await _append(sessions, 2, payment_id="p", now=NOW)
    assert first.hash != second.hash


async def test_payload_is_stored_exactly_as_hashed(sessions):
    (entry,) = await _append(sessions, payload={"amount": Decimal("1.50"), "at": NOW})
    async with sessions() as s:
        row = await s.get(AuditEvent, entry.id)
    assert row.payload == entry.payload == {"amount": "1.50", "at": "2026-11-04T12:00:00Z"}
    assert row.prev_hash == entry.prev_hash and row.hash == entry.hash


async def test_chain_of_many_entries_verifies(sessions):
    await _append(sessions, 5)
    await _append(sessions, 2, payment_id=None, event_type="mandate.created")
    assert await _verify(sessions) == audit.ChainCheck(ok=True, checked=7, first_bad_id=None)


async def test_appends_in_separate_transactions_keep_linking(sessions):
    (first,) = await _append(sessions)
    (second,) = await _append(sessions)
    assert second.prev_hash == first.hash
    assert (await _verify(sessions)).ok


@pytest.mark.parametrize(
    "payload", [{"amount": 1.5}, {"nested": [0.1]}, {"when": datetime(2026, 11, 4)}, {"x": {1}}]
)
async def test_unhashable_payload_is_refused_and_nothing_written(sessions, payload):
    async with sessions() as s:
        with pytest.raises((TypeError, ValueError)):
            await append_audit(s, event_type="payment.decided", payment_id=None, payload=payload,
                               now=NOW)
    assert await _verify(sessions) == audit.ChainCheck(ok=True, checked=0, first_bad_id=None)


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"now": datetime(2026, 11, 4, 12, 0)}, ValueError),
        ({"event_type": ""}, ValueError),
        ({"payload": ["not", "a", "dict"]}, TypeError),
    ],
)
async def test_bad_arguments_are_refused(sessions, overrides, error):
    with pytest.raises(error):
        await _append(sessions, **overrides)
    assert (await _verify(sessions)).checked == 0


async def test_timezone_of_now_does_not_change_the_hash(sessions):
    ist = timezone(timedelta(hours=5, minutes=30))
    (entry,) = await _append(sessions, now=NOW.astimezone(ist))
    assert entry.hash == entry_hash(
        GENESIS_HASH,
        created_at=NOW,
        event_type="payment.decided",
        payment_id="p0",
        payload=entry.payload,
    )
    assert (await _verify(sessions)).ok


async def test_append_refuses_to_extend_an_unhashed_row(sessions):
    async with sessions() as s:
        s.add(AuditEvent(id=1, created_at=NOW, event_type="payment.decided", payload={}))
        await s.commit()
    with pytest.raises(ValueError, match="no hash"):
        await _append(sessions)


# --- tampering ---------------------------------------------------------------


@pytest.mark.parametrize(
    "values",
    [
        {"payload": {"decision": "BLOCK", "amount": "179.00", "reasons": []}},
        {"event_type": "payment.blocked"},
        {"payment_id": "p-other"},
        {"created_at": NOW + timedelta(days=1)},
    ],
)
async def test_editing_any_field_is_detected_at_that_entry(sessions, values):
    entries = await _append(sessions, 4)
    await _tamper(sessions, entries[1].id, **values)
    assert await _verify(sessions) == audit.ChainCheck(
        ok=False, checked=1, first_bad_id=entries[1].id
    )


async def test_editing_the_stored_prev_hash_is_detected(sessions):
    entries = await _append(sessions, 3)
    await _tamper(sessions, entries[1].id, prev_hash="f" * 64)
    assert await _verify(sessions) == audit.ChainCheck(
        ok=False, checked=1, first_bad_id=entries[1].id
    )


async def test_rewriting_one_entry_with_a_fresh_hash_breaks_the_next_link(sessions):
    entries = await _append(sessions, 3)
    forged_payload = {"decision": "BLOCK"}
    forged_hash = entry_hash(
        entries[1].prev_hash,
        created_at=NOW + timedelta(seconds=1),
        event_type="payment.decided",
        payment_id="p1",
        payload=forged_payload,
    )
    await _tamper(sessions, entries[1].id, payload=forged_payload, hash=forged_hash)

    assert await _verify(sessions) == audit.ChainCheck(
        ok=False, checked=2, first_bad_id=entries[2].id
    )


async def test_deleting_a_middle_entry_is_detected(sessions):
    entries = await _append(sessions, 3)
    async with sessions() as s:
        await s.execute(delete(AuditEvent).where(AuditEvent.id == entries[1].id))
        await s.commit()
    assert await _verify(sessions) == audit.ChainCheck(
        ok=False, checked=1, first_bad_id=entries[2].id
    )


async def test_deleting_the_first_entry_is_detected(sessions):
    entries = await _append(sessions, 2)
    async with sessions() as s:
        await s.execute(delete(AuditEvent).where(AuditEvent.id == entries[0].id))
        await s.commit()
    result = await _verify(sessions)
    assert not result.ok and result.first_bad_id == entries[1].id


async def test_swapping_two_entries_is_detected(sessions):
    first, second = await _append(sessions, 2)
    await _tamper(sessions, first.id, id=999)
    await _tamper(sessions, second.id, id=first.id)
    await _tamper(sessions, 999, id=second.id)
    result = await _verify(sessions)
    assert not result.ok and result.first_bad_id == first.id


async def test_unhashed_row_fails_verification(sessions):
    entries = await _append(sessions, 2)
    async with sessions() as s:
        s.add(AuditEvent(created_at=NOW, event_type="payment.decided", payload={}))
        await s.commit()
        bad_id = (await s.scalars(select(AuditEvent.id).order_by(AuditEvent.id.desc()))).first()
    assert await _verify(sessions) == audit.ChainCheck(ok=False, checked=2, first_bad_id=bad_id)
    assert bad_id > entries[-1].id


async def test_corrupt_payload_is_reported_not_raised(sessions):
    entries = await _append(sessions, 2)
    await _tamper(sessions, entries[0].id, payload={"amount": 1.5})
    assert await _verify(sessions) == audit.ChainCheck(
        ok=False, checked=0, first_bad_id=entries[0].id
    )


# --- advisory lock -----------------------------------------------------------


class _RecordingSession:
    def __init__(self, dialect):
        self.dialect = dialect
        self.sql = []

    def get_bind(self):
        return type("Bind", (), {"dialect": type("Dialect", (), {"name": self.dialect})()})()

    async def execute(self, statement, params=None):
        self.sql.append((str(statement), params))


async def test_postgres_takes_the_chain_lock():
    session = _RecordingSession("postgresql")
    await audit._lock_chain(session)
    assert session.sql == [("SELECT pg_advisory_xact_lock(:key)", {"key": audit._CHAIN_LOCK_KEY})]


async def test_sqlite_skips_the_chain_lock():
    session = _RecordingSession("sqlite")
    await audit._lock_chain(session)
    assert session.sql == []


# --- mandate events ----------------------------------------------------------


@pytest.fixture
def signing_key(monkeypatch):
    settings = Settings(mandate_signing_key=generate_signing_key())
    monkeypatch.setattr(mandates, "get_settings", lambda: settings)


def _terms():
    return MandateTerms(
        mandate_id="m_demo",
        intent="Sony or Bose noise-cancelling headphones",
        category="electronics",
        max_payment_amount=Decimal("200.00"),
        total_budget=Decimal("500.00"),
        max_payment_count=3,
        allowed_merchant_ids=frozenset({"soundhub"}),
        expires_at=NOW + timedelta(days=7),
        created_at=NOW,
    )


async def _events(sessions):
    async with sessions() as s:
        return (await s.scalars(select(AuditEvent).order_by(AuditEvent.id))).all()


async def test_create_and_revoke_write_mandate_events(sessions, signing_key):
    async with sessions() as s:
        record = await create_mandate(s, _terms())
        await revoke_mandate(s, "m_demo", NOW + timedelta(hours=1))
        await revoke_mandate(s, "m_demo", NOW + timedelta(hours=2))  # no second event
        await s.commit()

    created, revoked = await _events(sessions)
    assert created.event_type == "mandate.created" and created.payment_id is None
    assert created.payload == {
        "mandate_id": "m_demo",
        "intent": "Sony or Bose noise-cancelling headphones",
        "category": "electronics",
        "max_payment_amount": "200.00",
        "total_budget": "500.00",
        "max_payment_count": 3,
        "allowed_merchant_ids": ["soundhub"],
        "expires_at": "2026-11-11T12:00:00Z",
        "signature": record.signature,
    }
    assert revoked.event_type == "mandate.revoked"
    assert revoked.payload == {"mandate_id": "m_demo"}
    assert revoked.prev_hash == created.hash
    assert await _verify(sessions) == audit.ChainCheck(ok=True, checked=2, first_bad_id=None)


async def test_failed_create_writes_no_event(sessions, signing_key):
    async with sessions() as s:
        await create_mandate(s, _terms())
        await s.commit()
    async with sessions() as s:
        with pytest.raises(ValueError, match="already exists"):
            await create_mandate(s, _terms())
    assert [e.event_type for e in await _events(sessions)] == ["mandate.created"]


# --- GET /audit/verify -------------------------------------------------------


async def test_audit_verify_endpoint_reports_the_chain(sessions):
    from fastapi.testclient import TestClient

    from app import db
    from app.main import app

    entries = await _append(sessions, 2)

    async def session_override():
        async with sessions() as s:
            yield s

    app.dependency_overrides[db.get_session] = session_override
    try:
        client = TestClient(app)
        assert client.get("/audit/verify").json() == {
            "ok": True,
            "checked": 2,
            "first_bad_id": None,
        }
        await _tamper(sessions, entries[0].id, event_type="payment.blocked")
        assert client.get("/audit/verify").json() == {
            "ok": False,
            "checked": 0,
            "first_bad_id": entries[0].id,
        }
    finally:
        app.dependency_overrides.clear()
