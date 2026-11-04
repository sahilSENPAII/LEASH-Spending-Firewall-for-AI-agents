from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from app import mandates, orchestrator, reconcile
from app.anomaly import AnomalyResult
from app.audit import verify_chain
from app.config import Settings
from app.mandates import (
    MandateTerms,
    check_and_reserve,
    create_mandate,
    generate_signing_key,
    get_mandate,
)
from app.models import AuditEvent, BudgetHold, Payment
from app.paypal import Authorization, OrderLookup, PayPalError
from app.policy import PaymentRequest
from app.reconcile import ReconcileResult, reconcile_payments
from app.state import PaymentStatus
from scripts import reconcile_payments as script
from scripts.seed_demo_mandate import DEMO_TERMS

S = PaymentStatus
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
KEY_B64 = generate_signing_key()
ERROR = PayPalError("any", 500, {})


class FakePayPal:
    def __init__(self, status="CREATED", lookup=None, void_error=None, on_status=None):
        self.status = status
        self.lookup = lookup
        self.void_error = void_error
        self.on_status = on_status
        self.calls = []

    async def authorize(self, **kwargs):
        return Authorization(order_id="O-1", authorization_id="A-1", status="CREATED")

    async def capture(self, authorization_id, request_id):
        return "COMPLETED"

    async def find_order(self, **kwargs):
        self.calls.append(("find_order", kwargs))
        if isinstance(self.lookup, Exception):
            raise self.lookup
        return self.lookup

    async def authorization_status(self, authorization_id):
        self.calls.append(("authorization_status", authorization_id))
        if self.on_status:
            await self.on_status()
        if isinstance(self.status, Exception):
            raise self.status
        return self.status

    async def void(self, authorization_id, request_id):
        self.calls.append(("void", authorization_id, request_id))
        if self.void_error:
            raise self.void_error
        self.status = "VOIDED"

    def call_names(self):
        return [call[0] for call in self.calls]


@pytest.fixture
async def sessions(sessions, monkeypatch):
    settings = Settings(
        mandate_signing_key=KEY_B64, velocity_max_payments=10, velocity_window_seconds=600
    )
    monkeypatch.setattr(mandates, "get_settings", lambda: settings)
    async with sessions() as s:
        terms = {**DEMO_TERMS, "max_payment_count": 10}
        await create_mandate(s, MandateTerms(**terms, created_at=NOW - timedelta(days=1)))
        await s.commit()
    return sessions


async def _set_age(sessions, payment_id, age):
    async with sessions() as s:
        await s.execute(
            update(Payment).where(Payment.id == payment_id).values(updated_at=NOW - age)
        )
        await s.commit()


async def _stuck(sessions, status=S.REQUESTED, age=timedelta(minutes=30), auth_id=None):
    """A payment with a real budget hold, as T1 commits it, last changed `age` ago."""
    async with sessions() as s:
        payment = Payment(
            mandate_id="m_demo",
            merchant_id="soundhub",
            amount=Decimal("50.00"),
            currency="USD",
            items="Sony WH-1000XM5",
            status=S.REQUESTED,
            decision="ALLOW",
            created_at=NOW - age,
        )
        s.add(payment)
        await s.flush()
        decision = await check_and_reserve(
            s,
            payment_id=payment.id,
            request=PaymentRequest(
                amount=Decimal("50.00"),
                currency="USD",
                category="electronics",
                merchant_id="soundhub",
            ),
            mandate_id="m_demo",
            now=NOW - age,
        )
        assert decision.is_allowed
        payment.status = status
        if auth_id:
            payment.paypal_order_id = "O-1"
            payment.paypal_authorization_id = auth_id
        await s.commit()
    await _set_age(sessions, payment.id, age)
    return payment


async def _run(sessions, paypal, vault_id="VAULT", minutes=15):
    return await reconcile_payments(
        sessions, paypal, vault_id, older_than=timedelta(minutes=minutes), now=NOW
    )


async def _state(sessions, payment_id):
    async with sessions() as s:
        payment = await s.get(Payment, payment_id)
        events = (
            await s.scalars(
                select(AuditEvent)
                .where(AuditEvent.payment_id == payment_id)
                .order_by(AuditEvent.id)
            )
        ).all()
        hold = await s.get(BudgetHold, payment_id)
        record = await get_mandate(s, "m_demo")
        chain = await verify_chain(s)
    assert chain.ok
    return payment, list(events), hold, record


def _names(events):
    return [e.event_type for e in events if e.event_type.startswith("payment.reconciled")]


async def test_authorized_and_captured_at_paypal_is_marked_captured_and_keeps_its_hold(sessions):
    stuck = await _stuck(sessions, S.AUTHORIZED, auth_id="A-1")
    paypal = FakePayPal(status="CAPTURED")

    (result,) = await _run(sessions, paypal)

    assert result == ReconcileResult(stuck.id, "AUTHORIZED", "CAPTURED", "PayPal: CAPTURED")
    assert paypal.calls == [("authorization_status", "A-1")]
    payment, events, hold, record = await _state(sessions, stuck.id)
    assert payment.status == S.CAPTURED and payment.decision == "ALLOW" and payment.reasons == []
    assert hold.released_at is None
    assert record.budget_used == Decimal("50.00") and record.payment_count == 1
    assert _names(events) == ["payment.reconciled_captured"]
    assert events[-1].payload == {
        "status": "CAPTURED",
        "paypal_status": "CAPTURED",
        "order_id": "O-1",
        "authorization_id": "A-1",
        "order_created_by_lookup": False,
        "void_sent": False,
    }


@pytest.mark.parametrize("held", ["CREATED", "PENDING"])
async def test_authorized_and_still_held_is_voided_and_released(sessions, held):
    stuck = await _stuck(sessions, S.AUTHORIZED, auth_id="A-1")
    paypal = FakePayPal(status=held)

    (result,) = await _run(sessions, paypal)

    assert result.after == "VOIDED"
    assert paypal.calls[-1] == ("void", "A-1", f"{stuck.idempotency_key}-void")
    payment, events, hold, record = await _state(sessions, stuck.id)
    assert payment.status == S.VOIDED and payment.decision == "BLOCK"
    assert payment.reasons == [reconcile.RECONCILED]
    assert hold.released_at is not None
    assert record.budget_used == Decimal("0") and record.payment_count == 0
    assert _names(events) == ["payment.reconciled_voided"]
    assert events[-1].payload["void_sent"] is True


@pytest.mark.parametrize("gone", ["VOIDED", "DENIED"])
async def test_authorized_with_no_hold_left_at_paypal_is_released_without_a_void(sessions, gone):
    stuck = await _stuck(sessions, S.AUTHORIZED, auth_id="A-1")
    paypal = FakePayPal(status=gone)

    (result,) = await _run(sessions, paypal)

    assert result.after == "VOIDED"
    assert "void" not in paypal.call_names()
    payment, events, hold, record = await _state(sessions, stuck.id)
    assert payment.status == S.VOIDED and hold.released_at is not None
    assert record.budget_used == Decimal("0")
    assert events[-1].payload["void_sent"] is False


async def test_failed_void_changes_nothing_and_the_next_run_settles_it(sessions):
    stuck = await _stuck(sessions, S.AUTHORIZED, auth_id="A-1")
    paypal = FakePayPal(status="CREATED", void_error=ERROR)

    (result,) = await _run(sessions, paypal)

    assert result.after is None and "PayPalError" in result.detail
    payment, events, hold, record = await _state(sessions, stuck.id)
    assert payment.status == S.AUTHORIZED and hold.released_at is None
    assert record.budget_used == Decimal("50.00") and _names(events) == []

    paypal.void_error = None
    paypal.status = "VOIDED"
    (result,) = await _run(sessions, paypal)
    assert result.after == "VOIDED"


@pytest.mark.parametrize("status", ["PARTIALLY_CAPTURED", "SOMETHING_NEW", ERROR])
async def test_unclear_paypal_answer_changes_nothing(sessions, status):
    stuck = await _stuck(sessions, S.AUTHORIZED, auth_id="A-1")
    paypal = FakePayPal(status=status)

    (result,) = await _run(sessions, paypal)

    assert result.after is None
    assert "void" not in paypal.call_names()
    payment, events, hold, _ = await _state(sessions, stuck.id)
    assert payment.status == S.AUTHORIZED and hold.released_at is None and _names(events) == []


async def test_authorized_without_an_authorization_id_changes_nothing(sessions):
    stuck = await _stuck(sessions, S.AUTHORIZED)
    paypal = FakePayPal()

    (result,) = await _run(sessions, paypal)

    assert result.after is None and paypal.calls == []
    payment, _, hold, _ = await _state(sessions, stuck.id)
    assert payment.status == S.AUTHORIZED and hold.released_at is None


async def test_requested_lookup_replays_the_original_request(sessions):
    stuck = await _stuck(sessions)
    paypal = FakePayPal(lookup=OrderLookup("O-1", None, created=False))

    await _run(sessions, paypal)

    assert paypal.calls == [
        (
            "find_order",
            {
                "vault_id": "VAULT",
                "amount": Decimal("50.00"),
                "request_id": stuck.idempotency_key,
                "currency": "USD",
                "custom_id": "soundhub",
                "description": "Sony WH-1000XM5",
            },
        )
    ]


async def test_requested_with_a_captured_order_goes_through_authorized_to_captured(sessions):
    stuck = await _stuck(sessions)
    paypal = FakePayPal(status="CAPTURED", lookup=OrderLookup("O-7", "A-7", created=False))

    (result,) = await _run(sessions, paypal)

    assert result.after == "CAPTURED"
    payment, events, hold, record = await _state(sessions, stuck.id)
    assert payment.status == S.CAPTURED
    assert payment.paypal_order_id == "O-7" and payment.paypal_authorization_id == "A-7"
    assert hold.released_at is None and record.budget_used == Decimal("50.00")
    assert _names(events) == [
        "payment.reconciled_lookup",
        "payment.reconciled_authorized",
        "payment.reconciled_captured",
    ]


async def test_requested_with_a_held_order_is_voided_and_released(sessions):
    stuck = await _stuck(sessions)
    paypal = FakePayPal(status="CREATED", lookup=OrderLookup("O-7", "A-7", created=False))

    (result,) = await _run(sessions, paypal)

    assert result.after == "VOIDED"
    assert paypal.calls[-1] == ("void", "A-7", f"{stuck.idempotency_key}-void")
    payment, events, hold, record = await _state(sessions, stuck.id)
    assert payment.status == S.VOIDED and hold.released_at is not None
    assert record.budget_used == Decimal("0")
    assert _names(events) == [
        "payment.reconciled_lookup",
        "payment.reconciled_authorized",
        "payment.reconciled_voided",
    ]


@pytest.mark.parametrize("created", [True, False])
async def test_requested_with_no_hold_at_paypal_is_failed_and_released(sessions, created):
    stuck = await _stuck(sessions)
    paypal = FakePayPal(lookup=OrderLookup("O-9", None, created=created))

    (result,) = await _run(sessions, paypal)

    assert result.after == "FAILED"
    assert paypal.call_names() == ["find_order"]
    payment, events, hold, record = await _state(sessions, stuck.id)
    assert payment.status == S.FAILED and payment.decision == "BLOCK"
    assert hold.released_at is not None and record.budget_used == Decimal("0")
    assert _names(events) == ["payment.reconciled_failed"]
    assert events[-1].payload["order_created_by_lookup"] is created


async def test_hold_placed_by_the_lookup_itself_is_voided_and_the_payment_failed(sessions):
    stuck = await _stuck(sessions)
    paypal = FakePayPal(status="CREATED", lookup=OrderLookup("O-9", "A-9", created=True))

    (result,) = await _run(sessions, paypal)

    assert result.after == "FAILED"
    assert paypal.calls[-1] == ("void", "A-9", f"{stuck.idempotency_key}-void")
    payment, events, hold, record = await _state(sessions, stuck.id)
    assert payment.status == S.FAILED and payment.paypal_authorization_id == "A-9"
    assert hold.released_at is not None and record.budget_used == Decimal("0")
    assert _names(events) == ["payment.reconciled_lookup", "payment.reconciled_failed"]
    assert events[-1].payload["order_created_by_lookup"] is True
    assert events[-1].payload["void_sent"] is True


async def test_hold_that_cannot_be_voided_keeps_its_ids_so_the_next_run_needs_no_lookup(sessions):
    stuck = await _stuck(sessions)
    paypal = FakePayPal(
        status="CREATED", lookup=OrderLookup("O-9", "A-9", created=True), void_error=ERROR
    )

    (result,) = await _run(sessions, paypal)

    assert result.after is None
    payment, events, hold, _ = await _state(sessions, stuck.id)
    assert payment.status == S.REQUESTED and hold.released_at is None
    assert payment.paypal_order_id == "O-9" and payment.paypal_authorization_id == "A-9"
    assert _names(events) == ["payment.reconciled_lookup"]
    assert events[-1].payload == {
        "order_id": "O-9",
        "authorization_id": "A-9",
        "order_created_by_lookup": True,
    }

    await _set_age(sessions, stuck.id, timedelta(minutes=30))
    paypal.void_error = None
    (result,) = await _run(sessions, paypal)

    assert result.after == "VOIDED"
    assert paypal.call_names().count("find_order") == 1
    assert paypal.calls[-1] == ("void", "A-9", f"{stuck.idempotency_key}-void")


async def test_lookup_error_is_not_proof_of_no_order(sessions):
    stuck = await _stuck(sessions)
    paypal = FakePayPal(lookup=ERROR)

    (result,) = await _run(sessions, paypal)

    assert result.after is None
    payment, events, hold, _ = await _state(sessions, stuck.id)
    assert payment.status == S.REQUESTED and hold.released_at is None and _names(events) == []


async def test_requested_past_the_replay_window_never_calls_paypal(sessions):
    stuck = await _stuck(sessions, age=reconcile.REPLAY_WINDOW)
    paypal = FakePayPal()

    (result,) = await _run(sessions, paypal)

    assert result.after is None and "by hand" in result.detail
    assert paypal.calls == []
    payment, _, hold, _ = await _state(sessions, stuck.id)
    assert payment.status == S.REQUESTED and hold.released_at is None


async def test_authorized_past_the_replay_window_is_still_settled(sessions):
    stuck = await _stuck(sessions, S.AUTHORIZED, age=timedelta(hours=8), auth_id="A-1")

    (result,) = await _run(sessions, FakePayPal(status="CAPTURED"))

    assert result.payment_id == stuck.id and result.after == "CAPTURED"


async def test_requested_without_a_vault_id_never_calls_paypal(sessions):
    await _stuck(sessions)
    paypal = FakePayPal()

    (result,) = await _run(sessions, paypal, vault_id="")

    assert result.after is None and paypal.calls == []


async def test_recent_and_other_status_payments_are_left_alone(sessions):
    await _stuck(sessions, S.AUTHORIZED, age=timedelta(minutes=10), auth_id="A-1")
    for status in (S.AWAITING_APPROVAL, S.CAPTURED, S.VOIDED, S.FAILED, S.BLOCKED):
        await _stuck(sessions, status, auth_id="A-1")
    paypal = FakePayPal()

    assert await _run(sessions, paypal) == []
    assert paypal.calls == []


async def test_a_second_run_finds_nothing(sessions):
    await _stuck(sessions, S.AUTHORIZED, auth_id="A-1")
    paypal = FakePayPal()
    await _run(sessions, paypal)
    paypal.calls.clear()

    assert await _run(sessions, paypal) == []
    assert paypal.calls == []


async def test_one_failure_does_not_stop_the_others(sessions):
    bad = await _stuck(sessions, age=timedelta(minutes=40))
    good = await _stuck(sessions, S.AUTHORIZED, auth_id="A-1")
    paypal = FakePayPal(status="CAPTURED", lookup=ERROR)

    results = await _run(sessions, paypal)

    assert {r.payment_id: r.after for r in results} == {bad.id: None, good.id: "CAPTURED"}


async def test_status_change_during_the_paypal_call_wins(sessions):
    stuck = await _stuck(sessions, S.AUTHORIZED, auth_id="A-1")

    async def captured_elsewhere():
        async with sessions() as s:
            await s.execute(update(Payment).where(Payment.id == stuck.id).values(status=S.CAPTURED))
            await s.commit()

    paypal = FakePayPal(status="VOIDED", on_status=captured_elsewhere)

    (result,) = await _run(sessions, paypal)

    assert result.after is None and "moved to CAPTURED" in result.detail
    payment, events, hold, _ = await _state(sessions, stuck.id)
    assert payment.status == S.CAPTURED and hold.released_at is None and _names(events) == []


@pytest.mark.parametrize(
    ("status", "auth_id", "lookup", "expected"),
    [
        (S.AUTHORIZED, "A-1", None, ["release", "audit"]),
        # The first audit is the lookup's own transaction, which takes no mandate lock.
        (
            S.REQUESTED,
            None,
            OrderLookup("O-1", "A-1", created=False),
            ["audit", "release", "audit", "audit"],
        ),
        (S.REQUESTED, None, OrderLookup("O-1", None, created=True), ["release", "audit"]),
    ],
)
async def test_release_comes_before_the_audit_writes_of_its_transaction(
    sessions, monkeypatch, status, auth_id, lookup, expected
):
    await _stuck(sessions, status, auth_id=auth_id)
    order = []

    async def spy_release(*args, **kwargs):
        order.append("release")
        return await mandates.release_budget(*args, **kwargs)

    real_append = reconcile.append_audit

    async def spy_append(*args, **kwargs):
        order.append("audit")
        return await real_append(*args, **kwargs)

    monkeypatch.setattr(reconcile, "release_budget", spy_release)
    monkeypatch.setattr(reconcile, "append_audit", spy_append)

    await _run(sessions, FakePayPal(lookup=lookup))

    assert order == expected


async def test_audit_failure_rolls_back_and_the_next_run_settles_it(sessions, monkeypatch):
    stuck = await _stuck(sessions, S.AUTHORIZED, auth_id="A-1")
    paypal = FakePayPal(status="CREATED")
    real_append = reconcile.append_audit

    async def broken(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(reconcile, "append_audit", broken)
    (result,) = await _run(sessions, paypal)
    assert result.after is None
    payment, _, hold, record = await _state(sessions, stuck.id)
    assert payment.status == S.AUTHORIZED and hold.released_at is None
    assert record.budget_used == Decimal("50.00")

    monkeypatch.setattr(reconcile, "append_audit", real_append)
    (result,) = await _run(sessions, paypal)  # the fake now reports VOIDED
    assert result.after == "VOIDED"
    assert paypal.call_names().count("void") == 1


@pytest.mark.parametrize("minutes", [0, 4])
async def test_short_age_is_refused_so_a_live_payment_is_never_voided(sessions, minutes):
    await _stuck(sessions, S.AUTHORIZED, age=timedelta(minutes=30), auth_id="A-1")
    paypal = FakePayPal()

    with pytest.raises(ValueError):
        await _run(sessions, paypal, minutes=minutes)
    assert paypal.calls == []


async def _crash_when_moving_to(monkeypatch, target):
    """The DB write that records `target` fails after PayPal already moved the money."""
    real_move = orchestrator._move

    async def move(session, payment, to, now, **payload):
        if to == target:
            raise RuntimeError("db write failed")
        return await real_move(session, payment, to, now, **payload)

    monkeypatch.setattr(orchestrator, "_move", move)


async def _pay(sessions, paypal, monkeypatch):
    # A low risk score, so these tests do not depend on the anomaly model.
    low = AnomalyResult(score=0.05, reasons=(), model_version="test")
    monkeypatch.setattr(orchestrator, "score_anomaly", lambda features: low)
    return await orchestrator.request_payment(
        sessions,
        paypal,
        "VAULT",
        mandate_id="m_demo",
        merchant_id="soundhub",
        items=[{"sku": "sh-sony-xm5", "quantity": 1}],
        now=NOW - timedelta(minutes=30),
    )


async def _only_payment(sessions):
    async with sessions() as s:
        return (await s.scalars(select(Payment))).one()


async def test_saga_gap_after_capture_ends_captured(sessions, monkeypatch):
    await _crash_when_moving_to(monkeypatch, S.CAPTURED)
    paypal = FakePayPal(status="CAPTURED")
    assert (await _pay(sessions, paypal, monkeypatch))["decision"] == "BLOCK"
    stuck = await _only_payment(sessions)
    assert stuck.status == S.AUTHORIZED
    await _set_age(sessions, stuck.id, timedelta(minutes=30))

    (result,) = await _run(sessions, paypal)

    assert result.after == "CAPTURED"
    payment, events, hold, _ = await _state(sessions, stuck.id)
    assert payment.status == S.CAPTURED and hold.released_at is None
    assert [e.event_type for e in events] == [
        "payment.decided",
        "payment.decided",
        "payment.authorized",
        "payment.reconciled_captured",
    ]


async def test_saga_gap_after_authorize_ends_voided(sessions, monkeypatch):
    await _crash_when_moving_to(monkeypatch, S.AUTHORIZED)
    paypal = FakePayPal(status="CREATED", lookup=OrderLookup("O-1", "A-1", created=False))
    assert (await _pay(sessions, paypal, monkeypatch))["decision"] == "BLOCK"
    stuck = await _only_payment(sessions)
    assert stuck.status == S.REQUESTED and stuck.paypal_authorization_id is None
    await _set_age(sessions, stuck.id, timedelta(minutes=30))

    (result,) = await _run(sessions, paypal)

    assert result.after == "VOIDED"
    payment, events, hold, record = await _state(sessions, stuck.id)
    assert payment.status == S.VOIDED and hold.released_at is not None
    assert record.budget_used == Decimal("0") and record.payment_count == 0
    assert [e.event_type for e in events] == [
        "payment.decided",
        "payment.decided",
        "payment.reconciled_lookup",
        "payment.reconciled_authorized",
        "payment.reconciled_voided",
    ]


@pytest.mark.parametrize(
    ("after", "exit_code"),
    [("CAPTURED", 0), (None, 1)],
)
async def test_script_passes_age_prints_report_and_sets_exit_code(
    monkeypatch, capsys, after, exit_code
):
    seen = {}

    async def fake_reconcile(sessions, paypal, vault_id, *, older_than):
        seen.update(vault_id=vault_id, older_than=older_than)
        return [ReconcileResult("p-1", "AUTHORIZED", after, "detail")]

    settings = Settings(database_url="postgresql://x", paypal_vault_id="VAULT")
    monkeypatch.setattr(script, "get_settings", lambda: settings)
    monkeypatch.setattr(script, "get_sessionmaker", lambda: None)
    monkeypatch.setattr(script, "reconcile_payments", fake_reconcile)

    assert await script.main(["--older-than-minutes", "20"]) == exit_code
    assert seen == {"vault_id": "VAULT", "older_than": timedelta(minutes=20)}
    out = capsys.readouterr().out
    assert f"p-1  AUTHORIZED -> {after or 'unchanged'}  (detail)" in out


async def test_script_needs_a_database_url(monkeypatch, capsys):
    monkeypatch.setattr(script, "get_settings", lambda: Settings(database_url=""))
    assert await script.main([]) == 1
    assert "DATABASE_URL" in capsys.readouterr().out
