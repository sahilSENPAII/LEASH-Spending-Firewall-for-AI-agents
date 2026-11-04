import asyncio
import base64
import dataclasses
import logging
import os
import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import mandates
from app.audit import append_audit, verify_chain
from app.config import Settings
from app.db import to_async_url
from app.mandates import (
    MandateNotFound,
    MandateRecord,
    MandateTerms,
    check_and_reserve,
    create_mandate,
    generate_signing_key,
    get_mandate,
    list_mandates,
    load_signing_key,
    public_key_b64,
    release_budget,
    revoke_mandate,
    sign_terms,
    terms_bytes,
    validate_terms,
    verify_mandate,
    verify_signature,
)
from app.models import BudgetHold, MandateRow, Payment
from app.policy import Mandate, PaymentRequest, PolicyOutcome
from app.state import PaymentStatus, transition

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
KEY = Ed25519PrivateKey.generate()


def make_terms(**overrides) -> MandateTerms:
    fields = dict(
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
    fields.update(overrides)
    return MandateTerms(**fields)


def make_record(terms=None, signature=None, **overrides) -> MandateRecord:
    terms = terms or make_terms()
    fields = dict(
        terms=terms,
        signature=sign_terms(terms, KEY) if signature is None else signature,
        revoked_at=None,
        budget_used=Decimal("0"),
        payment_count=0,
    )
    fields.update(overrides)
    return MandateRecord(**fields)


# --- signing ---------------------------------------------------------------


def test_signed_terms_verify():
    terms = make_terms()
    assert verify_signature(terms, sign_terms(terms, KEY), KEY.public_key())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mandate_id", "m_other"),
        ("intent", "3 x $100 gift cards"),
        ("category", "gift_cards"),
        ("max_payment_amount", Decimal("200.01")),
        ("total_budget", Decimal("5000.00")),
        ("max_payment_count", 4),
        ("allowed_merchant_ids", frozenset({"soundhub", "megadeals"})),
        ("expires_at", NOW + timedelta(days=8)),
        ("created_at", NOW + timedelta(seconds=1)),
    ],
)
def test_changing_any_term_breaks_the_signature(field, value):
    signature = sign_terms(make_terms(), KEY)
    tampered = make_terms(**{field: value})
    assert not verify_signature(tampered, signature, KEY.public_key())


def test_wrong_key_fails():
    terms = make_terms()
    other = Ed25519PrivateKey.generate()
    assert not verify_signature(terms, sign_terms(terms, other), KEY.public_key())


@pytest.mark.parametrize("signature", ["", "not base64!", base64.b64encode(b"x" * 64).decode()])
def test_malformed_signature_fails(signature):
    assert not verify_signature(make_terms(), signature, KEY.public_key())


def test_equal_amounts_sign_the_same():
    # 200 and 200.00 are the same money, so they must produce the same signed bytes.
    assert terms_bytes(make_terms(max_payment_amount=Decimal("200"))) == terms_bytes(make_terms())


def test_merchant_order_does_not_matter():
    a = make_terms(allowed_merchant_ids=frozenset(["soundhub", "megadeals"]))
    b = make_terms(allowed_merchant_ids=frozenset(["megadeals", "soundhub"]))
    assert terms_bytes(a) == terms_bytes(b)


def test_same_instant_in_another_timezone_signs_the_same():
    ist = timezone(timedelta(hours=5, minutes=30))
    assert terms_bytes(make_terms(created_at=NOW.astimezone(ist))) == terms_bytes(make_terms())


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_payment_amount": 200.0},
        {"max_payment_amount": Decimal("0.001")},
        {"total_budget": Decimal("NaN")},
        {"total_budget": Decimal("1E+200")},
        {"max_payment_count": True},
        {"max_payment_count": 3.0},
        {"allowed_merchant_ids": {"soundhub"}},
        {"expires_at": datetime(2026, 12, 1)},
    ],
)
def test_unsignable_terms_are_rejected(overrides):
    with pytest.raises((TypeError, ValueError)):
        sign_terms(make_terms(**overrides), KEY)


# --- verify_mandate --------------------------------------------------------


def test_verify_mandate_returns_policy_mandate():
    check = verify_mandate(make_record(), public_key=KEY.public_key())
    assert check.signature_valid
    assert check.reasons == ()
    assert check.mandate == Mandate(
        mandate_id="m_demo",
        category="electronics",
        max_payment_amount=Decimal("200.00"),
        total_budget=Decimal("500.00"),
        max_payment_count=3,
        allowed_merchant_ids=frozenset({"soundhub"}),
        expires_at=NOW + timedelta(days=7),
        revoked=False,
    )


def test_revoked_record_maps_to_revoked_mandate():
    check = verify_mandate(make_record(revoked_at=NOW), public_key=KEY.public_key())
    assert check.signature_valid
    assert check.mandate.revoked is True


def test_usage_state_is_not_signed():
    # Budget and count change after every payment; the signature must still hold.
    record = make_record(budget_used=Decimal("179.00"), payment_count=1, revoked_at=NOW)
    assert verify_mandate(record, public_key=KEY.public_key()).signature_valid


def test_tampered_record_fails_closed():
    record = make_record()
    tampered = dataclasses.replace(
        record, terms=dataclasses.replace(record.terms, total_budget=Decimal("9999.00"))
    )
    check = verify_mandate(tampered, public_key=KEY.public_key())
    assert not check.signature_valid
    assert [r.code for r in check.reasons] == ["mandate_signature_invalid"]


def test_malformed_record_fails_closed():
    check = verify_mandate("not a record", public_key=KEY.public_key())  # type: ignore[arg-type]
    assert not check.signature_valid
    assert check.mandate is None
    assert [r.code for r in check.reasons] == ["invalid_mandate"]


def test_verify_mandate_uses_settings_key(monkeypatch):
    key_b64 = generate_signing_key()
    key = load_signing_key(key_b64)
    terms = make_terms()
    record = make_record(terms=terms, signature=sign_terms(terms, key))
    monkeypatch.setattr(mandates, "get_settings", lambda: Settings(mandate_signing_key=key_b64))
    assert verify_mandate(record).signature_valid


def test_missing_settings_key_fails_closed(monkeypatch, caplog):
    monkeypatch.setattr(mandates, "get_settings", lambda: Settings(mandate_signing_key=""))
    with caplog.at_level(logging.ERROR, logger="app.mandates"):
        check = verify_mandate(make_record())
    assert not check.signature_valid
    assert check.mandate is None
    assert [r.code for r in check.reasons] == ["mandate_key_unavailable"]
    assert "MANDATE_SIGNING_KEY is not set" in caplog.text


def test_wrong_settings_key_is_logged(monkeypatch, caplog):
    other = generate_signing_key()
    monkeypatch.setattr(mandates, "get_settings", lambda: Settings(mandate_signing_key=other))
    with caplog.at_level(logging.WARNING, logger="app.mandates"):
        check = verify_mandate(make_record())
    assert [r.code for r in check.reasons] == ["mandate_signature_invalid"]
    assert "'m_demo' failed signature verification" in caplog.text
    assert "MANDATE_SIGNING_KEY" in caplog.text


def test_unreadable_record_is_logged(caplog):
    with caplog.at_level(logging.ERROR, logger="app.mandates"):
        verify_mandate("not a record", public_key=KEY.public_key())  # type: ignore[arg-type]
    assert "mandate record could not be read" in caplog.text


def test_settings_key_is_parsed_once(monkeypatch):
    key_b64 = generate_signing_key()
    key = load_signing_key(key_b64)
    terms = make_terms()
    record = make_record(terms=terms, signature=sign_terms(terms, key))
    monkeypatch.setattr(mandates, "get_settings", lambda: Settings(mandate_signing_key=key_b64))
    calls = []

    def counting_load(value):
        calls.append(value)
        return load_signing_key(value)

    monkeypatch.setattr(mandates, "load_signing_key", counting_load)
    mandates._signing_key.cache_clear()
    for _ in range(3):
        assert verify_mandate(record).signature_valid
    assert calls == [key_b64]


# --- keys ------------------------------------------------------------------


def test_generated_key_round_trips():
    value = generate_signing_key()
    key = load_signing_key(value)
    assert len(base64.b64decode(value)) == 32
    assert len(base64.b64decode(public_key_b64(key))) == 32


@pytest.mark.parametrize("value", ["", "not base64!", base64.b64encode(b"short").decode()])
def test_bad_signing_key_is_rejected(value):
    with pytest.raises(ValueError):
        load_signing_key(value)


# --- database: create / revoke / list ----------------------------------------

KEY_B64 = generate_signing_key()
S = PaymentStatus


@pytest.fixture
def settings(monkeypatch):
    value = Settings(
        mandate_signing_key=KEY_B64, velocity_max_payments=3, velocity_window_seconds=600
    )
    monkeypatch.setattr(mandates, "get_settings", lambda: value)
    return value


@pytest.fixture
async def sessions(sessions, settings):
    # The shared SQLite database from conftest.py, with a signing key and velocity limits set.
    return sessions


async def _create(sessions, **overrides):
    async with sessions() as s:
        record = await create_mandate(s, make_terms(**overrides))
        await s.commit()
    return record


async def test_created_mandate_round_trips_and_verifies(sessions):
    created = await _create(sessions)
    async with sessions() as s:
        loaded = await get_mandate(s, "m_demo")

    # SQLite drops tzinfo on read; the record must still carry UTC datetimes and verify.
    assert loaded == created
    assert loaded.terms.expires_at.tzinfo is not None
    assert loaded.budget_used == Decimal("0") and loaded.payment_count == 0
    check = verify_mandate(loaded)
    assert check.signature_valid and check.reasons == ()


async def test_mandate_created_in_another_timezone_still_verifies(sessions):
    ist = timezone(timedelta(hours=5, minutes=30))
    await _create(
        sessions,
        created_at=NOW.astimezone(ist),
        expires_at=(NOW + timedelta(days=7)).astimezone(ist),
    )
    async with sessions() as s:
        loaded = await get_mandate(s, "m_demo")
    assert loaded.terms.created_at == NOW
    assert verify_mandate(loaded).signature_valid


async def test_revoke_with_naive_time_is_refused(sessions):
    await _create(sessions)
    async with sessions() as s:
        with pytest.raises(ValueError, match="timezone-aware"):
            await revoke_mandate(s, "m_demo", datetime(2026, 11, 3))


async def test_duplicate_mandate_id_is_rejected(sessions):
    await _create(sessions)
    with pytest.raises(ValueError, match="already exists"):
        await _create(sessions)


@pytest.mark.parametrize(
    "overrides",
    [
        {"mandate_id": ""},
        {"mandate_id": "x" * 65},
        {"intent": " padded "},
        {"category": None},
        {"max_payment_amount": Decimal("0")},
        {"total_budget": Decimal("-5.00")},
        {"total_budget": Decimal("10000000000.00")},
        {"max_payment_amount": Decimal("1.005")},
        {"max_payment_count": 0},
        {"allowed_merchant_ids": frozenset()},
        {"allowed_merchant_ids": frozenset({""})},
        {"expires_at": NOW},
        {"created_at": datetime(2026, 11, 2)},
    ],
)
async def test_bad_terms_are_rejected(sessions, overrides):
    assert validate_terms(make_terms(**overrides))
    with pytest.raises(ValueError, match="invalid mandate terms"):
        await _create(sessions, **overrides)


async def test_missing_signing_key_refuses_to_create(sessions, monkeypatch):
    monkeypatch.setattr(mandates, "get_settings", lambda: Settings(mandate_signing_key=""))
    with pytest.raises(ValueError, match="MANDATE_SIGNING_KEY"):
        await _create(sessions)


async def test_revoke_keeps_first_time(sessions):
    await _create(sessions)
    async with sessions() as s:
        first = await revoke_mandate(s, "m_demo", NOW)
        second = await revoke_mandate(s, "m_demo", NOW + timedelta(hours=1))
        await s.commit()
    assert first.revoked_at == second.revoked_at == NOW


async def test_revoke_unknown_mandate_raises(sessions):
    async with sessions() as s:
        with pytest.raises(MandateNotFound):
            await revoke_mandate(s, "m_missing", NOW)


async def test_list_mandates_oldest_first(sessions):
    await _create(sessions, mandate_id="m_b", created_at=NOW + timedelta(minutes=1))
    await _create(sessions, mandate_id="m_a")
    async with sessions() as s:
        assert [r.terms.mandate_id for r in await list_mandates(s)] == ["m_a", "m_b"]


# --- database: check_and_reserve / release_budget ------------------------------

PAY_NOW = NOW + timedelta(hours=1)


async def _payment(sessions, status=S.REQUESTED, amount="179.00", created_at=PAY_NOW, **kw):
    async with sessions() as s:
        payment = Payment(
            mandate_id=kw.get("mandate_id", "m_demo"),
            merchant_id="soundhub",
            amount=Decimal(amount),
            status=status,
            created_at=created_at,
        )
        s.add(payment)
        await s.commit()
    return payment.id


async def _reserve(sessions, payment_id, amount="179.00", merchant_id="soundhub", **kw):
    async with sessions() as s:
        decision = await check_and_reserve(
            s,
            payment_id=payment_id,
            request=PaymentRequest(
                amount=Decimal(amount),
                currency="USD",
                category="electronics",
                merchant_id=merchant_id,
            ),
            mandate_id=kw.get("mandate_id", "m_demo"),
            now=kw.get("now", PAY_NOW),
        )
        await s.commit()
    return decision


async def _state(sessions):
    async with sessions() as s:
        record = await get_mandate(s, "m_demo")
        holds = (await s.scalars(select(BudgetHold))).all()
    return record, holds


def _codes(decision):
    return [r.code for r in decision.reasons]


async def test_allow_holds_budget_and_count(sessions):
    await _create(sessions)
    pid = await _payment(sessions)

    decision = await _reserve(sessions, pid)

    assert decision.is_allowed
    record, (hold,) = await _state(sessions)
    assert record.budget_used == Decimal("179.00") and record.payment_count == 1
    assert hold.payment_id == pid and hold.amount == Decimal("179.00")
    assert hold.released_at is None
    # Holding budget changes state only, so the mandate still verifies.
    assert verify_mandate(record).signature_valid


async def test_block_holds_nothing(sessions):
    await _create(sessions)
    pid = await _payment(sessions)

    decision = await _reserve(sessions, pid, merchant_id="megadeals")

    assert _codes(decision) == ["merchant_not_allowed"]
    record, holds = await _state(sessions)
    assert record.budget_used == Decimal("0") and record.payment_count == 0 and holds == []


async def test_unknown_mandate_blocks(sessions):
    pid = await _payment(sessions)
    decision = await _reserve(sessions, pid, mandate_id="m_missing")
    assert _codes(decision) == ["mandate_not_found"]


async def test_tampered_terms_in_database_block(sessions):
    await _create(sessions)
    async with sessions() as s:
        await s.execute(
            update(MandateRow).where(MandateRow.id == "m_demo").values(total_budget=99999)
        )
        await s.commit()
    pid = await _payment(sessions)

    decision = await _reserve(sessions, pid)

    assert "mandate_signature_invalid" in _codes(decision)
    _, holds = await _state(sessions)
    assert holds == []


async def test_revoked_mandate_blocks(sessions):
    await _create(sessions)
    async with sessions() as s:
        await revoke_mandate(s, "m_demo", NOW)
        await s.commit()
    decision = await _reserve(sessions, await _payment(sessions))
    assert _codes(decision) == ["mandate_revoked"]


async def test_held_budget_counts_against_the_next_payment(sessions):
    await _create(sessions)
    assert (await _reserve(sessions, await _payment(sessions))).is_allowed
    assert (await _reserve(sessions, await _payment(sessions))).is_allowed

    decision = await _reserve(sessions, await _payment(sessions))  # 3 x 179 > 500

    assert _codes(decision) == ["budget_exceeded"]
    record, holds = await _state(sessions)
    assert record.budget_used == Decimal("358.00") and len(holds) == 2


async def test_velocity_ignores_ended_old_and_other_mandate_payments(sessions):
    await _create(sessions, max_payment_count=20, total_budget=Decimal("5000.00"))
    await _payment(sessions, status=S.AUTHORIZED, amount="1.00")
    await _payment(sessions, status=S.AWAITING_APPROVAL, amount="1.00")
    for status in (S.BLOCKED, S.DENIED, S.EXPIRED, S.FAILED, S.VOIDED):
        await _payment(sessions, status=status, amount="1.00")
    await _payment(sessions, status=S.CAPTURED, amount="1.00", mandate_id="m_other")
    await _payment(
        sessions, status=S.CAPTURED, amount="1.00", created_at=PAY_NOW - timedelta(seconds=601)
    )

    # Only the 2 live payments count: 2 + this one = 3, exactly at the limit.
    decision = await _reserve(sessions, await _payment(sessions, amount="1.00"), amount="1.00")
    assert decision.is_allowed


async def test_velocity_counts_a_payment_on_the_window_edge(sessions):
    await _create(sessions, max_payment_count=20, total_budget=Decimal("5000.00"))
    await _payment(sessions, status=S.CAPTURED, amount="1.00")
    await _payment(sessions, status=S.CAPTURED, amount="1.00")
    await _payment(
        sessions, status=S.CAPTURED, amount="1.00", created_at=PAY_NOW - timedelta(seconds=600)
    )

    # created_at >= now - window: the edge payment counts, so 3 + this one exceeds 3.
    decision = await _reserve(sessions, await _payment(sessions, amount="1.00"), amount="1.00")
    assert _codes(decision) == ["velocity_limit_exceeded"]


async def test_velocity_ignores_the_payment_being_checked(sessions):
    await _create(sessions)
    await _payment(sessions, status=S.CAPTURED, amount="1.00")
    await _payment(sessions, status=S.CAPTURED, amount="1.00")
    # Two live payments + this one = 3, exactly at the limit.
    assert (await _reserve(sessions, await _payment(sessions))).is_allowed


async def test_second_hold_for_same_payment_raises(sessions):
    await _create(sessions)
    pid = await _payment(sessions)
    assert (await _reserve(sessions, pid)).is_allowed
    with pytest.raises(ValueError, match="already has a budget hold"):
        await _reserve(sessions, pid, amount="1.00")
    record, holds = await _state(sessions)
    assert record.budget_used == Decimal("179.00") and len(holds) == 1


async def test_release_gives_budget_back_once(sessions):
    await _create(sessions)
    pid = await _payment(sessions)
    await _reserve(sessions, pid)

    async with sessions() as s:
        first = await release_budget(s, payment_id=pid, now=PAY_NOW)
        second = await release_budget(s, payment_id=pid, now=PAY_NOW)
        await s.commit()

    assert first is True and second is False
    record, (hold,) = await _state(sessions)
    assert record.budget_used == Decimal("0") and record.payment_count == 0
    assert hold.released_at is not None


async def test_release_with_naive_time_is_refused(sessions):
    await _create(sessions)
    pid = await _payment(sessions)
    await _reserve(sessions, pid)
    async with sessions() as s:
        with pytest.raises(ValueError, match="timezone-aware"):
            await release_budget(s, payment_id=pid, now=datetime(2026, 11, 3))


async def test_release_without_hold_is_false(sessions):
    await _create(sessions)
    async with sessions() as s:
        assert await release_budget(s, payment_id="no-such-payment", now=PAY_NOW) is False


async def test_released_budget_can_be_spent_again(sessions):
    await _create(sessions, total_budget=Decimal("200.00"))
    first = await _payment(sessions)
    await _reserve(sessions, first)
    assert _codes(await _reserve(sessions, await _payment(sessions))) == ["budget_exceeded"]

    async with sessions() as s:
        await release_budget(s, payment_id=first, now=PAY_NOW)
        await s.commit()

    assert (await _reserve(sessions, await _payment(sessions))).is_allowed


async def test_release_refuses_to_go_below_zero(sessions):
    await _create(sessions)
    pid = await _payment(sessions)
    await _reserve(sessions, pid)
    async with sessions() as s:
        await s.execute(
            update(MandateRow).where(MandateRow.id == "m_demo").values(budget_used=0)
        )
        await s.commit()
    async with sessions() as s:
        with pytest.raises(ValueError, match="lower than the hold"):
            await release_budget(s, payment_id=pid, now=PAY_NOW)


# --- Postgres only: the mandate row lock under concurrency ---------------------

PG_TEST_URL = os.environ.get("LEASH_PG_TEST_URL", "")


@pytest.mark.skipif(not PG_TEST_URL, reason="set LEASH_PG_TEST_URL to run against real Postgres")
async def test_concurrent_reserves_cannot_overspend_on_postgres(settings):
    url, needs_ssl = to_async_url(PG_TEST_URL)
    engine = create_async_engine(url, connect_args={"ssl": True} if needs_ssl else {})
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    mandate_id = f"m_pgtest_{uuid.uuid4().hex[:12]}"
    payment_ids = []
    created = False
    try:
        async with sessions() as s:
            await create_mandate(
                s,
                make_terms(
                    mandate_id=mandate_id,
                    max_payment_amount=Decimal("10.00"),
                    total_budget=Decimal("10.00"),
                    max_payment_count=5,
                    created_at=now,
                    expires_at=now + timedelta(hours=1),
                ),
            )
            await s.commit()
        created = True
        for _ in range(2):
            payment_ids.append(
                await _payment(sessions, amount="6.00", created_at=now, mandate_id=mandate_id)
            )

        # Open both connections first so connecting does not serialize the two calls.
        async with engine.connect(), engine.connect():
            pass
        decisions = await asyncio.gather(
            *(
                _reserve(sessions, pid, amount="6.00", mandate_id=mandate_id, now=now)
                for pid in payment_ids
            )
        )

        assert sorted(d.outcome for d in decisions) == [PolicyOutcome.ALLOW, PolicyOutcome.BLOCK]
        assert [_codes(d) for d in decisions if not d.is_allowed] == [["budget_exceeded"]]
        async with sessions() as s:
            record = await get_mandate(s, mandate_id)
            holds = (
                await s.scalars(select(BudgetHold).where(BudgetHold.mandate_id == mandate_id))
            ).all()
        assert (record.budget_used, record.payment_count) == (Decimal("6.00"), 1)
        assert len(holds) == 1
    finally:
        done = datetime.now(UTC)
        async with sessions() as s:
            # Mandate row locks (release, revoke) before the audit chain lock (CONTRACTS section 3).
            held = set()
            for pid in payment_ids:
                if await s.get(BudgetHold, pid) is not None:
                    held.add(pid)
                # Without the lock both reserves pass and usage ends lower than the holds.
                with suppress(ValueError):
                    await release_budget(s, payment_id=pid, now=done)
            if created:
                await revoke_mandate(s, mandate_id, done)
            for pid in payment_ids:
                payment = await s.get(Payment, pid)
                target = S.FAILED if pid in held else S.BLOCKED
                payment.status = transition(S(payment.status), target)
                await append_audit(
                    s,
                    event_type=f"payment.{target.lower()}",
                    payment_id=pid,
                    payload={"status": target.value},
                    now=done,
                )
            await s.commit()
            chain = await verify_chain(s)
            statuses = sorted(
                await s.scalars(select(Payment.status).where(Payment.mandate_id == mandate_id))
            )
            open_holds = await s.scalar(
                select(func.count()).where(
                    BudgetHold.mandate_id == mandate_id, BudgetHold.released_at.is_(None)
                )
            )
        await engine.dispose()
    assert chain.ok
    assert statuses == [S.BLOCKED, S.FAILED]
    assert open_holds == 0
