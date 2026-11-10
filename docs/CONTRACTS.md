# Contracts between the money path (Sahil) and the trust path (Anurag)

Status: **agreed** (Nov 2). Anurag's half: sections 1-4. Sahil's half: section 5.

Sahil's orchestrator calls Anurag's modules only through the functions below. Both sides build
against these shapes and stub the other side until it lands. Changing a shape after sign-off needs
a note in `CONTEXT.md` and a reply from the other person.

## Shared conventions
- **Money:** `Decimal`, USD only, at most 2 decimal places. Never `float` (D12).
- **Time:** timezone-aware `datetime`, compared in UTC. The orchestrator passes one `now` per request.
- **Transactions:** every DB function takes the caller's `AsyncSession` and never commits.
  The orchestrator owns the transaction, so the payment row, the budget hold and the audit entry
  commit together or not at all.
- **Fail safe (D3):** these functions either return a result or raise. The orchestrator treats any
  exception as BLOCK (before a hold) or STEP_UP (only where noted). Nothing here ever returns ALLOW
  on error.
- **IDs:** `mandate_id` and `merchant_id` are our own strings (D10), at most 64 chars.

## 1. Mandates — `backend/app/mandates.py` (Anurag)

### What is signed
A mandate's **terms** are signed with Ed25519 at creation; its **state** is not.

| Signed terms (never change) | Unsigned state (changes) |
|---|---|
| `mandate_id`, `intent`, `category`, `max_payment_amount`, `total_budget`, `max_payment_count`, `allowed_merchant_ids`, `expires_at`, `created_at` | `revoked_at`, `budget_used`, `payment_count` |

Signed bytes = canonical JSON of the terms (see "Canonical JSON" below): amounts as strings
(`"200.00"`), times as UTC ISO-8601 with `Z`, `allowed_merchant_ids` as a sorted list.

Demo key handling: the server signs on the user's behalf when the mandate form is submitted, with a
key from env `MANDATE_SIGNING_KEY` (name added to `.env.example` when mandates land). The signature
still proves the terms were not edited in the database afterwards. Production would sign in the
user's browser or with a passkey; recorded as a decision when implemented.

### Functions
```python
async def create_mandate(session, terms: MandateTerms) -> MandateRecord
async def revoke_mandate(session, mandate_id: str, now: datetime) -> MandateRecord
async def list_mandates(session) -> list[MandateRecord]

def verify_mandate(record: MandateRecord) -> MandateCheck
```
```python
@dataclass(frozen=True)
class MandateCheck:
    signature_valid: bool        # Ed25519 over the canonical terms
    mandate: policy.Mandate | None   # None if the record cannot be parsed
    reasons: tuple[PolicyReason, ...]  # empty when signature_valid and mandate parsed
```
- `verify_mandate` checks only the signature and parses the record. Expiry, revocation, budget,
  count and velocity are checked by `evaluate_payment`, so no rule lives in two places.
- `create_mandate` rejects bad terms (the same validation `policy.py` applies) with `ValueError`.
- `revoke_mandate` is idempotent; revoking twice keeps the first `revoked_at`.

Added while building (Nov 3), all in `backend/app/mandates.py`:
- `get_mandate(session, mandate_id) -> MandateRecord | None`, for `/payments` and `list_mandates`.
- `revoke_mandate` raises `MandateNotFound` (a `LookupError`) for an unknown id.
- `create_mandate` raises `ValueError` for bad terms (see `validate_terms`), an existing id, or a
  missing `MANDATE_SIGNING_KEY`. It writes a `mandate.created` audit entry; the first revoke writes
  `mandate.revoked`.
- `MandateRecord` = `terms` (signed), `signature`, `revoked_at`, `budget_used`, `payment_count`.
- `verify_mandate` reasons: `mandate_key_unavailable` (signing key missing or invalid on this server),
  `invalid_mandate` (record unreadable), `mandate_signature_invalid` (terms changed, or a different
  key). Each failure is also logged with its real cause.
- **One key per database.** Every server using the same database must have the same
  `MANDATE_SIGNING_KEY`; under a different key every mandate fails verification and every payment
  blocks.
- Times: pass timezone-aware datetimes; everything is stored in UTC.

## 2. Policy + budget hold — `backend/app/policy.py`, `backend/app/mandates.py` (Anurag)

`evaluate_payment` (exists) is pure and never called directly by the orchestrator. The orchestrator
calls one function that checks **and** holds budget atomically, so two concurrent payments cannot
both pass on the same remaining budget.

```python
async def check_and_reserve(
    session,
    *,
    payment_id: str,          # payments.id, row already inserted by the orchestrator (REQUESTED)
    request: policy.PaymentRequest,
    mandate_id: str,
    now: datetime,
) -> policy.PolicyDecision

async def release_budget(session, *, payment_id: str, now: datetime) -> bool
```

`check_and_reserve`, inside the caller's transaction:
1. `SELECT ... FOR UPDATE` the mandate row (serializes payments on the same mandate).
2. `verify_mandate` → `signature_valid`, `policy.Mandate`.
3. Build `UsageSnapshot`:
   - `budget_used`, `payment_count` from the mandate row (only held or captured payments count).
   - `recent_payment_count` = payments for this mandate with `created_at >= now - window`,
     excluding `payment_id` itself, whose status is **not** in
     `{BLOCKED, DENIED, EXPIRED, FAILED, VOIDED}` (i.e. payments that hold or may hold money).
4. `evaluate_payment(...)` with `VelocityPolicy` from settings.
5. On ALLOW only: add `amount` to `budget_used`, `+1` to `payment_count`, and insert a
   `budget_holds` row `(payment_id, mandate_id, amount, released_at=NULL)`.
6. Return the decision. BLOCK holds nothing.

`release_budget` reverses step 5 for a payment and sets `released_at`. Returns `True` if a hold was
released, `False` if there was none or it was already released (safe to call twice).

Added while building (Nov 3):
- Unknown `mandate_id` returns BLOCK with reason `mandate_not_found` (no exception).
- `check_and_reserve` raises `ValueError` if `payment_id` already has a hold (a retry must reuse
  the first decision, not hold twice). `release_budget` raises `ValueError` if the mandate's usage
  is lower than the hold (inconsistent data).
- A naive `now`: `release_budget` and `revoke_mandate` raise `ValueError`; `check_and_reserve`
  returns BLOCK with `invalid_evaluation_time` (from the policy).
- Velocity limits come from settings `VELOCITY_MAX_PAYMENTS` (3) and `VELOCITY_WINDOW_SECONDS` (600).
- The payment row must be flushed before `check_and_reserve` (the hold references `payments.id`).

**When the orchestrator calls `release_budget`:** a held payment ends in any terminal state other
than `CAPTURED`: `DENIED`, `EXPIRED`, `FAILED`, `VOIDED`, or `BLOCKED` by a later check (judge or
anomaly model). The hold stays during `AWAITING_APPROVAL`, so an approved payment cannot exceed the
budget.

Trusted inputs: `request.category` and `request.merchant_id` must come from Leash's own store
records, not from the agent's text (see the `policy.py` docstring).

## 3. Audit log — `backend/app/audit.py` (Anurag)

Uses Sahil's `audit_events` table (migration 0001). Anurag's code fills `prev_hash` and `hash`.

```python
async def append_audit(
    session, *, event_type: str, payment_id: str | None, payload: dict, now: datetime
) -> AuditEntry

async def verify_chain(session) -> ChainCheck
```
```python
@dataclass(frozen=True)
class AuditEntry:
    id: int
    created_at: datetime
    event_type: str
    payment_id: str | None
    payload: dict
    prev_hash: str   # 64 hex chars; "0" * 64 for the first entry
    hash: str

@dataclass(frozen=True)
class ChainCheck:
    ok: bool
    checked: int               # entries verified
    first_bad_id: int | None   # first entry whose hash or link does not match
```
- `hash = sha256(prev_hash + canonical_json({created_at, event_type, payment_id, payload}))`.
  `created_at` is set from `now` by `append_audit`, not by the database default, so it can be hashed.
- Appends take a transaction-level advisory lock, so two appends cannot fork the chain.
- `append_audit` runs in the same transaction as the state change it records. If it fails, the
  transaction fails, so there is no decision without an audit entry.
- `GET /audit/verify` returns `ChainCheck` as JSON (route in Sahil's API, body from `verify_chain`).
- **Every** `audit_events` write must go through `append_audit`. A row inserted directly has no
  hash: `verify_chain` reports it as the first bad entry and `append_audit` refuses to extend it
  (`ValueError`), so the chain is never silently skipped (D13).
- `append_audit` raises `TypeError` / `ValueError` for a float in the payload, a naive `now` or an
  empty `event_type`. The payload is stored exactly as hashed (Decimal and datetime become strings).
- **Lock order (avoids deadlocks):** in one transaction, call `check_and_reserve` /
  `release_budget` / `revoke_mandate` (mandate row lock) **before** `append_audit` (chain lock),
  never the other way round.
- Also exported: `entry_hash(...)` and `GENESIS_HASH` (`"0" * 64`), for tests and the console.
- Event types Anurag's side writes: `mandate.created`, `mandate.revoked`. The orchestrator writes
  payment events: `payment.decided`, `payment.blocked`, `payment.authorized`, `payment.captured`,
  `payment.voided`, `payment.failed`, `approval.requested`, `approval.approved`, `approval.denied`,
  `approval.expired` (full list and when each is written: section 5.3).
  Payloads include `decision`, `reasons` (codes and messages), `risk_score` where known.

### Canonical JSON (used by mandates and audit)
`json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")`, after
converting `Decimal` → string and `datetime` → UTC ISO-8601 with `Z`. `float` values raise
`TypeError` (they do not round-trip exactly).

## 4. Anomaly model — `backend/app/anomaly.py` (Anurag; model landed Nov 4)

```python
def score_anomaly(features: AnomalyFeatures) -> AnomalyResult
```
```python
@dataclass(frozen=True)
class AnomalyFeatures:
    amount: Decimal
    amount_to_max_ratio: float        # amount / mandate.max_payment_amount
    budget_used_ratio: float          # budget_used / total_budget, before this payment
    payment_count: int
    recent_payment_count: int
    seconds_since_last_payment: float | None
    merchant_seen_before: bool        # this mandate paid this merchant before
    hour_of_day_utc: int

@dataclass(frozen=True)
class AnomalyResult:
    score: float                      # 0.0 (normal) .. 1.0 (anomalous)
    reasons: tuple[str, ...]          # top SHAP features, human-readable
    model_version: str
```
- Floats are fine here: these are model features, not money.
- The orchestrator maps the score to STEP_UP above a threshold (value agreed with the model, Nov 8).
- If `score_anomaly` raises or times out, the orchestrator uses STEP_UP (D3). It never turns a
  policy BLOCK into ALLOW (D2).
- Until the model lands, the stub returns `score=0.0, reasons=(), model_version="stub"`.

Landed (Nov 4):
- Lives in `backend/app/anomaly.py`, not `ml/score.py`, so the orchestrator imports it directly
  (D15). Model file: `backend/app/anomaly_model.json` (XGBoost, trained on synthetic data by
  `backend/scripts/train_anomaly.py`; deterministic). No stub any more: a missing or mismatched model
  file raises, and the orchestrator uses STEP_UP.
- `reasons`: up to 3 plain-language phrases for the features that pushed the score UP, from
  XGBoost's own SHAP values (`pred_contribs`); empty when nothing did.
- `score_anomaly` raises `ValueError`/`TypeError` for features outside the trained ranges.
- Helper: `await build_features(session, payment_id=, mandate_id=, merchant_id=, amount=, now=,
  window_seconds=) -> AnomalyFeatures`. Call it after `check_and_reserve` allowed the payment (5.3
  step 2, read-only, own session): it excludes the payment's own hold and row, counts live payments
  in the velocity window, and raises `LookupError` for an unknown mandate.
- Suggested thresholds from the synthetic hold-out set: `ANOMALY_STEP_UP_THRESHOLD=0.5` (catches 93%,
  0.4% false alarms) and `ANOMALY_BLOCK_THRESHOLD=0.9` (catches 88%, 0 false alarms). The sync-1
  demo purchase ($179 of $200, first payment) scores about 0.05. Final values still agreed Nov 8.

## 5. Money path — `backend/app/` (Sahil)

### 5.1 Intent judge — `app/judge.py`
```python
async def judge_intent(*, intent: str, mandate: policy.Mandate, cart: Cart) -> JudgeResult
```
```python
@dataclass(frozen=True)
class CartItem:
    name: str                 # from Leash's catalog, never the agent's text
    quantity: int
    unit_price: Decimal

@dataclass(frozen=True)
class Cart:
    merchant_id: str
    items: tuple[CartItem, ...]
    total: Decimal

@dataclass(frozen=True)
class JudgeResult:
    match: bool               # True = the cart fits what the user asked for
    score: float              # 0.0 (clear match) .. 1.0 (clear mismatch)
    reason: str               # one short human-readable sentence
    model: str                # e.g. "openai/gpt-oss-120b"
```
- Groq (D7): httpx `POST {JUDGE_BASE_URL}/chat/completions` with `Authorization: Bearer
  <GROQ_API_KEY>`, model `JUDGE_MODEL`, temperature 0, `response_format: json_object`. Any
  OpenAI-compatible endpoint works by config. The model's reply must be exactly
  `{match: bool, score: number 0..1, reason: non-empty string of at most 300 chars}` (Pydantic,
  strict: no extra keys, no `"true"` or `"0.1"` strings, no NaN).
- Timeout `JUDGE_TIMEOUT_SECONDS` (5 s) per attempt, and one retry after any failure, so at most
  two calls (about 10 s).
- The mandate intent and limits (category, max per payment) are trusted. The cart (merchant, item
  names, quantities, prices, total) is sent as JSON inside `<cart_data>...</cart_data>`, with `<` and
  `>` escaped so a name cannot close the block. The system prompt says the block is data, never
  instructions. Page text and the agent's conversation are never sent.
- Raises on a missing `GROQ_API_KEY` (no call made), timeout, bad JSON or provider error. The
  orchestrator turns that into STEP_UP (D3).
- `intent` is the user's own words ("Sony or Bose noise-cancelling headphones"). See open question A.
- An HTTP error, a body that is not JSON or has no `choices`, and a timeout all count as failures.
  On an HTTP error the status and the first 300 characters of the body are logged (the key is
  redacted); the key, the `Authorization` header and the prompt are never logged.
- Only `message.content` is read. Empty or missing content is a failure. Reasoning models
  (`openai/gpt-oss-120b`) also return `message.reasoning`; it is never parsed or stored.
- `scripts/smoke_judge.py` calls real Groq twice (a matching and a mismatching cart).

### 5.2 How the checks combine
Checked in this order. A later check can only make the outcome stricter (D2).

| Check | Result | Outcome |
|---|---|---|
| cart check (`app/catalog.py` `price_cart`) | any problem | **BLOCK** (no hold; see the note below) |
| `check_and_reserve` | BLOCK | **BLOCK** (no hold, judge and model not called) |
| `score_anomaly` | `score >= ANOMALY_BLOCK_THRESHOLD` | **BLOCK** (release hold) |
| `judge_intent` | `match == False` | **STEP_UP** |
| `score_anomaly` | `score >= ANOMALY_STEP_UP_THRESHOLD` | **STEP_UP** |
| judge or model | raises or times out | **STEP_UP** |
| all pass | | **ALLOW** |

Thresholds come from `ANOMALY_STEP_UP_THRESHOLD` (default 0.5) and `ANOMALY_BLOCK_THRESHOLD` (default
0.9), values 0..1; final values agreed Nov 8. A score outside 0..1 (NaN included) counts as a failed
check. Reason codes: `anomaly_block`, `anomaly_step_up`, `anomaly_check_failed`, `intent_mismatch`
(its message includes the judge's reason) and `intent_check_failed`. Only `match` decides; the judge's
`score` is recorded but not compared with a threshold. An anomaly BLOCK has only `anomaly_block` as its
reason. A STEP_UP lists every STEP_UP reason (for example `intent_mismatch` and `anomaly_step_up`), so
the approver sees all of them.

### 5.3 Orchestrator call order (one `request_payment`)
No database transaction is held open across a network call (judge, model, PayPal).

0. Price the cart from the catalog (`price_cart`). A cart Leash cannot price (5.5) is recorded as a
   `REQUESTED` row, then `BLOCKED` with `payment.decided` / `payment.blocked`, without calling
   `check_and_reserve` (no mandate lock, no hold). When the cart's only problems are
   `mixed_categories` and/or `sku_wrong_merchant` (so there is a priced total and known categories),
   the policy's pure `evaluate_payment` also runs, on the priced total, once per distinct category
   (sorted), with an unlocked read of the mandate and no velocity count. Its reasons are appended
   after the cart reasons, one per code, so the person sees every problem at once (for the injected
   MegaDeals cart: `mixed_categories`, `merchant_not_allowed`, `amount_exceeds_max_payment_amount`,
   `category_mismatch`). A missing mandate adds `mandate_not_found`; any error adds nothing. Still
   BLOCK: no hold, judge, model or PayPal, and the same two audit events with the merged reasons.
   An unknown SKU, bad quantity or empty cart keeps the cart reasons only.
1. **T1** (one transaction): insert `payments` row `REQUESTED` → `check_and_reserve(payment_id, ...)`
   → `append_audit("payment.decided", {policy decision, reasons})`.
   - BLOCK: status `BLOCKED`, `append_audit("payment.blocked")`, commit, return BLOCK.
   - ALLOW: commit (row + hold + audit together).
2. Outside a transaction, at the same time (`asyncio.gather`, each with its own error handling):
   `judge_intent` (mandate read in T1 from the already loaded row, item names from the catalog) and
   `build_features` (its own read-only session) then `score_anomaly`. No timeout on the model yet
   (scoring takes milliseconds).
3. **T2**: combine (5.2), `append_audit("payment.decided", {decision, reasons, risk_score (6-decimal
   string or null), anomaly_reasons, model_version, judge})`. `judge` is `{match, score (6-decimal
   string), reason, model}`, or null if the judge failed. An allowed payment therefore has two
   `payment.decided` entries; readers take the latest.
   - BLOCK: status `BLOCKED`, `release_budget`, `append_audit("payment.blocked")`, commit, return.
   - STEP_UP: status `AWAITING_APPROVAL`, store the approval token's SHA-256 and
     `approval_expires_at = now + APPROVAL_TTL_SECONDS`,
     `append_audit("approval.requested", {approval_expires_at})`, commit, return STEP_UP with
     `approval_url`. The hold stays.
   - ALLOW: commit, continue.
4. PayPal authorize (idempotency key = `payments.idempotency_key`).
   - **T3** success: status `AUTHORIZED`, `append_audit("payment.authorized", {order_id})`, commit.
   - **T3** failure: status `FAILED`, `release_budget`, `append_audit("payment.failed")`, commit,
     return BLOCK.
5. PayPal capture.
   - **T4** success: status `CAPTURED`, `append_audit("payment.captured")`, commit, return ALLOW.
   - **T4** failure, then void succeeds: status `VOIDED`, `release_budget`,
     `append_audit("payment.voided")`, commit, return BLOCK.
   - Void also fails: status stays `AUTHORIZED`, hold kept, `append_audit("payment.failed",
     {"void_failed": true})`, commit, return BLOCK. Needs manual cleanup; logged as an error.

Approvals (`app/approvals.py`, routes in 5.8). Each answer is one transaction that locks the payment
row (`FOR UPDATE`); the payment must be `AWAITING_APPROVAL`, undecided and not expired. A second click
or a concurrent request sees it decided and changes nothing.
- Approve: the mandate is checked again (signature, not revoked, not expired). If that fails it is a
  deny with reason `mandate_no_longer_valid`. Otherwise set `approval_decision = approved`,
  `append_audit("approval.approved")`, commit, then steps 4 and 5 run unchanged
  (`orchestrator.authorize_and_capture`, same `idempotency_key`). The status stays
  `AWAITING_APPROVAL` until PayPal authorizes.
- Deny: `DENIED`, `release_budget`, `append_audit("approval.denied")`.
- Expiry: `expire_due_approvals(sessions, now)` moves undecided payments past `approval_expires_at`
  to `EXPIRED`, `release_budget`, `append_audit("approval.expired")`, one transaction per payment.
  It runs at startup and every 60 s from the FastAPI lifespan, and before every approve or deny.
  An undecided `AWAITING_APPROVAL` row with no `approval_expires_at` (a STEP_UP from before
  approvals existed, which nobody can answer) counts as expired, so the first run releases it.
- Stuck approvals: `settle_stuck_approvals(sessions, paypal, vault_id, now)` runs in the same
  background loop (not before approve or deny). It takes payments still `AWAITING_APPROVAL` with
  `approval_decision = approved` and `approval_decided_at` older than `APPROVAL_STUCK_SECONDS`: the
  approve request died before PayPal's answer was recorded. It asks PayPal for the order by
  replaying the create call with the payment's idempotency key (`find_order`, as reconcile does);
  `authorize` is never called a second time.
  - An order that was already there with an authorization: steps T3 to T4 run unchanged
    (`orchestrator.capture_authorized`: `AUTHORIZED`, capture, or void and release).
  - No order (the replay made a new one): any hold the replay placed is voided, then `FAILED`
    with reason `approval_not_completed`, `release_budget`, `append_audit("payment.failed")`.
  - PayPal unreachable, no `PAYPAL_VAULT_ID`, or decided more than 5 hours ago (PayPal forgets
    keys after 6): logged and left unchanged for the next run or a person.
  - The payment row is locked (`FOR UPDATE`) during the lookup so two runs cannot settle the same
    payment. This is the one place a transaction spans a PayPal call; it holds no mandate or
    audit-chain lock while it waits.
  - Known limit: if voiding the replay's own hold fails, the next run sees that hold as an
    existing order and captures it.

Reconcile (`app/reconcile.py`, `scripts/reconcile_payments.py`): settles payments left in `REQUESTED`
or `AUTHORIZED` for N minutes (default 15, minimum 5) with PayPal as the source of truth, for when a
DB write failed after a PayPal call. Run by hand on a laptop; nothing schedules it yet, and it does not
look at `AWAITING_APPROVAL`. A `REQUESTED` row is checked by replaying the create-order call with its
idempotency key (PayPal keeps keys for 6 hours, so rows older than 5 hours are left for manual review);
a replay that creates a new order is voided at once. Releases come before `append_audit` in each
transaction. Events:
- `payment.reconciled_lookup`: the replay found an authorization; order and authorization IDs saved
- `payment.reconciled_authorized`: a `REQUESTED` row had a real authorization
- `payment.reconciled_captured`: PayPal shows it captured; hold kept
- `payment.reconciled_voided`: hold voided at PayPal or already gone; budget released
- `payment.reconciled_failed`: no hold, or only the lookup's own hold; budget released

### 5.4 Decision response (MCP `request_payment` and REST `/payments/{id}`)
```json
{
  "payment_id": "uuid or null",
  "decision": "ALLOW | STEP_UP | BLOCK",
  "status": "REQUESTED | BLOCKED | AWAITING_APPROVAL | AUTHORIZED | CAPTURED | VOIDED | FAILED | DENIED | EXPIRED",
  "reasons": [{"code": "merchant_not_allowed", "message": "...", "source": "policy | judge | anomaly | system"}],
  "risk_score": "0.045706",
  "judge": {"match": true, "score": "0.050000", "reason": "...", "model": "openai/gpt-oss-120b"},
  "approval_url": "https://.../approvals/<payment_id>?token=<token> or null",
  "approval_expires_at": "2026-11-10T18:15:00Z or null"
}
```
- `payment_id` is null only on an internal error (no row could be written).
- `risk_score` is a string (the audit chain refuses floats), or null when the model did not run or
  failed. `judge` is null when the judge did not run (cart or policy BLOCK) or failed; its `score` is
  a 6-decimal string for the same reason. Both are kept on PayPal failures after they ran.
  `request_payment` and `get_payment_status` return all these keys.
- `approval_url` is set only in the `request_payment` response of a STEP_UP. It carries the raw
  approval token, which is stored nowhere (the database keeps its SHA-256), so
  `get_payment_status` and `GET /payments` always return `approval_url: null`.
  `approval_expires_at` is in the STEP_UP response and in `get_payment_status`; `GET /payments`
  does not return it yet.
- Money in JSON is always a string (`"179.00"`), never a number.
- `reasons` is empty on ALLOW.

### 5.5 MCP tools (agent-facing, stdio now, hosted later)
| Tool | Arguments | Returns |
|---|---|---|
| `request_payment` | `mandate_id`, `merchant_id`, `items: [{sku, quantity}]` | decision response (5.4) |
| `list_mandates` | none | `{"mandates": [{mandate_id, intent, category, currency, max_payment_amount, remaining_budget, remaining_count, expires_at, revoked}]}` (active mandates only) |
| `get_payment_status` | `payment_id` | the `GET /payments/{id}` view (5.4 plus mandate_id, merchant_id, amount, currency, items, created_at, updated_at); unknown ID is an MCP error result |

- The agent sends SKUs and quantities only, never a price, amount or category. Leash prices each line
  from its catalog (`stores/catalog.json`, setting `CATALOG_PATH`), the same file the demo stores are
  built from. The amount is the sum of price × quantity; the category is the single category of the
  items.
- A cart Leash cannot price is blocked before the mandate check (no hold), with these reason codes
  (source `system`): `empty_cart`, `too_many_items` (more than 20 lines; checked before pricing, stored without the lines), `unknown_sku`, `sku_wrong_merchant` (the SKU belongs to another
  store), `invalid_quantity` (not an integer from 1 to 10), `mixed_categories` (items from more than one
  category).
- `payments.items` stores the lines as JSON: `[{sku, quantity, unit_price}]`, with `unit_price` only on
  lines Leash priced.
- Demo stores: `python -m stores.serve` serves SoundHub on `:8101` and MegaDeals on `:8102`, built from
  the catalog. The MegaDeals `md-sony-xm5` page holds the hidden injection (3 × `md-giftcard-100`).
- The agent never sees PayPal IDs or credentials.
- `list_mandates` returns one object, not a bare list: FastMCP splits a list into one text block per
  item. Money is a string (`currency` is always `"USD"`), `expires_at` is UTC with `Z`. Revoked and
  expired mandates are left out, so `revoked` is always `false` here.
- Transports: stdio (`python -m app.mcp_server`) for Claude Desktop; streamable HTTP at `/mcp` on the
  FastAPI app (stateless, JSON responses) for hosted agents. HTTP needs
  `Authorization: Bearer <MCP_AUTH_TOKEN>`: 401 if the token is missing or wrong, 503 if
  `MCP_AUTH_TOKEN` is unset. Clients read the result's JSON text block.

### 5.6 REST API (frontend-facing)
Single demo user, no auth (hackathon scope; recorded as a limitation in the README). `/mcp` is the
MCP endpoint (5.5, bearer token), not a REST route.

| Method + path | Body / query | Returns |
|---|---|---|
| `GET /health` | | `{status, db}` |
| `GET /mandates` | | list as in `list_mandates` |
| `POST /mandates` | `{intent, category, max_payment_amount, total_budget, max_payment_count, allowed_merchant_ids, expires_at}` | created mandate (`create_mandate`) |
| `POST /mandates/{id}/revoke` | | revoked mandate (`revoke_mandate`) |
| `GET /payments` | `?limit=50` | latest payments, newest first, decision response shape |
| `GET /payments/{id}` | | decision response |
| `GET /approvals/{payment_id}` | `?token=` | HTML approval page (5.8) |
| `POST /approvals/{payment_id}/approve` | form field `token` | HTML page with the result (5.8) |
| `POST /approvals/{payment_id}/deny` | form field `token` | HTML page with the result (5.8) |
| `GET /audit` | `?after_id=&limit=100` | audit entries, oldest first |
| `GET /audit/verify` | | `ChainCheck` (`{ok, checked, first_bad_id}`) |

Read routes landed (Anurag, Nov 5; `backend/app/read_api.py`, all read-only):
- `GET /payments?limit=50` (1-500, newest first) and `GET /payments/{id}` (404 `{"detail": "payment
  not found"}`) return the 5.4 shape plus `mandate_id`, `merchant_id`, `amount` (string, 2 decimals),
  `currency`, `items`, `created_at`, `updated_at` (UTC ISO-8601 with `Z`). Each reason gets
  `source: null` unless the stored reason has one.
- `risk_score` and `judge` are read from the payment's LATEST `payment.decided` audit payload, keys
  `risk_score` (string) and `judge` (`{match, score, reason}`). Write them there when the model and
  the judge run and the console shows them; no schema change needed. `approval_url` is null for now.
- `GET /audit?after_id=0&limit=100` (limit 1-1000): `[{id, created_at, event_type, payment_id,
  payload, prev_hash, hash}]`, oldest first; page with `after_id` = the last id you got.
- CORS: origins from `CORS_ORIGINS` (comma-separated, default `http://localhost:5173`), GET and POST.

### 5.7 Settings (env, names in `.env.example`)
| Name | Default | Used by |
|---|---|---|
| `VELOCITY_MAX_PAYMENTS` | `3` | `check_and_reserve` |
| `VELOCITY_WINDOW_SECONDS` | `600` | `check_and_reserve` |
| `CATALOG_PATH` | empty = `stores/catalog.json` | `app/catalog.py` |
| `JUDGE_BASE_URL` | `https://api.groq.com/openai/v1` | `judge_intent` |
| `JUDGE_MODEL` | `openai/gpt-oss-120b` | `judge_intent` |
| `JUDGE_TIMEOUT_SECONDS` | `5` (per attempt) | `judge_intent` |
| `ANOMALY_STEP_UP_THRESHOLD` | `0.5` (final Nov 8) | orchestrator |
| `ANOMALY_BLOCK_THRESHOLD` | `0.9` (final Nov 8) | orchestrator |
| `MCP_AUTH_TOKEN` | empty = `/mcp` off (503) | `app/mcp_server.py` |
| `APPROVAL_TTL_SECONDS` | `900` | orchestrator (STEP_UP expiry time) |
| `APPROVAL_STUCK_SECONDS` | `300` (minimum 300) | `settle_stuck_approvals` |
| `PUBLIC_BASE_URL` | `http://localhost:8000` (Render: `https://leash-84h9.onrender.com`) | orchestrator (`approval_url`) |

### 5.8 Approval routes — `app/approvals.py`
Server-rendered HTML for a phone browser: no JavaScript, inline CSS, every value HTML-escaped. The
token in the link is the only credential (single demo user). It is `secrets.token_urlsafe(32)`,
compared by SHA-256 with `hmac.compare_digest`, and removed from the uvicorn access log.

| Method + path | Input | Returns |
|---|---|---|
| `GET /approvals/{payment_id}` | query `token` | the page; never changes state |
| `POST /approvals/{payment_id}/approve` | form field `token` | the page with the result |
| `POST /approvals/{payment_id}/deny` | form field `token` | the page with the result |

- Unknown payment, wrong token or missing token: `404 {"detail": "Not Found"}`, nothing else.
- The page shows the merchant, items with prices (names from the catalog), total, the mandate
  intent, every reason message once, the risk score (from the latest `payment.decided` entry), and
  the expiry time, with Approve and Deny buttons. Under "Checks" the judge's reason and the risk
  signals appear only if they are not already in a reason above. The expiry is a
  `<time datetime="...Z">` element showing UTC; a small inline script rewrites it in the viewer's
  local time.
- Once decided or expired it shows the status instead of the buttons: `CAPTURED`, `DENIED`,
  `EXPIRED`, `FAILED` or `VOIDED`. A pending payment past its expiry is shown as `EXPIRED` by GET
  without being written; the next POST or expiry run records it.
- Columns on `payments` (migration `0003`): `approval_token_hash`, `approval_expires_at`,
  `approval_decided_at`, `approval_decision` (`approved | denied | expired`).

## Open questions (answered Nov 2)
1. Velocity limits in settings as `VELOCITY_MAX_PAYMENTS` / `VELOCITY_WINDOW_SECONDS`: **yes** (5.7).
2. Payment row inserted before `check_and_reserve`: **yes** (5.3 step 1).
3. `/audit/verify` returns `ChainCheck`: **yes** (5.6).
4. Migration `0002` on top of `0001`: **yes**. `sahil` is merged to `main`.

## Open questions for Anurag (answered Nov 2)
A. Signed `intent: str` term on mandates (the user's own words, used by the judge): **yes**. It is part
   of `MandateTerms` and covered by the signature (section 1).
B. `MandateRecord` exposes `budget_used`, `payment_count`, `total_budget` and `max_payment_count`:
   **yes**, so `list_mandates` shows what is left without recomputing.
