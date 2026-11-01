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

## 1. Mandates — `backend/mandates.py` (Anurag)

### What is signed
A mandate's **terms** are signed with Ed25519 at creation; its **state** is not.

| Signed terms (never change) | Unsigned state (changes) |
|---|---|
| `mandate_id`, `category`, `max_payment_amount`, `total_budget`, `max_payment_count`, `allowed_merchant_ids`, `expires_at`, `created_at` | `revoked_at`, `budget_used`, `payment_count` |

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

## 2. Policy + budget hold — `backend/policy.py`, `backend/mandates.py` (Anurag)

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

**When the orchestrator calls `release_budget`:** a held payment ends in any terminal state other
than `CAPTURED`: `DENIED`, `EXPIRED`, `FAILED`, `VOIDED`, or `BLOCKED` by a later check (judge or
anomaly model). The hold stays during `AWAITING_APPROVAL`, so an approved payment cannot exceed the
budget.

Trusted inputs: `request.category` and `request.merchant_id` must come from Leash's own store
records, not from the agent's text (see the `policy.py` docstring).

## 3. Audit log — `backend/audit.py` (Anurag)

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
- Event types Anurag's side writes: `mandate.created`, `mandate.revoked`. The orchestrator writes
  payment events: `payment.decided`, `payment.blocked`, `payment.authorized`, `payment.captured`,
  `payment.voided`, `payment.failed`, `approval.requested`, `approval.approved`, `approval.denied`,
  `approval.expired` (full list and when each is written: section 5.3).
  Payloads include `decision`, `reasons` (codes and messages), `risk_score` where known.

### Canonical JSON (used by mandates and audit)
`json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")`, after
converting `Decimal` → string and `datetime` → UTC ISO-8601 with `Z`. `float` values raise
`TypeError` (they do not round-trip exactly).

## 4. Anomaly model — `ml/score.py` (Anurag, shape only until Nov 6–8)

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

## 5. Money path — `backend/app/` (Sahil)

### 5.1 Intent judge — `app/judge.py`
```python
async def judge_intent(*, intent: str, mandate: policy.Mandate, cart: Cart) -> JudgeResult
```
```python
@dataclass(frozen=True)
class CartItem:
    name: str                 # from the store's own record, not the agent's text
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
    model: str                # e.g. "groq/llama-3.3-70b-versatile"
```
- Groq via LiteLLM, JSON output validated against the schema (D7). Timeout 5 s, one retry.
- The mandate intent is trusted. Cart and page text are untrusted: passed as clearly labelled data,
  never as instructions, and never shown the agent's conversation.
- Raises on timeout, bad JSON or provider error. The orchestrator turns that into STEP_UP (D3).
- `intent` is the user's own words ("Sony or Bose noise-cancelling headphones"). See open question A.

### 5.2 How the checks combine
Checked in this order. A later check can only make the outcome stricter (D2).

| Check | Result | Outcome |
|---|---|---|
| `check_and_reserve` | BLOCK | **BLOCK** (no hold, judge and model not called) |
| `score_anomaly` | `score >= ANOMALY_BLOCK_THRESHOLD` | **BLOCK** (release hold) |
| `judge_intent` | `match == False` | **STEP_UP** |
| `score_anomaly` | `score >= ANOMALY_STEP_UP_THRESHOLD` | **STEP_UP** |
| judge or model | raises or times out | **STEP_UP** |
| all pass | | **ALLOW** |

Thresholds come from settings and are agreed with the model on Nov 8. Until the judge and model
land, their stubs return `match=True` and `score=0.0`.

### 5.3 Orchestrator call order (one `request_payment`)
No database transaction is held open across a network call (judge, model, PayPal).

1. **T1** (one transaction): insert `payments` row `REQUESTED` → `check_and_reserve(payment_id, ...)`
   → `append_audit("payment.decided", {policy decision, reasons})`.
   - BLOCK: status `BLOCKED`, `append_audit("payment.blocked")`, commit, return BLOCK.
   - ALLOW: commit (row + hold + audit together).
2. Outside a transaction: `judge_intent` and `score_anomaly` run in parallel, each with a timeout.
3. **T2**: combine (5.2), `append_audit("payment.decided", {judge, risk_score, final decision})`.
   - BLOCK: status `BLOCKED`, `release_budget`, `append_audit("payment.blocked")`, commit, return.
   - STEP_UP: status `AWAITING_APPROVAL`, `append_audit("approval.requested")`, commit, return
     STEP_UP. The hold stays.
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

Approvals (M5): approve → re-enter at step 4; deny → `DENIED`, `release_budget`,
`append_audit("approval.denied")`; expiry job → `EXPIRED`, `release_budget`,
`append_audit("approval.expired")`. Approve on an expired or decided payment is rejected.

### 5.4 Decision response (MCP `request_payment` and REST `/payments/{id}`)
```json
{
  "payment_id": "uuid or null",
  "decision": "ALLOW | STEP_UP | BLOCK",
  "status": "REQUESTED | BLOCKED | AWAITING_APPROVAL | AUTHORIZED | CAPTURED | VOIDED | FAILED | DENIED | EXPIRED",
  "reasons": [{"code": "merchant_not_allowed", "message": "...", "source": "policy | judge | anomaly | system"}],
  "risk_score": 0.12,
  "judge": {"match": true, "score": 0.05, "reason": "..."},
  "approval_url": "https://.../approvals/<payment_id> or null"
}
```
- `payment_id` is null only when the request was rejected before a row could be written (bad amount).
- `risk_score` and `judge` are null when that check did not run (policy BLOCK, or stub).
- Money in JSON is always a string (`"179.00"`), never a number.
- `reasons` is empty on ALLOW.

### 5.5 MCP tools (agent-facing, stdio now, hosted later)
| Tool | Arguments | Returns |
|---|---|---|
| `request_payment` | `mandate_id`, `merchant_id`, `items: [{name, quantity, unit_price}]` | decision response (5.4) |
| `list_mandates` | none | `[{mandate_id, intent, category, max_payment_amount, remaining_budget, remaining_count, expires_at, revoked}]` |
| `get_payment_status` | `payment_id` | decision response (5.4) for that payment |

- The agent never sends `category` or a trusted price. Leash looks up the merchant and item prices
  from its own store records (`MERCHANT_CATEGORIES` now, a `merchants` table later if needed) and
  computes the amount itself.
- Change from today's code: `request_payment` currently takes `amount: str` and `items: str`. It moves
  to the item list above when the judge lands, so the judge and the price check see structured items.
- The agent never sees PayPal IDs or credentials.

### 5.6 REST API (frontend-facing)
Single demo user, no auth (hackathon scope; recorded as a limitation in the README).

| Method + path | Body / query | Returns |
|---|---|---|
| `GET /health` | | `{status, db}` |
| `GET /mandates` | | list as in `list_mandates` |
| `POST /mandates` | `{intent, category, max_payment_amount, total_budget, max_payment_count, allowed_merchant_ids, expires_at}` | created mandate (`create_mandate`) |
| `POST /mandates/{id}/revoke` | | revoked mandate (`revoke_mandate`) |
| `GET /payments` | `?limit=50` | latest payments, newest first, decision response shape |
| `GET /payments/{id}` | | decision response |
| `GET /approvals` | `?status=pending` | payments in `AWAITING_APPROVAL` |
| `POST /approvals/{payment_id}/approve` | | decision response after the payment continues |
| `POST /approvals/{payment_id}/deny` | | decision response (`DENIED`) |
| `GET /audit` | `?after_id=&limit=100` | audit entries, oldest first |
| `GET /audit/verify` | | `ChainCheck` (`{ok, checked, first_bad_id}`) |

### 5.7 Settings (env, names in `.env.example`)
| Name | Default | Used by |
|---|---|---|
| `VELOCITY_MAX_PAYMENTS` | `3` | `check_and_reserve` |
| `VELOCITY_WINDOW_SECONDS` | `600` | `check_and_reserve` |
| `JUDGE_MODEL` | `groq/llama-3.3-70b-versatile` | `judge_intent` |
| `JUDGE_TIMEOUT_SECONDS` | `5` | orchestrator |
| `ANOMALY_STEP_UP_THRESHOLD` | agreed Nov 8 | orchestrator |
| `ANOMALY_BLOCK_THRESHOLD` | agreed Nov 8 | orchestrator |
| `APPROVAL_TTL_SECONDS` | `900` | approvals expiry job |

## Open questions (answered Nov 2)
1. Velocity limits in settings as `VELOCITY_MAX_PAYMENTS` / `VELOCITY_WINDOW_SECONDS`: **yes** (5.7).
2. Payment row inserted before `check_and_reserve`: **yes** (5.3 step 1).
3. `/audit/verify` returns `ChainCheck`: **yes** (5.6).
4. Migration `0002` on top of `0001`: **yes**. `sahil` is merged to `main`.

## Open questions for Anurag
A. Can mandates carry the user's own words as a signed `intent: str` term ("Sony or Bose
   noise-cancelling headphones")? The judge needs it; `category` alone is too coarse to catch
   "headphones mandate, gift-card cart" inside one category.
B. `list_mandates` shows `remaining_budget` and `remaining_count`. Can `MandateRecord` expose them
   (or `budget_used` / `payment_count`) so the orchestrator does not recompute them?
