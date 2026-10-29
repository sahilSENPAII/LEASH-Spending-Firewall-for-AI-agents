# Leash — Stage 1: Problem & Requirements

Oct 30, 2026 · @Sahil

## Problem

AI agents can now shop and pay, but nothing stops them from spending money the user never approved. Leash is a firewall between an agent and PayPal: money moves only when a payment matches a mandate the user signed.

- **Agents get hijacked.** Hidden text on a webpage (indirect prompt injection) can tell an agent to "also buy 3 gift cards", and the agent obeys.
- **No proof of consent.** When an agent pays, there is no signed record that the user approved that purchase, that amount, at that merchant.
- **Agents hold too much power.** Giving an agent PayPal credentials gives it the whole wallet. There is no spending scope.
- **No explanation afterwards.** When something goes wrong, there is no log of why the agent paid, or why a payment was allowed.

**Problem statement:** A user who lets an AI agent buy things on their behalf has no way to limit what it can spend, prove what they approved, or see why each payment happened.

## Users

The primary user is a person delegating shopping to an agent. The developer is secondary. The rest are audiences for the pitch, not users of the build.

| User | Goal | What Leash gives them |
| --- | --- | --- |
| Shopper (primary) | Let an agent buy things without risking the wallet | Signed mandates, step-up approval on phone, revoke anytime |
| Agent developer | Add payments to an agent without building safety from scratch | Drop-in MCP server, zero agent code changes |
| Merchant | Accept agent payments without chargebacks | Proof the user approved the purchase |
| PayPal | Make agentic commerce trustworthy | A guard layer on top of Orders, Vault, Webhooks |
| Hackathon judge | Understand the value in under 3 minutes | Live injection attack, visibly blocked, with reasons |

## Success metrics

Success = a working, hosted demo that scores on all five Devpost criteria and competes for Best Use of Agentic Commerce, Best Use of PayPal + AI, and the AG Grid prize.

**Judging criteria (equal, no weights published) → how Leash scores**

| Criterion | What we show |
| --- | --- |
| Technological implementation | Real PayPal Orders / Authorizations / Vault / Webhooks, idempotency, state machine, MCP server, LLM + XGBoost checks |
| Design | Clean mandate flow, approvals inbox, AG Grid audit console |
| Potential impact | Agent payments are a funded, growing space; every agent wallet needs this layer |
| Innovation | Defense in depth: signed mandate + rules + LLM judge + anomaly model in one MCP gateway |
| Presentation | Under-3-min video built around a live injection attack being blocked |

**Product metrics (measured in the demo and tests)**

- 100% of the scripted injection attacks blocked or stepped up.
- 0 legitimate in-mandate purchases blocked in the demo set.
- 0 duplicate charges when a request is retried.
- Audit chain verifies; editing any row breaks verification.
- Check pipeline decision in under 3 s (LLM included), excluding PayPal latency.
- A judge can run the hosted demo in under 2 minutes.

## Functional requirements

Must = the demo fails without it. Should = planned, cut if behind. Could = only with spare time.

| ID | Requirement | Priority |
| --- | --- | --- |
| F1 | User connects PayPal once (Vault v3 payment token) | Must |
| F2 | User creates a mandate (category, max amount, merchants, count, expiry) via a form; it is Ed25519-signed and stored | Must |
| F3 | User can revoke a mandate; revoked mandates block all payments | Must |
| F4 | Agent pays only via MCP tool `request_payment`; also `list_mandates`, `get_payment_status` | Must |
| F5 | Mandate check: signature valid, not expired/revoked, budget and count left | Must |
| F6 | Policy engine: amount, budget, count, category, merchant allowlist, velocity. Any failure = BLOCK | Must |
| F7 | ALLOW → PayPal order with intent=AUTHORIZE, then capture; budget and count decremented | Must |
| F8 | Failure after authorize → void, restore budget (saga compensation) | Must |
| F9 | Idempotency key on every PayPal write | Must |
| F10 | Every decision written to a hash-chained audit log with reasons; verify endpoint | Must |
| F11 | Demo: two stores (legit + malicious) and an agent that falls for the injection | Must |
| F12 | Intent judge (LLM, structured output) flags cart vs mandate mismatch | Should |
| F13 | Anomaly model (XGBoost) gives a risk score + SHAP reasons | Should |
| F14 | STEP-UP: payment waits for human Approve/Deny (Telegram or web), survives restart, expires | Should |
| F15 | Audit console in AG Grid: filters, risk score, reasons, chain-verified badge | Must |
| F16 | Webhook receiver with PayPal signature verification | Should |
| F17 | Natural-language mandate parser with confirm step | Could |
| F18 | Kernel live-view embed of the agent session | Could |

## Non-functional requirements

Security and money-safety come first; speed and scale only need to be demo-grade.

| Area | Requirement |
| --- | --- |
| Security | Agent never sees PayPal credentials. Secrets only in env vars. Cart text is treated as data, never as instructions to the judge LLM |
| Fail-safe | If any check errors or times out (LLM, model, DB), the default is BLOCK or STEP-UP, never ALLOW |
| Money correctness | No double charge on retry. No capture without a valid ALLOW. Illegal state jumps rejected |
| Auditability | Audit log is append-only; tampering is detectable |
| Latency | Decision under 3 s p95 including the LLM call; rules-only path under 200 ms |
| Reliability | Pending approvals survive a server restart |
| Explainability | Every BLOCK / STEP-UP carries human-readable reasons |
| Testability | Unit tests for every policy rule, signature tampering, state transitions, idempotency |
| Deployability | One `docker compose up` locally; hosted on Render with a seed script |
| Maintainability | Code simple enough that the author can explain every file in an interview |

## Constraints

The hard limit is time: about 13 days from Oct 30 to the Nov 13, 1:30 AM IST deadline, solo, alongside college and placement prep.

| Constraint | Detail |
| --- | --- |
| Deadline | Nov 12, 12:00 PM PST = Nov 13, 1:30 AM IST. Target submit: Nov 12 evening IST |
| Team | Solo, code written with Claude Code |
| PayPal | Sandbox only; at least one PayPal API must be meaningfully integrated |
| Demo | Hosted URL or run instructions; static mockups don't qualify |
| Video | Under 3 min, public on YouTube, no unlicensed music or trademarks |
| Repo | Public, open-source license visible in the GitHub About section |
| Disclosure | Must list the AI tools/models used and how each was used |
| Progress | Commits must show meaningful work during the hackathon window |
| Stack | Python 3.12 + FastAPI + Postgres (decided) |
| Budget | Free tiers + Render $50 credits + LLM API key |
| ML data | No real fraud data exists for agents; training data is synthetic and disclosed as such |

Source: [PayPal AI Hackathon on Devpost](https://paypalaihackathon.devpost.com/)

## Non-goals

Anything not on this list waits until after Nov 12.

- Live (non-sandbox) payments, real money, or compliance work (PCI, KYC).
- Multi-user teams, org accounts, roles or SSO. One demo user is enough.
- Our own agent framework. We use LangGraph for the demo and any MCP client otherwise.
- Support for payment rails other than PayPal (cards, crypto, UPI).
- Full AP2 protocol compliance. We borrow the signed-mandate idea only.
- Training on real fraud data, or claiming production-grade ML accuracy.
- Mobile app. Approvals go through Telegram or a web page.
- Redis and Render Workflows. Postgres handles counters and durable waits.
- Load testing, observability dashboards, feature flags, staged rollout.
- Refunds, disputes, subscriptions, multi-currency.

## Open questions and assumptions

The three biggest unknowns get answered by the Stage 4 throwaway scripts on day one.

**Open questions (tick when answered)**

- [ ] Does sandbox Vault v3 let us charge a saved payment token later without the buyer clicking each time (merchant-initiated)?
- [ ] Can one platform account route order funds to two different sandbox merchants (payee routing)?
- [ ] Does Claude Desktop connect to a remote (hosted) MCP server, or only local? Decides how judges try it.
- [ ] Kernel free tier: enough browser minutes for development plus the demo recording?
- [ ] Which LLM for the intent judge (Gemini vs Claude) on cost and structured-output reliability?
- [ ] Telegram bot or a web approval page for STEP-UP?
- [ ] How many hours per day are realistically available until Nov 12?

**Assumptions**

- Judges value a working, hosted end-to-end flow over feature count.
- The injection attack demo is the strongest single moment for the Presentation and Innovation scores.
- Synthetic training data is acceptable if disclosed in the README.
- Using Claude Code is allowed under the rules as long as AI tools are disclosed.
