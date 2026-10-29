"""Spike 1: can Leash charge a saved PayPal payment later, without the buyer clicking each time?

Flow:
  1. OAuth token (client credentials)
  2. Vault v3 setup token  -> buyer approves once in the browser
  3. Exchange setup token  -> payment token (saved PayPal wallet)
  4. Order A: intent=AUTHORIZE with the vault token, payee = SoundHub -> capture
  5. Order B: same, then VOID the authorization
  6. Idempotency: resend order A's create call with the same PayPal-Request-Id -> must return the same order

Run:  python spikes/01_paypal_vault.py                      # full flow, pays SoundHub
      python spikes/01_paypal_vault.py --vault-id XYZ --no-payee   # reuse saved wallet, pay the app's own account
"""
import argparse
import json
import os
import sys
import uuid
import webbrowser
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
BASE = os.environ["PAYPAL_BASE_URL"]
CID = os.environ["PAYPAL_CLIENT_ID"]
SECRET = os.environ["PAYPAL_CLIENT_SECRET"]
PAYEE = os.environ["MERCHANT_SOUNDHUB_EMAIL"]

ap = argparse.ArgumentParser()
ap.add_argument("--vault-id", help="reuse an existing payment token, skip buyer approval")
ap.add_argument("--no-payee", action="store_true", help="omit payee: funds go to the app's own sandbox business account")
args = ap.parse_args()
if args.no_payee:
    PAYEE = None

client = httpx.Client(base_url=BASE, timeout=30)


def show(label, r):
    print(f"\n--- {label}: HTTP {r.status_code}")
    try:
        body = r.json()
    except ValueError:
        print(r.text[:500])
        return {}
    print(json.dumps(body, indent=2)[:1500])
    return body


def fail(msg):
    print(f"\nFAILED: {msg}")
    sys.exit(1)


# 1. OAuth
r = client.post("/v1/oauth2/token", auth=(CID, SECRET), data={"grant_type": "client_credentials"})
if r.status_code != 200:
    show("oauth", r)
    fail("could not get an access token")
token = r.json()["access_token"]
H = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
print("1. OAuth OK")

if args.vault_id:
    vault_id = args.vault_id
    print(f"2-3. Reusing saved wallet token: {vault_id}")
else:
    # 2. Setup token
    r = client.post(
        "/v3/vault/setup-tokens",
        headers={**H, "PayPal-Request-Id": str(uuid.uuid4())},
        json={
            "payment_source": {
                "paypal": {
                    "usage_type": "MERCHANT",
                    "customer_type": "CONSUMER",
                    "permit_multiple_payment_tokens": False,
                    "experience_context": {
                        "return_url": "https://example.com/leash/return",
                        "cancel_url": "https://example.com/leash/cancel",
                        "brand_name": "Leash",
                        "shipping_preference": "NO_SHIPPING",
                        "vault_instruction": "ON_PAYER_APPROVAL",
                    },
                }
            }
        },
    )
    setup = show("2. setup token", r)
    if r.status_code not in (200, 201):
        fail("setup token rejected -> Vault may not be enabled on this sandbox app (check app settings)")
    approve = next((l["href"] for l in setup.get("links", []) if l.get("rel") == "approve"), None)
    if not approve:
        fail("no approve link returned")

    print("\n>>> Opening the approval page. Log in with the SANDBOX BUYER account and approve.")
    print(f">>> If it doesn't open, paste this into your browser:\n{approve}")
    webbrowser.open(approve)
    input("\n>>> After approving (you'll land on example.com), press Enter here... ")

    # 3. Payment token
    r = client.post(
        "/v3/vault/payment-tokens",
        headers={**H, "PayPal-Request-Id": str(uuid.uuid4())},
        json={"payment_source": {"token": {"id": setup["id"], "type": "SETUP_TOKEN"}}},
    )
    pt = show("3. payment token", r)
    if r.status_code not in (200, 201):
        fail("could not exchange setup token for a payment token")
    vault_id = pt["id"]
    print(f"3. Saved wallet token: {vault_id}")




def create_order(amount, request_id):
    return client.post(
        "/v2/checkout/orders",
        headers={**H, "PayPal-Request-Id": request_id, "Prefer": "return=representation"},
        json={
            "intent": "AUTHORIZE",
            "purchase_units": [{
                "amount": {"currency_code": "USD", "value": amount},
                **({"payee": {"email_address": PAYEE}} if PAYEE else {}),
                "description": "Leash spike",
            }],
            "payment_source": {"paypal": {"vault_id": vault_id}},
        },
    )


def get_authorization(order):
    """With a vault token the order is usually authorized immediately; otherwise authorize it."""
    if order.get("status") in ("CREATED", "APPROVED"):
        r = client.post(f"/v2/checkout/orders/{order['id']}/authorize",
                        headers={**H, "PayPal-Request-Id": str(uuid.uuid4())}, json={})
        order = show("authorize order", r)
    try:
        return order["purchase_units"][0]["payments"]["authorizations"][0]
    except (KeyError, IndexError):
        fail(f"no authorization on order (status={order.get('status')})")


# 4. Order A -> capture
req_a = str(uuid.uuid4())
r = create_order("1.00", req_a)
order_a = show("4. order A (authorize, no buyer click)", r)
if r.status_code not in (200, 201):
    fail("order with vault token rejected -> merchant-initiated charge may not work; payee routing may be the issue")
auth_a = get_authorization(order_a)
r = client.post(f"/v2/payments/authorizations/{auth_a['id']}/capture",
                headers={**H, "PayPal-Request-Id": str(uuid.uuid4())}, json={"final_capture": True})
cap = show("4. capture A", r)
print(f"4. Capture status: {cap.get('status')}")

# 5. Order B -> void
r = create_order("2.00", str(uuid.uuid4()))
order_b = show("5. order B", r)
auth_b = get_authorization(order_b)
r = client.post(f"/v2/payments/authorizations/{auth_b['id']}/void",
                headers={**H, "PayPal-Request-Id": str(uuid.uuid4())})
void_status = r.status_code
print(f"\n5. Void B: HTTP {void_status} (204 = voided)")

# 6. Idempotency
r = create_order("1.00", req_a)
same = r.json().get("id") == order_a["id"]
print(f"\n6. Retry with same PayPal-Request-Id -> same order: {same}")

print("\n=== RESULTS ===")
print(f"Vault charge without buyer click : {'YES' if order_a.get('id') else 'NO'}")
print(f"Payee                            : {order_a['purchase_units'][0].get('payee', {}).get('email_address')}")
print(f"Capture                          : {cap.get('status')}")
print(f"Void                             : {'OK' if void_status == 204 else void_status}")
print(f"Idempotent retry                 : {same}")
