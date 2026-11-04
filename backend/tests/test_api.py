import time
from datetime import timedelta

from fastapi.testclient import TestClient

from desk.api import create_app
from desk.auth import Auth
from desk.db import make_engine
from desk.runner import make_fake_runner
from desk.settings import Settings

OWNER, PASSWORD = "owner", "correct horse battery"


def _client(db_url, login=True, **overrides) -> TestClient:
    settings = Settings(database_url=db_url, fake_runs=True, fake_delay=0, **overrides)
    client = TestClient(create_app(settings, runner=make_fake_runner(0)), base_url="https://testserver")
    engine = make_engine(db_url)
    Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(OWNER, PASSWORD)
    engine.dispose()
    if login:
        assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
    return client


def _wait_done(client, run_id, headers=None):
    for _ in range(200):
        run = client.get(f"/api/runs/{run_id}", headers=headers).json()
        if run["status"] in ("done", "failed"):
            return run
        time.sleep(0.02)
    raise AssertionError("run did not finish")


def test_fake_run_streams_the_whole_desk_and_ends_with_a_plan(db_url):
    with _client(db_url) as client:
        created = client.post("/api/runs", json={"ticker": "nvda", "trade_date": "2026-11-03"})
        assert created.status_code == 201
        run = _wait_done(client, created.json()["id"])
        assert run["status"] == "done", run["error"]
        assert run["ticker"] == "NVDA"
        assert run["plan"]["rating"] == "Overweight"
        assert run["plan"]["entry_price"] == 182.5
        assert run["plan"]["reward_risk"] == 1.96

        events = client.get(f"/api/runs/{run['id']}/events").json()
        speakers = [e["agent"] for e in events if e["type"] == "message"]
        assert speakers[:4] == ["market_analyst", "sentiment_analyst", "news_analyst", "fundamentals_analyst"]
        assert speakers[-1] == "portfolio_manager"
        assert [e["type"] for e in events][-2:] == ["plan", "done"]

        # The stream replays everything after the given id and closes once the run is over.
        body = client.get(f"/api/runs/{run['id']}/stream?after=3").text
        assert body.count("data: ") == len(events) - 3


def test_portfolio_run_feeds_holdings_to_the_trader(db_url):
    with _client(db_url) as client:
        run_id = client.post("/api/runs", json={"ticker": "NVDA", "use_portfolio": True}).json()["id"]
        _wait_done(client, run_id)
        trader = next(e for e in client.get(f"/api/runs/{run_id}/events").json() if e["agent"] == "trader")
        assert "Current position in NVDA: 12 units" in trader["content"]


def test_rejects_unknown_analyst_and_odd_ticker(db_url):
    with _client(db_url) as client:
        assert client.post("/api/runs", json={"ticker": "NVDA", "analysts": ["astrology"]}).status_code == 422
        assert client.post("/api/runs", json={"ticker": "../etc"}).status_code == 422
