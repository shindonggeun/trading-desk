from desk import cli, timing
from tests.test_api import _client, _wait_done


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_stages_and_agent_turns_with_parallel_analysts():
    clock = _Clock()
    w = timing.Stopwatch(clock)
    clock.t = 5.0
    w.mark("desk_pre")
    for agent, at in (("market_analyst", 65.0), ("news_analyst", 95.0), ("bull_researcher", 125.0),
                      ("bear_researcher", 150.0), ("research_manager", 210.0), ("portfolio_manager", 300.0)):
        clock.t = at
        w.message({"type": "message", "agent": agent})
    w.message({"type": "tool", "agent": "tool"})                 # tool calls are not turns
    clock.t = 301.0
    w.mark("graph")
    clock.t = 309.0
    w.mark("desk_post")
    clock.t = 330.0
    w.mark("consensus")
    r = w.result()
    assert r["total"] == 330.0
    assert r["stages"] == {"desk_pre": 5.0, "graph": 296.0, "desk_post": 8.0, "consensus": 21.0}
    took = {a["agent"]: a["took"] for a in r["agents"]}
    # analysts count from the graph's start; the rest from the message before them
    assert took == {"market_analyst": 60.0, "news_analyst": 90.0, "bull_researcher": 30.0,
                    "bear_researcher": 25.0, "research_manager": 60.0, "portfolio_manager": 90.0}
    assert r["critical_path"] == ["news_analyst", "bull_researcher", "bear_researcher", "research_manager",
                                  "portfolio_manager"]
    assert "total 330.0s" in w.line()


def test_summary_averages_stages_and_agents():
    t1 = {"total": 300.0, "stages": {"desk_pre": 4.0, "graph": 280.0}, "agents": [{"agent": "trader", "took": 20.0}]}
    t2 = {"total": 400.0, "stages": {"desk_pre": 6.0, "graph": 370.0}, "agents": [{"agent": "trader", "took": 40.0}]}
    s = timing.summarize([t1, t2, None, {}])
    assert s["runs"] == 2 and s["total"]["mean"] == 350.0 and s["total"]["max"] == 400.0
    assert s["stages"]["desk_pre"]["mean"] == 5.0 and s["stages"]["consensus"]["n"] == 0
    assert s["agents"]["trader"]["median"] == 30.0


def test_a_finished_run_carries_its_timing_and_the_cli_reports_it(db_url, monkeypatch, capsys):
    with _client(db_url) as client:
        created = client.post("/api/runs", json={"ticker": "nvda", "trade_date": "2026-11-03"})
        run = _wait_done(client, created.json()["id"])
    t = run["plan"]["timing"]
    assert set(t["stages"]) == set(timing.STAGES) and t["total"] >= 0
    assert any(a["agent"] == "portfolio_manager" for a in t["agents"])
    monkeypatch.setenv("DESK_DATABASE_URL", db_url)
    assert cli.main(["timing", "--last", "5"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("1 runs") and "graph" in out and "portfolio_manager" in out
