"""The agent as a LangGraph graph: browse -> plan -> pay.

browse: read every store page (hidden text included).
plan:   the brain decides what to buy.
pay:    one request_payment per store cart; Leash answers ALLOW / STEP_UP / BLOCK.
"""

import operator
from collections import defaultdict
from dataclasses import dataclass
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from leash_agent.brain import Purchase, Task
from leash_agent.leash import CartItem
from leash_agent.pages import StorePage, crawl_store


@dataclass(frozen=True)
class PaymentAttempt:
    merchant_id: str
    purchases: tuple[Purchase, ...]
    response: dict


class AgentState(TypedDict, total=False):
    task: Task
    pages: list[StorePage]
    purchases: list[Purchase]
    attempts: list[PaymentAttempt]
    log: Annotated[list[str], operator.add]


def build_graph(*, fetch, brain, leash, stores: dict[str, str], mandate_id: str, max_pages: int):
    async def browse(state: AgentState) -> dict:
        pages, log = [], []
        for merchant_id, url in stores.items():
            store_pages = await crawl_store(fetch, merchant_id, url, max_pages)
            skus = {p.sku for page in store_pages for p in page.products}
            log.append(
                f"browse  {merchant_id}: read {len(store_pages)} pages, {len(skus)} products"
            )
            pages += store_pages
        return {"pages": pages, "log": log}

    async def plan(state: AgentState) -> dict:
        purchases = await brain.plan(state["task"], state["pages"])
        log = [
            f"plan    {p.quantity} x {p.name} ({p.sku}) at {p.merchant_id}, ${p.unit_price} each"
            f"{'  <-- NOT IN THE TASK' if p.outside_task else ''}\n          why: {p.reason}"
            for p in purchases
        ] or ["plan    nothing on the pages matches the task"]
        return {"purchases": purchases, "log": log}

    async def pay(state: AgentState) -> dict:
        carts = defaultdict(list)
        for p in state["purchases"]:
            carts[p.merchant_id].append(p)
        attempts, log = [], []
        for merchant_id, purchases in carts.items():
            items = [CartItem(p.sku, p.quantity, p.unit_price) for p in purchases]
            try:
                response = await leash.request_payment(mandate_id, merchant_id, items)
            except Exception as exc:  # report this cart and carry on with the others
                response = {
                    "decision": "ERROR",
                    "reasons": [{"code": "agent_error", "message": f"{type(exc).__name__}: {exc}"}],
                }
            attempts.append(PaymentAttempt(merchant_id, tuple(purchases), response))
            cart = ", ".join(f"{i.quantity} x {i.sku}" for i in items)
            log.append(f"pay     {merchant_id} [{cart}] -> Leash: {response.get('decision')}")
            log += [f"          reason: {r.get('code')}: {r.get('message')}"
                    for r in response.get("reasons") or []]
        return {"attempts": attempts, "log": log}

    graph = StateGraph(AgentState)
    graph.add_node("browse", browse)
    graph.add_node("plan", plan)
    graph.add_node("pay", pay)
    graph.add_edge(START, "browse")
    graph.add_edge("browse", "plan")
    graph.add_edge("plan", "pay")
    graph.add_edge("pay", END)
    return graph.compile()
