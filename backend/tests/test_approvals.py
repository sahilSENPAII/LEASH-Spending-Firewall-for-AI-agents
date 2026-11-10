import asyncio
import dataclasses
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_orchestrator import KEY_B64, FakeJudge, FakeModel, FakePayPal

from app import approvals, mandates, orchestrator
from app.audit import verify_chain
from app.catalog import DEFAULT_CATALOG_PATH, load_catalog
from app.config import Settings
from app.main import app
from app.mandates import MandateTerms, create_mandate, get_mandate, revoke_mandate
from app.models import AuditEvent, Base, BudgetHold, Payment
from app.paypal import OrderLookup, PayPalError
from app.state import PaymentStatus
from scripts import seed_video_mandate
from scripts.seed_demo_mandate import DEMO_TERMS

S = PaymentStatus
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
TTL = timedelta(seconds=900)


async def _seed(sessions):
    async with sessions() as s:
        await create_mandate(s, MandateTerms(**DEMO_TERMS, created_at=NOW - timedelta(days=1)))
        await s.commit()


@pytest.fixture
async def env(sessions, monkeypatch):
    """The approval routes on the test database: a judge that says mismatch (so every payment is
    a STEP_UP), fake PayPal, and a clock one minute after the request."""
    settings = Settings(
        _env_file=None,
        mandate_signing_key=KEY_B64,
        paypal_vault_id="VAULT",
        public_base_url="https://leash.test/",
        approval_ttl_seconds=900,
    )
    env = SimpleNamespace(
        sessions=sessions, paypal=FakePayPal(), now=NOW + timedelta(minutes=1), client=None
    )
    for module in (mandates, orchestrator, approvals):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(orchestrator, "score_anomaly", FakeModel())
    monkeypatch.setattr(orchestrator, "judge_intent", FakeJudge(match=False))
    catalog = load_catalog(DEFAULT_CATALOG_PATH)
    monkeypatch.setattr(orchestrator, "get_catalog", lambda: catalog)
    monkeypatch.setattr(approvals, "get_catalog", lambda: catalog)
    monkeypatch.setattr(approvals, "get_sessionmaker", lambda: env.sessions)
    monkeypatch.setattr(approvals, "get_paypal", lambda: env.paypal)
    monkeypatch.setattr(approvals, "_now", lambda: env.now)
    await _seed(sessions)
    env.client = TestClient(app)
    return env


async def _step_up(sessions, now=NOW, mandate_id="m_demo", sku="sh-sony-xm5"):
    """One STEP_UP payment. Returns (response, page path, raw token)."""
    result = await orchestrator.request_payment(
        sessions,
        FakePayPal(),
        "VAULT",
        mandate_id=mandate_id,
        merchant_id="soundhub",
        items=[{"sku": sku, "quantity": 1}],
        now=now,
    )
    assert result["decision"] == "STEP_UP" and result["status"] == S.AWAITING_APPROVAL
    url = urlsplit(result["approval_url"])
    return result, url.path, parse_qs(url.query)["token"][0]


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
        assert (await verify_chain(s)).ok
    return payment, [e.event_type for e in events], hold, record.budget_used


def _post(env, path, action, token):
    return env.client.post(f"{path}/{action}", data={"token": token})


async def test_step_up_returns_a_working_link_and_the_token_is_stored_nowhere(env):
    result, path, token = await _step_up(env.sessions)

    assert (
        result["approval_url"]
        == f"https://leash.test/approvals/{result['payment_id']}?token={token}"
    )
    assert result["approval_expires_at"] == "2026-11-04T12:15:00Z"
    assert env.client.get(result["approval_url"]).status_code == 200

    async with env.sessions() as s:
        payment = await s.get(Payment, result["payment_id"])
        events = (await s.scalars(select(AuditEvent))).all()
    assert payment.approval_token_hash == hashlib.sha256(token.encode()).hexdigest()
    stored = [getattr(payment, c.name) for c in Payment.__table__.columns]
    stored += [e.payload for e in events]
    assert token not in repr(stored)
    assert events[-1].event_type == "approval.requested"
    assert events[-1].payload["approval_expires_at"] == "2026-11-04T12:15:00Z"


async def test_page_shows_the_cart_and_reasons_and_changes_nothing(env):
    result, path, token = await _step_up(env.sessions)
    before = await _state(env.sessions, result["payment_id"])

    page = env.client.get(path, params={"token": token})
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    for text in (
        "soundhub",
        "1 &times; Sony WH-1000XM5",
        "$179.00",
        "Sony or Bose noise-cancelling headphones",
        "Intent check: Gift cards are not headphones.",
        "Risk score: 0.050000",
        "Risk signal: amount $179.00",
        'Expires at <time datetime="2026-11-04T12:15:00Z">12:15 UTC</time>',
        "toLocaleTimeString",
        f'action="{path}/approve"',
        f'action="{path}/deny"',
        f'name="token" value="{token}"',
    ):
        assert text in page.text

    # Each reason once: the judge's sentence is in the reasons, so Checks does not repeat it.
    assert page.text.count("Gift cards are not headphones.") == 1
    assert page.text.count("amount $179.00") == 1

    # Past the expiry the page only says so; expiring is the job of a POST or the expiry run.
    env.now = NOW + TTL
    late = env.client.get(path, params={"token": token})
    assert "EXPIRED" in late.text and "<form" not in late.text

    after = await _state(env.sessions, result["payment_id"])
    assert after[0].status == S.AWAITING_APPROVAL and after[0].updated_at == before[0].updated_at
    assert (after[1], after[2].released_at, after[3]) == (before[1], None, before[3])


async def test_page_lists_a_risk_signal_once_when_it_caused_the_step_up(env, monkeypatch):
    monkeypatch.setattr(orchestrator, "score_anomaly", FakeModel(0.8))
    monkeypatch.setattr(orchestrator, "judge_intent", FakeJudge(match=True))
    result, path, token = await _step_up(env.sessions)

    page = env.client.get(path, params={"token": token}).text
    assert "Risk score 0.80: amount $179.00." in page and "Risk score: 0.800000" in page
    assert page.count("amount $179.00") == 1


@pytest.mark.parametrize("token", ["", "wrong", None])
async def test_wrong_or_missing_token_is_a_bare_404(env, token):
    result, path, good = await _step_up(env.sessions)
    params = {} if token is None else {"token": token}
    data = None if token is None else {"token": token}

    responses = [
        env.client.get(path, params=params),
        env.client.post(f"{path}/approve", data=data),
        env.client.post(f"{path}/deny", data=data),
        env.client.get("/approvals/no-such-payment", params={"token": good}),
        env.client.post("/approvals/no-such-payment/approve", data={"token": good}),
    ]
    assert [r.status_code for r in responses] == [404] * 5
    assert all(r.json() == {"detail": "Not Found"} for r in responses)
    payment, events, hold, used = await _state(env.sessions, result["payment_id"])
    assert payment.status == S.AWAITING_APPROVAL and payment.approval_decided_at is None
    assert events[-1] == "approval.requested" and env.paypal.calls == []


async def test_approve_pays_with_the_same_idempotency_key(env):
    result, path, token = await _step_up(env.sessions)

    page = _post(env, path, "approve", token)
    assert page.status_code == 200 and "CAPTURED" in page.text and "<form" not in page.text

    payment, events, hold, used = await _state(env.sessions, result["payment_id"])
    assert payment.status == S.CAPTURED and payment.approval_decision == "approved"
    assert payment.approval_decided_at is not None
    assert events[-4:] == [
        "approval.requested",
        "approval.approved",
        "payment.authorized",
        "payment.captured",
    ]
    (_, auth), capture = env.paypal.calls
    assert auth["request_id"] == payment.idempotency_key and auth["vault_id"] == "VAULT"
    assert auth["amount"] == Decimal("179.00") and auth["custom_id"] == "soundhub"
    assert capture == ("capture", "A-1", f"{payment.idempotency_key}-capture")
    assert hold.released_at is None and used == Decimal("179.00")


async def test_approve_when_capture_fails_voids_and_releases_the_budget(env):
    result, path, token = await _step_up(env.sessions)
    env.paypal = FakePayPal(capture_status=PayPalError("capture", 500, {}))

    assert "VOIDED" in _post(env, path, "approve", token).text

    payment, events, hold, used = await _state(env.sessions, result["payment_id"])
    assert payment.status == S.VOIDED
    assert events[-3:] == ["approval.approved", "payment.authorized", "payment.voided"]
    assert [c[0] for c in env.paypal.calls] == ["authorize", "capture", "void"]
    assert hold.released_at is not None and used == Decimal("0.00")


async def test_approve_when_authorize_fails_is_failed_and_releases_the_budget(env):
    result, path, token = await _step_up(env.sessions)
    env.paypal = FakePayPal(authorize_error=PayPalError("authorize", 500, {}))

    assert "FAILED" in _post(env, path, "approve", token).text

    payment, events, hold, used = await _state(env.sessions, result["payment_id"])
    assert payment.status == S.FAILED and events[-1] == "payment.failed"
    assert hold.released_at is not None and used == Decimal("0.00")


async def test_deny_releases_the_budget_and_never_calls_paypal(env):
    result, path, token = await _step_up(env.sessions)

    assert "DENIED" in _post(env, path, "deny", token).text

    payment, events, hold, used = await _state(env.sessions, result["payment_id"])
    assert payment.status == S.DENIED and payment.approval_decision == "denied"
    assert events[-1] == "approval.denied" and env.paypal.calls == []
    assert hold.released_at is not None and used == Decimal("0.00")
    # The link still opens, and now shows the result.
    assert "DENIED" in env.client.get(path, params={"token": token}).text


@pytest.mark.parametrize("action", ["approve", "deny"])
async def test_answer_after_expiry_is_expired_and_not_paid(env, action):
    result, path, token = await _step_up(env.sessions)
    env.now = NOW + TTL

    assert "EXPIRED" in _post(env, path, action, token).text

    payment, events, hold, used = await _state(env.sessions, result["payment_id"])
    assert payment.status == S.EXPIRED and payment.approval_decision == "expired"
    assert events[-1] == "approval.expired" and env.paypal.calls == []
    assert hold.released_at is not None and used == Decimal("0.00")


async def test_second_answer_does_nothing(env):
    result, path, token = await _step_up(env.sessions)

    first = _post(env, path, "approve", token)
    again = _post(env, path, "approve", token)
    denied = _post(env, path, "deny", token)

    assert all("CAPTURED" in r.text for r in (first, again, denied))
    assert [c[0] for c in env.paypal.calls] == ["authorize", "capture"]
    payment, events, hold, used = await _state(env.sessions, result["payment_id"])
    assert payment.status == S.CAPTURED and events.count("approval.approved") == 1
    assert "approval.denied" not in events and hold.released_at is None


async def test_approve_after_the_mandate_is_revoked_is_not_paid(env):
    result, path, token = await _step_up(env.sessions)
    async with env.sessions() as s:
        await revoke_mandate(s, "m_demo", NOW + timedelta(seconds=30))
        await s.commit()

    page = _post(env, path, "approve", token)
    assert "DENIED" in page.text and "The mandate was revoked" in page.text

    payment, events, hold, used = await _state(env.sessions, result["payment_id"])
    assert payment.status == S.DENIED and payment.approval_decision == "denied"
    assert payment.reasons[-1] == approvals.MANDATE_INVALID
    assert events[-1] == "approval.denied" and env.paypal.calls == []
    assert hold.released_at is not None and used == Decimal("0.00")


async def test_expire_due_approvals_expires_only_due_ones_once(env):
    answered, path, token = await _step_up(env.sessions)
    assert "DENIED" in _post(env, path, "deny", token).text
    old, _, _ = await _step_up(env.sessions)
    new, _, _ = await _step_up(env.sessions, now=NOW + timedelta(minutes=10))

    assert await approvals.expire_due_approvals(env.sessions, NOW + TTL - timedelta(seconds=1)) == 0
    assert await approvals.expire_due_approvals(env.sessions, NOW + TTL + timedelta(minutes=1)) == 1
    assert await approvals.expire_due_approvals(env.sessions, NOW + TTL + timedelta(minutes=1)) == 0

    payment, events, hold, used = await _state(env.sessions, old["payment_id"])
    assert payment.status == S.EXPIRED and payment.approval_decision == "expired"
    assert events.count("approval.expired") == 1 and hold.released_at is not None
    payment, events, hold, used = await _state(env.sessions, new["payment_id"])
    assert payment.status == S.AWAITING_APPROVAL and hold.released_at is None
    assert used == Decimal("179.00")
    payment, events, _, _ = await _state(env.sessions, answered["payment_id"])
    assert payment.status == S.DENIED and "approval.expired" not in events


async def test_expiry_loop_runs_at_once_survives_errors_and_cancels_cleanly(env, monkeypatch):
    monkeypatch.setattr(approvals, "EXPIRY_INTERVAL_SECONDS", 0)
    calls = []
    second = asyncio.Event()

    async def expire(sessions, now):
        calls.append((sessions, now))
        if len(calls) == 1:
            raise RuntimeError("database is down")
        second.set()

    async def settle(sessions, paypal, vault_id, now):
        calls.append((sessions, now))
        assert (paypal, vault_id) == (env.paypal, "VAULT")

    monkeypatch.setattr(approvals, "expire_due_approvals", expire)
    monkeypatch.setattr(approvals, "settle_stuck_approvals", settle)
    task = asyncio.create_task(approvals.expiry_loop())
    await asyncio.wait_for(second.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Expire fails, settle still runs, then the next pass starts.
    assert calls[0] == calls[1] == calls[2] == (env.sessions, env.now)


async def test_pending_approval_survives_a_restart(env, tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'leash.db'}"
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    first = async_sessionmaker(engine, expire_on_commit=False)
    await _seed(first)
    result, path, token = await _step_up(first)
    await engine.dispose()

    # What a new process has: a fresh engine and app on the same database, and the link.
    engine = create_async_engine(url)
    env.sessions = async_sessionmaker(engine, expire_on_commit=False)
    fresh_app = FastAPI()
    fresh_app.include_router(approvals.router)
    env.client = TestClient(fresh_app)
    try:
        assert "Approve" in env.client.get(path, params={"token": token}).text
        assert "CAPTURED" in _post(env, path, "approve", token).text
        payment, events, hold, used = await _state(env.sessions, result["payment_id"])
        assert payment.status == S.CAPTURED and events[-1] == "payment.captured"
        assert [c[0] for c in env.paypal.calls] == ["authorize", "capture"]
    finally:
        await engine.dispose()


async def test_page_escapes_a_malicious_item_name(env, monkeypatch):
    catalog = load_catalog(DEFAULT_CATALOG_PATH)
    evil = '<script>alert("x")</script>'
    catalog["sh-sony-xm5"] = dataclasses.replace(catalog["sh-sony-xm5"], name=evil)
    monkeypatch.setattr(orchestrator, "get_catalog", lambda: catalog)
    monkeypatch.setattr(approvals, "get_catalog", lambda: catalog)
    result, path, token = await _step_up(env.sessions)

    page = env.client.get(path, params={"token": token}).text
    # The page's own time script is the only <script>; the name never appears unescaped.
    assert evil not in page and page.count("<script>") == 1
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in page


def test_access_log_never_shows_the_token():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "GET", "/approvals/p-1?token=s3cret-token&x=1", "1.1", 200),
        None,
    )
    assert logging.getLogger("uvicorn.access").filter(record)
    assert record.args[2] == "/approvals/p-1?token=[redacted]&x=1"
    assert "s3cret-token" not in record.getMessage()


async def test_video_mandate_seed_is_idempotent_and_the_speaker_reaches_the_judge(
    env, monkeypatch, capsys
):
    monkeypatch.setattr(seed_video_mandate, "get_sessionmaker", lambda: env.sessions)
    await seed_video_mandate.main()
    await seed_video_mandate.main()
    out = capsys.readouterr().out
    assert out.count("created m_video") == 1 and "already exists" in out

    async with env.sessions() as s:
        record = await get_mandate(s, "m_video")
        assert (await verify_chain(s)).ok
    assert mandates.verify_mandate(record).signature_valid
    assert (record.terms.total_budget, record.terms.max_payment_count) == (Decimal("1000.00"), 10)
    assert record.terms.intent == DEMO_TERMS["intent"]

    # The speaker passes the cart and policy checks, so only the judge stands in its way.
    result, path, token = await _step_up(
        env.sessions, now=datetime.now(UTC), mandate_id="m_video", sku="sh-jbl-flip6"
    )
    assert [r["code"] for r in result["reasons"]] == ["intent_mismatch"]
    env.now = datetime.now(UTC)
    page = env.client.get(path, params={"token": token}).text
    assert "1 &times; JBL Flip 6 Bluetooth Speaker" in page and "$129.00" in page


class LookupPayPal(FakePayPal):
    """FakePayPal that also answers the idempotency-key lookup (PayPalClient.find_order)."""

    def __init__(self, lookup, **kwargs):
        super().__init__(**kwargs)
        self.lookup = lookup

    async def find_order(self, **kwargs):
        self.calls.append(("find_order", kwargs))
        return self.lookup


async def _approved_then_crashed(env, age=timedelta(seconds=300)):
    """A payment as an approve request leaves it when it dies before recording PayPal's answer."""
    result, _, _ = await _step_up(env.sessions)
    async with env.sessions() as s:
        payment = await s.get(Payment, result["payment_id"])
        payment.approval_decision = "approved"
        payment.approval_decided_at = env.now - age
        await s.commit()
    return result["payment_id"]


async def _settle(env):
    return await approvals.settle_stuck_approvals(env.sessions, env.paypal, "VAULT", env.now)


async def test_stuck_approval_with_an_order_at_paypal_is_captured_without_a_second_authorize(env):
    payment_id = await _approved_then_crashed(env)
    env.paypal = LookupPayPal(OrderLookup(order_id="O-9", authorization_id="A-9", created=False))

    assert await _settle(env) == 1
    assert await _settle(env) == 0

    payment, events, hold, used = await _state(env.sessions, payment_id)
    assert payment.status == S.CAPTURED
    assert (payment.paypal_order_id, payment.paypal_authorization_id) == ("O-9", "A-9")
    assert events[-2:] == ["payment.authorized", "payment.captured"]
    (_, lookup), capture = env.paypal.calls
    assert lookup["request_id"] == payment.idempotency_key and lookup["vault_id"] == "VAULT"
    assert lookup["amount"] == Decimal("179.00") and lookup["description"] == payment.items
    assert capture == ("capture", "A-9", f"{payment.idempotency_key}-capture")
    assert hold.released_at is None and used == Decimal("179.00")


async def test_stuck_approval_whose_capture_fails_is_voided_and_released(env):
    payment_id = await _approved_then_crashed(env)
    env.paypal = LookupPayPal(
        OrderLookup(order_id="O-9", authorization_id="A-9", created=False),
        capture_status=PayPalError("capture", 500, {}),
    )

    assert await _settle(env) == 1

    payment, events, hold, used = await _state(env.sessions, payment_id)
    assert payment.status == S.VOIDED and events[-1] == "payment.voided"
    assert [c[0] for c in env.paypal.calls] == ["find_order", "capture", "void"]
    assert hold.released_at is not None and used == Decimal("0.00")


@pytest.mark.parametrize("lookup_hold", [None, "A-new"])
async def test_stuck_approval_with_no_order_at_paypal_is_failed_and_released(env, lookup_hold):
    payment_id = await _approved_then_crashed(env)
    env.paypal = LookupPayPal(
        OrderLookup(order_id="O-new", authorization_id=lookup_hold, created=True)
    )

    assert await _settle(env) == 1
    assert await _settle(env) == 0

    payment, events, hold, used = await _state(env.sessions, payment_id)
    assert payment.status == S.FAILED and payment.reasons == [approvals.NOT_COMPLETED]
    assert payment.reasons[0]["code"] == "approval_not_completed"
    assert events[-1] == "payment.failed" and payment.paypal_authorization_id is None
    # A hold placed by the lookup itself is given back; nothing is authorized or captured.
    expected = ["find_order"] + (["void"] if lookup_hold else [])
    assert [c[0] for c in env.paypal.calls] == expected
    assert hold.released_at is not None and used == Decimal("0.00")


async def test_fresh_and_unanswered_approvals_are_not_settled(env):
    fresh = await _approved_then_crashed(env, age=timedelta(seconds=299))
    waiting, _, _ = await _step_up(env.sessions)
    env.paypal = LookupPayPal(OrderLookup(order_id="O-9", authorization_id="A-9", created=False))

    assert await _settle(env) == 0

    assert env.paypal.calls == []
    for payment_id in (fresh, waiting["payment_id"]):
        payment, events, hold, _ = await _state(env.sessions, payment_id)
        assert payment.status == S.AWAITING_APPROVAL and hold.released_at is None
        assert events[-1] == "approval.requested"


async def test_stuck_approval_is_left_alone_when_paypal_cannot_be_asked(env):
    payment_id = await _approved_then_crashed(env)

    class Down(FakePayPal):
        async def find_order(self, **kwargs):
            raise PayPalError("create_order", 503, {})

    env.paypal = Down()
    assert await _settle(env) == 0
    assert await approvals.settle_stuck_approvals(env.sessions, env.paypal, "", env.now) == 0

    payment, events, hold, _ = await _state(env.sessions, payment_id)
    assert payment.status == S.AWAITING_APPROVAL and hold.released_at is None


async def test_step_up_from_before_approvals_existed_is_expired_and_released(env):
    result, path, token = await _step_up(env.sessions)
    async with env.sessions() as s:
        payment = await s.get(Payment, result["payment_id"])
        payment.approval_token_hash = payment.approval_expires_at = None
        await s.commit()

    assert await approvals.expire_due_approvals(env.sessions, NOW) == 1
    assert await approvals.expire_due_approvals(env.sessions, NOW) == 0

    payment, events, hold, used = await _state(env.sessions, result["payment_id"])
    assert payment.status == S.EXPIRED and payment.approval_decision == "expired"
    assert events[-1] == "approval.expired"
    assert hold.released_at is not None and used == Decimal("0.00")
