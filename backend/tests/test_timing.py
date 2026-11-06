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
    first_serial = next(a for a in r["agents"] if not a["parallel"])
    assert first_serial["agent"] == "bull_researcher" and first_serial["includes_join"]
    took = {a["agent"]: a["took"] for a in r["agents"]}
    # analysts count from the graph's start; the rest from the message before them
    assert took == {"market_analyst": 60.0, "news_analyst": 90.0, "bull_researcher": 30.0,
                    "bear_researcher": 25.0, "research_manager": 60.0, "portfolio_manager": 90.0}
    assert r["critical_path"] == ["news_analyst", "bull_researcher", "bear_researcher", "research_manager",
                                  "portfolio_manager"]
    assert "total 330.0s" in w.line()


def test_openers_arriving_together_are_one_step():
    """With the library's parallel first rounds, a debate's openers come out of one state update."""
    clock = _Clock()
    w = timing.Stopwatch(clock)
    w.mark("desk_pre")
    for agent, at in (("market_analyst", 60.0), ("bull_researcher", 100.0), ("bear_researcher", 100.002),
                      ("research_manager", 160.0), ("trader", 180.0), ("aggressive_analyst", 220.0),
                      ("conservative_analyst", 220.001), ("neutral_analyst", 220.003),
                      ("portfolio_manager", 280.0)):
        clock.t = at
        w.message({"type": "message", "agent": agent})
    r = w.result()
    rows = {a["agent"]: a for a in r["agents"]}
    assert (rows["bull_researcher"]["took"], rows["bear_researcher"]["took"]) == (40.0, 40.0)
    assert [rows[a]["took"] for a in ("aggressive_analyst", "conservative_analyst", "neutral_analyst")] == [40.0] * 3
    assert all(rows[a].get("together") for a in ("bull_researcher", "bear_researcher", "aggressive_analyst",
                                                 "conservative_analyst", "neutral_analyst"))
    assert not rows["research_manager"].get("together") and rows["research_manager"]["took"] == 60.0
    assert rows["bull_researcher"]["includes_join"] and "includes_join" not in rows["bear_researcher"]
    assert r["critical_path"] == ["market_analyst", "bear_researcher", "research_manager", "trader",
                                  "neutral_analyst", "portfolio_manager"]


def test_turns_are_numbered_and_summed_per_run():
    clock = _Clock()
    w = timing.Stopwatch(clock)
    w.mark("desk_pre")
    for agent, at in (("market_analyst", 10.0), ("bull_researcher", 20.0), ("bear_researcher", 30.0),
                      ("bull_researcher", 45.0), ("bear_researcher", 50.0)):
        clock.t = at
        w.message({"type": "message", "agent": agent})
    r = w.result()
    assert (r["agents"][3]["agent"], r["agents"][3]["turn"]) == ("bull_researcher", 2)
    s = timing.summarize([{**r, "total": 50.0}])
    assert s["agents"]["bull_researcher"]["mean"] == 10.0 and s["agents"]["bull_researcher#2"]["mean"] == 15.0
    assert s["agents_per_run"]["bull_researcher"]["mean"] == 25.0


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
    assert set(t["stages"]) == set(timing.STAGES) - {"leverage"} and t["total"] >= 0   # NVDA: no leverage step
    assert any(a["agent"] == "portfolio_manager" for a in t["agents"])
    monkeypatch.setenv("DESK_DATABASE_URL", db_url)
    assert cli.main(["timing", "--last", "5"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("1 runs") and "graph" in out and "portfolio_manager" in out and "(n=1)" in out
