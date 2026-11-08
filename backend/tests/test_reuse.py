"""#171 step 6: a re-run of the same ticker, day, horizon and language within DESK_REUSE_REPORTS_MIN
takes over the news and fundamentals reports; the price and sentiment work runs again."""

from datetime import timedelta as td

from fastapi.testclient import TestClient

from desk import api
from desk.auth import Auth
from desk.db import make_engine
from desk.events import StateDiffer
from desk.runner import RunRequest, make_fake_runner
from desk.settings import Settings
from tests.test_api import OWNER, PASSWORD, _wait_done


def _client(db_url, minutes, graph=None):
    client = TestClient(api.create_app(Settings(database_url=db_url, fake_delay=0, reuse_reports_min=minutes),
                                       runner=graph or make_fake_runner(0)),
                        base_url="https://testserver")
    engine = make_engine(db_url)
    Auth(engine, td(hours=1), td(hours=1)).create_user(OWNER, PASSWORD)
    engine.dispose()
    return client


def _run(client, **body):
    run = client.post("/api/runs", json={"ticker": "NVDA", "trade_date": "2026-11-04", **body}).json()
    done = _wait_done(client, run["id"])
    return done, client.get(f"/api/runs/{run['id']}/events").json()


def _said(events, agent):
    return [e for e in events if e["type"] == "message" and e["agent"] == agent]


def test_a_rerun_takes_over_news_and_fundamentals_and_redoes_the_rest(db_url):
    seen = []
    fake = make_fake_runner(0)

    def graph(request, emit, portfolio):
        seen.append(request)
        return fake(request, emit, portfolio)

    client = _client(db_url, 30, graph)
    with client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        first, _ = _run(client)
        second, events = _run(client)
    assert first["status"] == second["status"] == "done"
    assert not seen[0].reused and seen[1].graph_analysts() == ["market", "social"]
    for agent in ("news_analyst", "fundamentals_analyst"):
        said = _said(events, agent)
        assert len(said) == 1 and said[0]["data"]["reused"]["run_id"] == first["id"]
        assert said[0]["data"]["reused"]["minutes"] == 0
    assert len(_said(events, "market_analyst")) == 1          # fresh
    tools = [e["content"] for e in events if e["type"] == "tool"]
    assert "get_news" not in tools and "get_stock_data" in tools
    assert second["plan"]["timing"]["reused"] == ["fundamentals", "news"]
    # the run's stored options do not carry the reports
    assert "reused" not in second["options"] or second["options"]["reused"] is None


def test_no_reuse_across_horizons_or_when_off(db_url):
    client = _client(db_url, 30)
    with client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        _run(client, horizon="swing")
        _, other = _run(client, horizon="intraday")
    assert all(not (e.get("data") or {}).get("reused") for e in other)


def test_off_by_default(db_url):
    client = _client(db_url, 0)
    with client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        _run(client)
        _, events = _run(client)
    assert all(not (e.get("data") or {}).get("reused") for e in events)


def test_a_reused_report_is_not_reused_again_and_one_analyst_always_runs(db_url):
    client = _client(db_url, 30)
    with client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        first, _ = _run(client)
        _run(client)
        _, third = _run(client)
        # news only: nothing left for the graph, so nothing is reused
        _, alone = _run(client, analysts=["news"])
    assert _said(third, "news_analyst")[0]["data"]["reused"]["run_id"] == first["id"]
    assert all(not (e.get("data") or {}).get("reused") for e in alone)


def test_the_differ_does_not_repeat_a_seeded_report():
    d = StateDiffer()
    d.seed("news_report", "old news")
    assert d.feed([], {"news_report": "old news", "market_report": "fresh"}) == [
        {"type": "message", "agent": "market_analyst", "content": "fresh", "data": None}]
    assert RunRequest("NVDA", "2026-11-04", reused={"news": "x"}).graph_analysts() == ["market", "social",
                                                                                       "fundamentals"]
