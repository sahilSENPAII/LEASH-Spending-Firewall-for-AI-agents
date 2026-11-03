"""PayPal REST v2 sandbox client: OAuth caching, authorize with a saved wallet, capture, void.

Built from spike 1 (see docs/DECISIONS.md D4, D10). Every write call carries a
PayPal-Request-Id so a retry with the same key returns the original result.
"""

import time
from dataclasses import dataclass
from decimal import Decimal

import httpx

TOKEN_REFRESH_MARGIN_S = 60


class PayPalError(Exception):
    def __init__(self, action: str, status: int, body: object):
        super().__init__(f"PayPal {action} failed: HTTP {status}")
        self.action = action
        self.status = status
        self.body = body


@dataclass(frozen=True)
class Authorization:
    order_id: str
    authorization_id: str
    status: str


@dataclass(frozen=True)
class OrderLookup:
    order_id: str
    authorization_id: str | None  # None: no money is held on this order
    created: bool  # True: PayPal had no order for the key, so the lookup just made this one


class PayPalClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        base_url: str,
        http: httpx.AsyncClient | None = None,
    ):
        self._id = client_id
        self._secret = client_secret
        self._http = http or httpx.AsyncClient(base_url=base_url, timeout=30)
        self._token: str | None = None
        self._token_expires_at = 0.0

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _access_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        r = await self._http.post(
            "/v1/oauth2/token",
            auth=(self._id, self._secret),
            data={"grant_type": "client_credentials"},
        )
        if r.status_code != 200:
            raise PayPalError("oauth", r.status_code, _body(r))
        data = r.json()
        self._token = data["access_token"]
        self._token_expires_at = (
            time.monotonic() + int(data.get("expires_in", 0)) - TOKEN_REFRESH_MARGIN_S
        )
        return self._token

    async def _headers(self, request_id: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {await self._access_token()}",
            "Content-Type": "application/json",
            "PayPal-Request-Id": request_id,
            "Prefer": "return=representation",
        }

    async def _read_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self._access_token()}"}

    async def _create_order(
        self,
        *,
        vault_id: str,
        amount: Decimal,
        request_id: str,
        currency: str,
        custom_id: str,
        description: str,
    ) -> httpx.Response:
        unit = {
            "amount": {"currency_code": currency, "value": f"{amount:.2f}"},
            "custom_id": custom_id[:127],
            "description": description[:127],
        }
        r = await self._http.post(
            "/v2/checkout/orders",
            headers=await self._headers(request_id),
            json={
                "intent": "AUTHORIZE",
                "purchase_units": [unit],
                "payment_source": {"paypal": {"vault_id": vault_id}},
            },
        )
        if r.status_code not in (200, 201):
            raise PayPalError("create_order", r.status_code, _body(r))
        return r

    async def authorize(
        self,
        *,
        vault_id: str,
        amount: Decimal,
        request_id: str,
        currency: str = "USD",
        custom_id: str = "",
        description: str = "",
    ) -> Authorization:
        """Create an AUTHORIZE order charged to a saved wallet (no buyer click) and return the hold.

        No `payee`: funds settle to the app's own merchant account (D10). The store is tracked
        in `custom_id` / our DB.
        """
        r = await self._create_order(
            vault_id=vault_id,
            amount=amount,
            request_id=request_id,
            currency=currency,
            custom_id=custom_id,
            description=description,
        )
        order = r.json()

        if order.get("status") in ("CREATED", "APPROVED"):
            r = await self._http.post(
                f"/v2/checkout/orders/{order['id']}/authorize",
                headers=await self._headers(f"{request_id}-auth"),
                json={},
            )
            if r.status_code not in (200, 201):
                raise PayPalError("authorize_order", r.status_code, _body(r))
            order = r.json()

        try:
            auth = order["purchase_units"][0]["payments"]["authorizations"][0]
        except (KeyError, IndexError) as exc:
            raise PayPalError("authorize_order", 200, order) from exc
        return Authorization(
            order_id=order["id"], authorization_id=auth["id"], status=auth["status"]
        )

    async def find_order(
        self,
        *,
        vault_id: str,
        amount: Decimal,
        request_id: str,
        currency: str = "USD",
        custom_id: str = "",
        description: str = "",
    ) -> OrderLookup:
        """Find the order for an idempotency key by replaying its create call.

        Orders v2 has no search, so this is a write. HTTP 200 is the stored order; 201 means there
        was none and PayPal just made one, which with a saved wallet usually holds money at once.
        The caller must void any hold returned. Never authorizes. PayPal keeps keys for 6 hours;
        after that a replay makes a new order and cannot see the old one.
        """
        r = await self._create_order(
            vault_id=vault_id,
            amount=amount,
            request_id=request_id,
            currency=currency,
            custom_id=custom_id,
            description=description,
        )
        order_id = r.json()["id"]
        # The replay may show the order as first created, before a later authorize call.
        current = await self._http.get(
            f"/v2/checkout/orders/{order_id}", headers=await self._read_headers()
        )
        if current.status_code != 200:
            raise PayPalError("get_order", current.status_code, _body(current))
        order = current.json()
        authorizations = order["purchase_units"][0].get("payments", {}).get("authorizations", [])
        if not authorizations and order.get("status") == "COMPLETED":
            raise PayPalError("get_order", 200, order)
        return OrderLookup(
            order_id=order_id,
            authorization_id=authorizations[0]["id"] if authorizations else None,
            created=r.status_code == 201,
        )

    async def authorization_status(self, authorization_id: str) -> str:
        """The authorization's current status at PayPal, e.g. CREATED, CAPTURED or VOIDED."""
        r = await self._http.get(
            f"/v2/payments/authorizations/{authorization_id}", headers=await self._read_headers()
        )
        if r.status_code != 200:
            raise PayPalError("get_authorization", r.status_code, _body(r))
        return r.json()["status"]

    async def capture(self, authorization_id: str, request_id: str) -> str:
        """Take the held money. Returns the capture status (COMPLETED / PENDING)."""
        r = await self._http.post(
            f"/v2/payments/authorizations/{authorization_id}/capture",
            headers=await self._headers(request_id),
            json={"final_capture": True},
        )
        if r.status_code not in (200, 201):
            raise PayPalError("capture", r.status_code, _body(r))
        return r.json().get("status", "UNKNOWN")

    async def void(self, authorization_id: str, request_id: str) -> None:
        """Release the hold (saga compensation)."""
        r = await self._http.post(
            f"/v2/payments/authorizations/{authorization_id}/void",
            headers=await self._headers(request_id),
        )
        if r.status_code not in (200, 204):
            raise PayPalError("void", r.status_code, _body(r))


def _body(r: httpx.Response) -> object:
    try:
        return r.json()
    except ValueError:
        return r.text[:500]
