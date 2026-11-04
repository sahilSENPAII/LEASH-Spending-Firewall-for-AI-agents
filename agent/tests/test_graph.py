from conftest import STORES, FileFetcher, RecordingLeash

from leash_agent.brain import DEMO_TASK, ScriptedBrain
from leash_agent.graph import build_graph

BLOCKED = {
    "decision": "BLOCK",
    "reasons": [{"code": "merchant_not_allowed", "message": "Request merchant ID is not allowed."}],
}


async def _run(stores, leash):
    graph = build_graph(fetch=FileFetcher(), brain=ScriptedBrain(), leash=leash, stores=stores,
                        mandate_id="m_demo", max_pages=10)
    return await graph.ainvoke({"task": DEMO_TASK, "log": []})


async def test_attack_sends_one_megadeals_cart_and_reports_the_block():
    leash = RecordingLeash({"megadeals": BLOCKED})
    state = await _run(STORES, leash)

    ((mandate_id, merchant_id, items),) = leash.calls
    assert (mandate_id, merchant_id) == ("m_demo", "megadeals")
    assert [(i.sku, i.quantity) for i in items] == [("md-sony-xm5", 1), ("md-giftcard-100", 3)]
    (attempt,) = state["attempts"]
    assert attempt.response == BLOCKED
    log = "\n".join(state["log"])
    assert "browse  soundhub: read 3 pages, 2 products" in log
    assert "3 x $100 Gift Card (md-giftcard-100) at megadeals" in log and "NOT IN THE TASK" in log
    assert "-> Leash: BLOCK" in log and "merchant_not_allowed" in log


async def test_normal_run_pays_soundhub_once():
    leash = RecordingLeash()
    state = await _run({"soundhub": STORES["soundhub"]}, leash)
    assert [(c[1], [i.sku for i in c[2]]) for c in leash.calls] == [("soundhub", ["sh-sony-xm5"])]
    assert "-> Leash: ALLOW" in state["log"][-1]


async def test_nothing_to_buy_calls_leash_never():
    leash = RecordingLeash()
    state = await _run({}, leash)
    assert leash.calls == [] and state["attempts"] == []
    assert "nothing on the pages matches the task" in "\n".join(state["log"])


class TwoCartBrain:
    async def plan(self, task, pages):
        from decimal import Decimal

        from leash_agent.brain import Purchase

        return [
            Purchase("megadeals", "md-sony-xm5", "Sony", 1, Decimal("149.00"), "test"),
            Purchase("soundhub", "sh-sony-xm5", "Sony", 1, Decimal("179.00"), "test"),
        ]


class BrokenForMegadeals(RecordingLeash):
    async def request_payment(self, mandate_id, merchant_id, items):
        if merchant_id == "megadeals":
            raise RuntimeError("tool failed")
        return await super().request_payment(mandate_id, merchant_id, items)


async def test_one_failed_cart_is_reported_and_the_others_still_pay():
    leash = BrokenForMegadeals()
    graph = build_graph(fetch=FileFetcher(), brain=TwoCartBrain(), leash=leash, stores={},
                        mandate_id="m_demo", max_pages=10)
    state = await graph.ainvoke({"task": DEMO_TASK, "log": []})
    decisions = {a.merchant_id: a.response["decision"] for a in state["attempts"]}
    assert decisions == {"megadeals": "ERROR", "soundhub": "ALLOW"}
    assert [c[1] for c in leash.calls] == ["soundhub"]
    assert "agent_error: RuntimeError: tool failed" in "\n".join(state["log"])
