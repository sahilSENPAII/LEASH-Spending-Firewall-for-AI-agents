from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import BigInteger, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app import mcp_server, orchestrator
from app.models import AuditEvent, Base, Payment
from app.paypal import Authorization, PayPalError
from app.state import PaymentStatus

S = PaymentStatus
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


# SQLite only auto-increments INTEGER PRIMARY KEY, so audit_events.id needs INTEGER here.
@compiles(BigInteger, "sqlite")
def _sqlite_bigint(type_, compiler, **kw):
    return "INTEGER"


class FakePayPal:
    def __init__(self, authorize_error=None, capture_status="COMPLETED", void_error=None):
        self.authorize_error = authorize_error
        self.capture_status = capture_status
        self.void_error = void_error
        self.calls = []

    async def authorize(self, **kwargs):
        self.calls.append(("authorize", kwargs))
        if self.authorize_error:
            raise self.authorize_error
        return Authorization(order_id="O-1", authorization_id="A-1", status="CREATED")

    async def capture(self, authorization_id, request_id):
        self.calls.append(("capture", authorization_id, request_id))
        if isinstance(self.capture_status, Exception):
            raise self.capture_status
        return self.capture_status

    async def void(self, authorization_id, request_id):
        self.calls.append(("void", authorization_id, request_id))
        if self.void_error:
            raise self.void_error


@pytest.fixture
async def sessions():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _pay(sessions, paypal, vault_id="VAULT", **overrides):
    args = {
        "mandate_id": "m_demo",
        "merchant_id": "soundhub",
        "amount": "179.00",
        "items": "Sony WH-1000XM5",
        "now": NOW,
    }
    return await orchestrator.request_payment(sessions, paypal, vault_id, **(args | overrides))


async def _rows(sessions):
    async with sessions() as s:
        payments = (await s.scalars(select(Payment))).all()
        events = (await s.scalars(select(AuditEvent).order_by(AuditEvent.id))).all()
    return payments, events


async def _seed(sessions, status, amount, created_at=NOW):
    async with sessions() as s:
        s.add(
            Payment(
                mandate_id="m_demo",
                merchant_id="soundhub",
                amount=Decimal(amount),
                status=status,
                created_at=created_at,
            )
        )
        await s.commit()


def _codes(result):
    return [r["code"] for r in result["reasons"]]


async def test_allow_authorizes_then_captures_with_vault_and_idempotency_key(sessions):
    paypal = FakePayPal()
    result = await _pay(sessions, paypal)

    assert result["decision"] == "ALLOW" and result["reasons"] == []
    assert result["status"] == S.CAPTURED
    (payment,), events = await _rows(sessions)
    assert payment.id == result["payment_id"]
    assert payment.status == S.CAPTURED and payment.decision == "ALLOW"
    assert payment.paypal_order_id == "O-1" and payment.paypal_authorization_id == "A-1"
    assert [e.event_type for e in events] == [
        "payment.decided",
        "payment.authorized",
        "payment.captured",
    ]
    assert all(e.payment_id == payment.id for e in events)

    (_, auth), capture = paypal.calls
    assert auth["vault_id"] == "VAULT"
    assert auth["amount"] == Decimal("179.00")
    assert auth["request_id"] == payment.idempotency_key
    assert auth["custom_id"] == "soundhub"
    assert capture == ("capture", "A-1", f"{payment.idempotency_key}-capture")


async def test_policy_block_never_calls_paypal(sessions):
    paypal = FakePayPal()
    result = await _pay(sessions, paypal, merchant_id="megadeals", amount="250.00")

    assert result["decision"] == "BLOCK" and result["status"] == S.BLOCKED
    assert _codes(result) == ["merchant_not_allowed", "amount_exceeds_max_payment_amount"]
    assert paypal.calls == []
    (payment,), events = await _rows(sessions)
    assert payment.status == S.BLOCKED and payment.decision == "BLOCK"
    assert payment.reasons == result["reasons"]
    assert [e.event_type for e in events] == ["payment.decided", "payment.blocked"]
    assert events[0].payload["reasons"] == result["reasons"]


async def test_unknown_mandate_blocks(sessions):
    paypal = FakePayPal()
    result = await _pay(sessions, paypal, mandate_id="m_other")

    assert result["decision"] == "BLOCK" and _codes(result) == ["invalid_mandate"]
    assert paypal.calls == []


async def test_unknown_merchant_has_no_category_and_blocks(sessions):
    result = await _pay(sessions, FakePayPal(), merchant_id="evilshop")

    assert result["decision"] == "BLOCK"
    assert _codes(result) == ["invalid_request_category", "merchant_not_allowed"]


async def test_budget_counts_held_and_taken_money_only(sessions):
    await _seed(sessions, S.CAPTURED, "200.00", NOW - timedelta(days=1))
    await _seed(sessions, S.AUTHORIZED, "150.00", NOW - timedelta(days=1))
    await _seed(sessions, S.VOIDED, "200.00", NOW - timedelta(days=1))

    assert (await _pay(sessions, FakePayPal(), amount="150.00"))["decision"] == "ALLOW"
    result = await _pay(sessions, FakePayPal(), amount="0.01")
    assert result["decision"] == "BLOCK" and _codes(result) == ["budget_exceeded"]


async def test_payment_count_limit_uses_saved_payments(sessions):
    for _ in range(5):
        await _seed(sessions, S.CAPTURED, "1.00", NOW - timedelta(days=1))

    result = await _pay(sessions, FakePayPal(), amount="1.00")
    assert result["decision"] == "BLOCK" and _codes(result) == ["payment_count_exceeded"]


async def test_velocity_counts_payments_inside_window_only(sessions):
    await _seed(sessions, S.CAPTURED, "1.00", NOW - timedelta(minutes=11))
    await _seed(sessions, S.CAPTURED, "1.00", NOW - timedelta(minutes=5))
    await _seed(sessions, S.CAPTURED, "1.00", NOW - timedelta(minutes=1))

    assert (await _pay(sessions, FakePayPal(), amount="1.00"))["decision"] == "ALLOW"
    result = await _pay(sessions, FakePayPal(), amount="1.00")
    assert result["decision"] == "BLOCK" and _codes(result) == ["velocity_limit_exceeded"]


@pytest.mark.parametrize("amount", ["$179", "", "abc"])
async def test_unparseable_amount_blocks_without_saving(sessions, amount):
    paypal = FakePayPal()
    result = await _pay(sessions, paypal, amount=amount)

    assert result == {
        "payment_id": None,
        "decision": "BLOCK",
        "status": None,
        "reasons": [orchestrator.INVALID_AMOUNT],
    }
    assert paypal.calls == [] and await _rows(sessions) == ([], [])


async def test_authorize_error_marks_failed_and_blocks(sessions):
    paypal = FakePayPal(authorize_error=PayPalError("create_order", 422, {}))
    result = await _pay(sessions, paypal)

    assert result["decision"] == "BLOCK" and result["status"] == S.FAILED
    assert _codes(result) == ["paypal_authorize_failed"]
    assert [c[0] for c in paypal.calls] == ["authorize"]
    (payment,), events = await _rows(sessions)
    assert payment.status == S.FAILED and payment.decision == "BLOCK"
    assert [e.event_type for e in events] == ["payment.decided", "payment.failed"]


async def test_missing_vault_id_blocks_without_calling_paypal(sessions):
    paypal = FakePayPal()
    result = await _pay(sessions, paypal, vault_id="")

    assert result["decision"] == "BLOCK" and result["status"] == S.FAILED
    assert paypal.calls == []


@pytest.mark.parametrize("capture_status", [PayPalError("capture", 500, {}), "DECLINED"])
async def test_capture_failure_voids_the_hold_and_blocks(sessions, capture_status):
    paypal = FakePayPal(capture_status=capture_status)
    result = await _pay(sessions, paypal)

    assert result["decision"] == "BLOCK" and result["status"] == S.VOIDED
    assert _codes(result) == ["paypal_capture_failed"]
    (payment,), events = await _rows(sessions)
    assert paypal.calls[-1] == ("void", "A-1", f"{payment.idempotency_key}-void")
    assert [e.event_type for e in events] == [
        "payment.decided",
        "payment.authorized",
        "payment.voided",
    ]


async def test_void_failure_leaves_hold_authorized_and_blocks(sessions):
    paypal = FakePayPal(
        capture_status=PayPalError("capture", 500, {}), void_error=PayPalError("void", 500, {})
    )
    result = await _pay(sessions, paypal)

    assert result["decision"] == "BLOCK" and result["status"] == S.AUTHORIZED
    (payment,), events = await _rows(sessions)
    assert payment.status == S.AUTHORIZED and payment.decision == "BLOCK"
    assert events[-1].event_type == "payment.error"


async def test_database_error_blocks():
    def broken():
        raise RuntimeError("db down")

    result = await _pay(broken, FakePayPal())
    assert result == orchestrator.internal_error()


async def test_mcp_tool_is_registered():
    tools = {t.name: t for t in await mcp_server.mcp.list_tools()}
    assert set(tools["request_payment"].inputSchema["required"]) == {
        "mandate_id",
        "merchant_id",
        "amount",
        "items",
    }


async def test_mcp_tool_blocks_when_setup_fails(monkeypatch):
    def broken():
        raise RuntimeError("no DATABASE_URL")

    monkeypatch.setattr(mcp_server, "get_sessionmaker", broken)
    result = await mcp_server.request_payment("m_demo", "soundhub", "1.00", "x")
    assert result == orchestrator.internal_error()
