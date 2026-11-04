"""Run the demo agent.

  uv run python -m leash_agent attack     visits SoundHub and MegaDeals (falls for the injection)
  uv run python -m leash_agent normal     visits SoundHub only (a legitimate purchase)

Options:
  --standin    serve the stand-in store pages instead of the real stores on :8101 / :8102
  --dry-run    do not call Leash; print what would be sent
  --brain      scripted (default, same result every run) or groq (needs GROQ_API_KEY)
"""

import argparse
import asyncio
import sys
from contextlib import nullcontext

from leash_agent.brain import DEMO_TASK, GroqBrain, ScriptedBrain
from leash_agent.config import get_settings, leash_connection
from leash_agent.graph import build_graph
from leash_agent.leash import DryRunLeash, McpLeash
from leash_agent.pages import BrowserFetcher
from leash_agent.standin import serve_standin_stores


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="leash_agent", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", choices=["attack", "normal"])
    parser.add_argument("--standin", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--brain", choices=["scripted", "groq"])
    return parser.parse_args(argv)


async def run(args, settings, *, fetcher=None, leash=None) -> dict:
    names = ("soundhub", "megadeals") if args.scenario == "attack" else ("soundhub",)
    brain_name = args.brain or settings.brain
    brain = GroqBrain.from_settings(settings) if brain_name == "groq" else ScriptedBrain()
    standin = serve_standin_stores(names) if args.standin else nullcontext(None)
    with standin as standin_urls:
        stores = {n: (standin_urls or settings.stores())[n] for n in names}
        leash = leash or (DryRunLeash() if args.dry_run else McpLeash(leash_connection(settings)))
        fetcher = fetcher or BrowserFetcher(settings.chromium_path)
        async with fetcher as fetch, leash as client:
            graph = build_graph(fetch=fetch, brain=brain, leash=client, stores=stores,
                                mandate_id=settings.mandate_id,
                                max_pages=settings.max_pages_per_store)
            print(f"task    {DEMO_TASK.goal}  (mandate {settings.mandate_id}, brain {brain_name})")
            state = await graph.ainvoke({"task": DEMO_TASK, "log": []})
    for line in state["log"]:
        print(line)
    return state


def main(argv=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        state = asyncio.run(run(args, get_settings()))
    except Exception as exc:  # show one clear line, not a traceback, during a live demo
        print(f"error   {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 1 if any(a.response.get("decision") == "ERROR" for a in state["attempts"]) else 0


if __name__ == "__main__":
    sys.exit(main())
