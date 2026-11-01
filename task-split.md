# Task split — Sahil & Anurag

Both of us are backend + AI engineers, so the work is split into two **vertical slices**. Each person
owns one path end to end: backend services, one AI component, and one demo/frontend piece.
Each of us should still be able to explain the other's half (see `docs/LEARN.md`).

Status as of Nov 2: M0 done. `backend/policy.py` (deterministic evaluator) exists, no tests yet.
M1 not started. We are ~1.5 days behind the original plan; this schedule absorbs it.

## Ownership

| Area | **Sahil — money path + LLM** | **Anurag — trust path + ML** |
|---|---|---|
| Backend core | FastAPI app, settings, Neon Postgres, Alembic (first, unblocks both) | Policy engine (`backend/policy.py`) + unit tests |
| Backend core | PayPal client, payment state machine, idempotency keys, saga (void + restore budget) | Mandates: Ed25519 sign/verify, create/revoke, budget + count tracking |
| Backend core | MCP server + decision orchestrator (calls every check, combines into ALLOW / STEP_UP / BLOCK) | Hash-chained audit log + `/audit/verify` endpoint |
| **AI** | **Groq intent judge** — LLM-as-judge, structured output, cart text treated as data never instructions | **XGBoost + SHAP anomaly model** — synthetic data generator, features, training, thresholds, explanations |
| **AI agent / demo** | Demo stores: SoundHub (legit) + MegaDeals (hidden injection) | **LangGraph attack agent** (Playwright) that gets prompt-injected |
| Backend feature | Step-up approvals: `AWAITING_APPROVAL`, expiry job, approve/deny API | — |
| Frontend | Mandate form + approvals page | AG Grid audit console (filters, risk score, reasons, chain-verified badge) |
| Infra | GitHub Actions CI + Render deploy | — |
| Submission | README + architecture diagram | Demo video (< 3 min) |

## Schedule

| Date | Sahil | Anurag |
|---|---|---|
| **Nov 2** | **Together (~2 h): write and agree `docs/CONTRACTS.md`** | ← same |
| Nov 2–3 | App skeleton + DB, PayPal client, MCP server + orchestrator | Policy tests, mandates, audit log |
| **Nov 4** | **Sync 1:** Claude Desktop pays $1 sandbox through hosted Leash using real policy, mandates and audit | ← same |
| Nov 4–6 | Groq intent judge, saga, demo stores | LangGraph attack agent, synthetic data generator |
| **Nov 6** | **Sync 2:** attack runs end to end and is blocked | ← same |
| Nov 6–8 | Step-up approvals, mandate + approvals pages | XGBoost + SHAP model, AG Grid console |
| **Nov 8** | **Sync 3:** everything wired: judge + model in the orchestrator, frontend on hosted backend | ← same |
| Nov 9–10 | Bug bash, hardening. **Feature freeze Nov 10 23:59 IST** | ← same |
| Nov 11 | README, architecture diagram, seed script | Demo video |
| Nov 12 | **Submit on Devpost by 6 PM IST** | ← same |

## Contracts (to define in `docs/CONTRACTS.md` on Nov 2)

The orchestrator (Sahil) calls Anurag's modules only through these functions. Agree inputs/outputs
first, then both build in parallel against stubs.

| Function | Owner | Purpose |
|---|---|---|
| `verify_mandate(mandate) -> MandateCheck` | Anurag | Signature valid, not expired/revoked, budget + count left |
| `evaluate_payment(request, mandate, usage) -> PolicyDecision` | Anurag | Deterministic rules (already in `backend/policy.py`) |
| `reserve_budget(...)` / `release_budget(...)` | Anurag | Atomic budget hold on ALLOW, release on saga void |
| `append_audit(event) -> AuditEntry` / `verify_chain() -> bool` | Anurag | Hash-chained log |
| `score_anomaly(features) -> (score, reasons)` | Anurag | XGBoost risk + SHAP reasons |
| `judge_intent(mandate, cart) -> JudgeResult` | Sahil | `{match, score, reason}` from Groq |
| MCP `request_payment` / `list_mandates` / `get_payment_status` | Sahil | Agent-facing tool shapes |
| REST `/mandates`, `/approvals`, `/audit`, `/payments/{id}` | Sahil (API), both (frontend) | Frontend-facing endpoints |
| Decision response `{decision, reasons[], risk_score, judge, payment_id}` | Sahil | Shared by MCP, REST and audit |

## Working rules
- Small branch per task: `sahil/<task>`, `anurag/<task>`. Open a PR; the other person reviews; merge to `main`.
- `main` must always run. Enable "Require a pull request before merging" on `main` after this file lands.
- Claim a task in `CONTEXT.md` → "In progress" before starting; log every change there.
- Fail safe everywhere: any check error → BLOCK or STEP_UP, never ALLOW (D3).
- Write tests with the code. Zero spend (D6).
- 15-minute daily sync call.

## Cut order if behind
1. NL mandate parser, Kernel live view (stretch)
2. XGBoost + SHAP (keep the Groq judge)
3. Webhook verification
4. Frontend beyond the audit console and approvals page

**Never cut:** authorize/capture/void with idempotency, policy engine, attack demo, audit log + console,
hosted URL, video.
