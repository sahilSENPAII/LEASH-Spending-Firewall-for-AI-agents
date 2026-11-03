import json
from decimal import Decimal

import httpx
import pytest
import respx

from app.paypal import PayPalClient, PayPalError

BASE = "https://api-m.sandbox.paypal.com"
TOKEN = {"access_token": "T", "expires_in": 3600}


def _client(router):
    return PayPalClient("id", "secret", BASE, http=httpx.AsyncClient(base_url=BASE))


def _authorized_order(order_id="O-1", auth_id="A-1"):
    return {
        "id": order_id,
        "status": "COMPLETED",
        "purchase_units": [
            {"payments": {"authorizations": [{"id": auth_id, "status": "CREATED"}]}}
        ],
    }


@respx.mock
async def test_authorize_sends_vault_id_and_idempotency_key_without_payee():
    token = respx.post(f"{BASE}/v1/oauth2/token").respond(200, json=TOKEN)
    order = respx.post(f"{BASE}/v2/checkout/orders").respond(201, json=_authorized_order())

    pp = _client(respx)
    auth = await pp.authorize(
        vault_id="VAULT", amount=Decimal("1"), request_id="REQ-1", custom_id="soundhub"
    )

    assert auth.order_id == "O-1" and auth.authorization_id == "A-1"
    sent = order.calls.last.request
    assert sent.headers["PayPal-Request-Id"] == "REQ-1"
    body = sent.read().decode()
    assert '"vault_id":"VAULT"' in body.replace(" ", "")
    assert '"value":"1.00"' in body.replace(" ", "")
    assert "payee" not in body  # D10: funds settle to our own merchant account
    assert token.call_count == 1


@respx.mock
async def test_token_is_cached_between_calls():
    token = respx.post(f"{BASE}/v1/oauth2/token").respond(200, json=TOKEN)
    respx.post(f"{BASE}/v2/payments/authorizations/A-1/capture").respond(
        201, json={"status": "COMPLETED"}
    )
    respx.post(f"{BASE}/v2/payments/authorizations/A-1/void").respond(204)

    pp = _client(respx)
    assert await pp.capture("A-1", "R-1") == "COMPLETED"
    await pp.void("A-1", "R-2")
    assert token.call_count == 1


@respx.mock
async def test_created_order_is_authorized_explicitly():
    respx.post(f"{BASE}/v1/oauth2/token").respond(200, json=TOKEN)
    respx.post(f"{BASE}/v2/checkout/orders").respond(201, json={"id": "O-2", "status": "CREATED"})
    auth_call = respx.post(f"{BASE}/v2/checkout/orders/O-2/authorize").respond(
        201, json=_authorized_order("O-2", "A-2")
    )

    pp = _client(respx)
    auth = await pp.authorize(vault_id="V", amount=Decimal("5.5"), request_id="REQ-2")
    assert auth.authorization_id == "A-2"
    assert auth_call.calls.last.request.headers["PayPal-Request-Id"] == "REQ-2-auth"


@respx.mock
async def test_paypal_error_carries_status_and_body():
    respx.post(f"{BASE}/v1/oauth2/token").respond(200, json=TOKEN)
    respx.post(f"{BASE}/v2/checkout/orders").respond(
        422, json={"details": [{"issue": "BILLING_AGREEMENT_NOT_FOUND"}]}
    )
    pp = _client(respx)
    with pytest.raises(PayPalError) as err:
        await pp.authorize(vault_id="V", amount=Decimal("1"), request_id="R")
    assert err.value.status == 422
    assert err.value.action == "create_order"


@respx.mock
async def test_find_order_replays_the_authorize_body_and_never_authorizes():
    respx.post(f"{BASE}/v1/oauth2/token").respond(200, json=TOKEN)
    create = respx.post(f"{BASE}/v2/checkout/orders").respond(200, json=_authorized_order())
    respx.get(f"{BASE}/v2/checkout/orders/O-1").respond(200, json=_authorized_order())
    authorize = respx.post(f"{BASE}/v2/checkout/orders/O-1/authorize").respond(201, json={})
    args = {
        "vault_id": "V",
        "amount": Decimal("179.00"),
        "request_id": "KEY",
        "custom_id": "soundhub",
        "description": "Sony WH-1000XM5",
    }

    pp = _client(respx)
    await pp.authorize(**args)
    lookup = await pp.find_order(**args)

    original, replay = (call.request for call in create.calls)
    assert json.loads(replay.content) == json.loads(original.content)
    assert replay.headers["PayPal-Request-Id"] == "KEY"
    assert not authorize.called
    assert (lookup.order_id, lookup.authorization_id) == ("O-1", "A-1")
    assert lookup.created is False


@respx.mock
async def test_find_order_reads_an_authorization_added_after_the_order_was_created():
    respx.post(f"{BASE}/v1/oauth2/token").respond(200, json=TOKEN)
    respx.post(f"{BASE}/v2/checkout/orders").respond(200, json={"id": "O-1", "status": "CREATED"})
    respx.get(f"{BASE}/v2/checkout/orders/O-1").respond(200, json=_authorized_order())

    lookup = await _client(respx).find_order(vault_id="V", amount=Decimal("1"), request_id="R")
    assert lookup.authorization_id == "A-1"


@respx.mock
async def test_find_order_reports_a_new_order_without_a_hold():
    respx.post(f"{BASE}/v1/oauth2/token").respond(200, json=TOKEN)
    order = {"id": "O-9", "status": "CREATED", "purchase_units": [{}]}
    respx.post(f"{BASE}/v2/checkout/orders").respond(201, json=order)
    respx.get(f"{BASE}/v2/checkout/orders/O-9").respond(200, json=order)

    lookup = await _client(respx).find_order(vault_id="V", amount=Decimal("1"), request_id="R")
    assert lookup.created is True and lookup.authorization_id is None


@respx.mock
@pytest.mark.parametrize(
    "current",
    [
        {"id": "O-1", "status": "COMPLETED", "purchase_units": [{}]},
        {"id": "O-1", "status": "CREATED", "purchase_units": []},
    ],
)
async def test_find_order_refuses_an_order_it_cannot_read(current):
    respx.post(f"{BASE}/v1/oauth2/token").respond(200, json=TOKEN)
    respx.post(f"{BASE}/v2/checkout/orders").respond(200, json={"id": "O-1"})
    respx.get(f"{BASE}/v2/checkout/orders/O-1").respond(200, json=current)

    with pytest.raises((PayPalError, IndexError)):
        await _client(respx).find_order(vault_id="V", amount=Decimal("1"), request_id="R")


@respx.mock
async def test_find_order_raises_when_the_order_read_fails():
    respx.post(f"{BASE}/v1/oauth2/token").respond(200, json=TOKEN)
    respx.post(f"{BASE}/v2/checkout/orders").respond(200, json={"id": "O-1"})
    respx.get(f"{BASE}/v2/checkout/orders/O-1").respond(500, json={})

    with pytest.raises(PayPalError) as err:
        await _client(respx).find_order(vault_id="V", amount=Decimal("1"), request_id="R")
    assert err.value.action == "get_order"


@respx.mock
async def test_authorization_status_returns_status_and_raises_on_error():
    respx.post(f"{BASE}/v1/oauth2/token").respond(200, json=TOKEN)
    respx.get(f"{BASE}/v2/payments/authorizations/A-1").respond(200, json={"status": "CAPTURED"})
    respx.get(f"{BASE}/v2/payments/authorizations/A-2").respond(404, json={})

    pp = _client(respx)
    assert await pp.authorization_status("A-1") == "CAPTURED"
    with pytest.raises(PayPalError) as err:
        await pp.authorization_status("A-2")
    assert err.value.action == "get_authorization"
