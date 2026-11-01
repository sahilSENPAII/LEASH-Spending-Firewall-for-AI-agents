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
