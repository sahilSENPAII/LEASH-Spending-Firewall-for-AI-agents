# LEARN — what was built and how to explain it

One section per milestone. Plain words first, then the interview angle.

---

## M0 — Setup and de-risking spikes (Oct 30)

### What was built
Three throwaway scripts in `spikes/`, each answering one "could this kill the project?" question
before any real code was written. All three passed.

| Spike | Question | Result |
|---|---|---|
| `01_paypal_vault.py` | Can Leash charge a buyer's saved PayPal later, without the buyer clicking each time? | Yes. Authorize, capture, void and idempotent retry all work |
| `02_mcp_hello.py` | Will an AI assistant (Claude Desktop) call Leash's tools and obey the answer? | Yes. $179 → ALLOW, $300 → BLOCK |
| `03_playwright.py` | Can a headless browser read hidden prompt-injection text on a page? | Yes. The agent sees white 1px text a human can't |

### Concepts, in plain words

**Spike (de-risking).** A tiny, disposable experiment that tests the riskiest assumption first.
If PayPal had refused to charge a saved wallet, the whole design would change. Finding that out on
day 1 costs an hour; finding it on day 10 costs the project.

**OAuth 2.0 client credentials.** Leash proves who it is to PayPal with a Client ID + Secret and gets
back a short-lived access token. Every later API call carries that token. The agent never sees these
credentials; only Leash does (least privilege).

**Vault v3 (saved payment method).** The buyer approves once ("Leash may charge me for future
payments"). PayPal returns a *setup token*, which we swap for a long-lived *payment token*. Later,
Leash creates orders with that token and no buyer click. This is a *merchant-initiated transaction*.

**Authorize → capture / void.** Like a hotel holding money on your card at check-in:
- *Authorize* = put a hold on the money. Nothing is taken yet.
- *Capture* = actually take the held money (purchase confirmed).
- *Void* = release the hold (something went wrong, or the purchase was cancelled).
Leash authorizes first so that a failure later in the flow can be undone cleanly.

**Idempotency (`PayPal-Request-Id`).** Networks fail. If Leash sends "create order" and the reply is
lost, it will retry. With the same request ID, PayPal returns the *same* order instead of making a
second one, so the buyer is never charged twice. Spike 1 proved this: same ID → same order ID.

**Why the payee failed (D10).** The saved wallet is an agreement between the buyer and *our app's*
merchant account. Sending money to a different merchant (SoundHub) with it returned
`BILLING_AGREEMENT_NOT_FOUND`. Real multi-merchant routing needs PayPal's partner program, so all
funds settle to our merchant account and Leash records which store the purchase was for.

**MCP (Model Context Protocol).** A standard way for AI assistants to discover and call outside tools.
A tool has a name, a description and typed arguments. The assistant reads the description, decides to
call it, and fills in the arguments as JSON. Leash is an MCP server, so any MCP-capable agent can use
it with zero code changes. Spike 2 used *stdio* transport: Claude Desktop starts our script and talks
to it over stdin/stdout.

**Why stdout matters in MCP stdio.** stdout *is* the protocol channel. Printing logs to stdout corrupts
the messages; logs must go to stderr. (Our crash was different: a library version change, D11.)

**Indirect prompt injection.** Malicious instructions hidden inside data the agent reads, such as a web
page. Humans can't see white 1px text; the agent reads the raw page text and may obey it. This is the
main attack Leash defends against, and spike 3 proves our demo agent will "see" it.

**Dependency pinning (D11).** `pip install mcp` pulled version 2.x, which renamed `FastMCP`. Pinning
`mcp>=1.2,<2` makes installs repeatable. Lesson: pin major versions of anything the code imports.

### Why this design over alternatives
- Spikes before scaffolding: the riskiest assumptions are tested in hours, not discovered in week 2.
- Authorize-then-capture over direct capture: gives an undo step (void) for the saga later.
- MCP over a custom REST client in the agent: any agent can plug in; that is the product's pitch.

### Likely interview questions
1. **Why authorize first instead of charging directly?**
   So money is only held until every check passes and the merchant confirms. If anything fails after
   that, we void and nothing is taken. *Follow-up: what if capture fails?* Void the authorization,
   restore the mandate's budget, and log the compensation (saga pattern, M2).
2. **How do you stop double charges when a request is retried?**
   An idempotency key (`PayPal-Request-Id`) on every write call. Same key → PayPal returns the original
   result. *Follow-up: where does the key come from?* Generated once per logical payment and stored in
   our DB before calling PayPal, so a retry after a crash reuses it.
3. **What is MCP and why build Leash as an MCP server?**
   A standard protocol for agents to call tools. As an MCP server, Leash works with Claude Desktop,
   Cursor, LangGraph, etc. without changing the agent. *Follow-up: stdio vs HTTP transport?* stdio for a
   local process, streamable HTTP for a hosted server.
4. **What is indirect prompt injection and why can't the agent just ignore it?**
   Instructions hidden in content the agent reads. The model can't reliably tell data from instructions,
   so the defence has to sit outside the model: Leash checks the payment itself, not the agent's reasoning.
5. **Why did paying a different merchant fail, and how did you handle it?**
   The vaulted wallet's agreement is only with our merchant account. We kept funds on one account and
   track the store in Leash. *Follow-up: production fix?* PayPal partner/multiparty onboarding, where each
   merchant is onboarded and we act on their behalf.
6. **Why did you spike before building?**
   To kill the biggest unknowns first. Each spike had a clear pass/fail question and a fallback ready.

---

## Policy tests (Nov 2) — Anurag

### What was built
`backend/tests/test_policy.py`: 109 pytest cases for the rule checker in `backend/policy.py`.
They also caught a real bug, now fixed.

### Concepts, in plain words
**Boundary testing.** Most money bugs sit at the edges, so each limit is tested exactly at the limit
(must ALLOW) and one step past it (must BLOCK): $200.00 vs $200.01, 3rd payment vs 4th.

**Fail closed.** Bad input (a float amount, a naive datetime, a set instead of a frozenset) or an
unexpected exception never ALLOWs. The tests feed garbage on purpose and expect BLOCK.

**Why money uses `Decimal`, and why that wasn't enough.** Floats can't store 0.1 exactly, so money is
`Decimal`. But Decimal arithmetic still rounds to the active *context* precision (28 digits by
default, and a caller can lower it). `1E+30 + 0.01` rounded back to `1E+30`, so a fully spent
mandate passed the budget check. Fix: add in a private exact context that raises `Inexact` on any
rounding; the evaluator turns that into BLOCK. Comparisons (`>`) are always exact, only arithmetic rounds.

### Likely interview questions
1. **Why not floats for money?** Binary floats can't represent most cents exactly; errors add up.
   *Follow-up: is Decimal always exact?* No, arithmetic rounds to context precision; we use an exact
   context that errors instead of rounding.
2. **How did you test a fail-closed design?** Pass malformed inputs and force an exception inside the
   evaluator (a Decimal subclass whose comparison raises) and assert BLOCK with `internal_evaluation_error`.
3. **How do you know a test actually tests the fix?** Ran the new tests against the old code: they fail
   there and pass after the fix.
