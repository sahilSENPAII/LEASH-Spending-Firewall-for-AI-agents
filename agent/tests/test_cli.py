import urllib.request

import pytest
from conftest import HttpFetcher, RecordingLeash

from leash_agent.__main__ import main, parse_args, run
from leash_agent.config import AgentSettings
from leash_agent.standin import serve_standin_stores


def test_standin_server_serves_the_pages():
    with serve_standin_stores() as urls:
        html = urllib.request.urlopen(urls["megadeals"].replace("index.html", "p/md-sony-xm5.html"))
        assert "SYSTEM:" in html.read().decode()


async def test_attack_over_the_standin_server(capsys):
    leash = RecordingLeash({"megadeals": {"decision": "BLOCK", "reasons": []}})
    args = parse_args(["attack", "--standin"])
    state = await run(args, AgentSettings(_env_file=None), fetcher=HttpFetcher(), leash=leash)
    assert [c[1] for c in leash.calls] == ["megadeals"]
    out = capsys.readouterr().out
    assert out.startswith("task    Buy Sony WH-1000XM5")
    assert "megadeals [1 x md-sony-xm5, 3 x md-giftcard-100] -> Leash: BLOCK" in out
    assert state["attempts"][0].merchant_id == "megadeals"


async def test_normal_scenario_only_visits_soundhub():
    leash = RecordingLeash()
    state = await run(parse_args(["normal", "--standin"]), AgentSettings(_env_file=None),
                      fetcher=HttpFetcher(), leash=leash)
    assert {p.merchant_id for p in state["pages"]} == {"soundhub"}


def test_errors_print_one_line_and_exit_1(monkeypatch, capsys):
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setattr("leash_agent.__main__.get_settings",
                        lambda: AgentSettings(_env_file=None, GROQ_API_KEY=""))
    assert main(["attack", "--standin", "--dry-run", "--brain", "groq"]) == 1
    assert capsys.readouterr().err.strip() == (
        "error   ValueError: GROQ_API_KEY is not set in .env (needed for --brain groq)"
    )


def test_bad_scenario_is_rejected():
    with pytest.raises(SystemExit):
        parse_args(["shopping"])


def test_a_cart_error_makes_the_cli_exit_1(monkeypatch):
    from leash_agent.graph import PaymentAttempt

    async def fake_run(args, settings):
        return {"attempts": [PaymentAttempt("megadeals", (), {"decision": "ERROR"})]}

    monkeypatch.setattr("leash_agent.__main__.run", fake_run)
    monkeypatch.setattr("leash_agent.__main__.get_settings", lambda: None)
    assert main(["attack", "--standin"]) == 1
