# Contracts between the money path (Sahil) and the trust path (Anurag)

Status: **draft** (Anurag's half). Sahil's half and sign-off pending.

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
  payment events; suggested names: `payment.decided`, `payment.authorized`, `payment.captured`,
  `payment.voided`, `payment.failed`, `approval.approved`, `approval.denied`, `approval.expired`.
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

## 5. Sahil's half (to fill in)
- `judge_intent(mandate, cart) -> JudgeResult`
- MCP tools `request_payment`, `list_mandates`, `get_payment_status`
- REST `/mandates`, `/approvals`, `/audit`, `/payments/{id}`
- Decision response `{decision, reasons[], risk_score, judge, payment_id}`
- Orchestrator order of calls and where `release_budget` / `append_audit` are called

## Open questions for Sahil
1. Velocity window and max: put them in settings (`VELOCITY_MAX_PAYMENTS`, `VELOCITY_WINDOW_SECONDS`)?
2. OK for `check_and_reserve` to need the payment row inserted first (so `payment_id` exists)?
3. OK for `/audit/verify` to return `ChainCheck` instead of a bare bool?
4. Migration `0002` (mandates + budget_holds) goes on top of your `0001`; fine to add it on `anurag`
   once `sahil` is merged to `main`?
