import time
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from desk.api import create_app
from desk.auth import Auth
from desk.db import events, make_engine
from desk.runner import make_fake_runner
from desk.settings import Settings
from desk.store import Store

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
        assert speakers[-2:] == ["portfolio_manager", "consensus"]   # #94 closes the chat
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


def test_runs_default_to_the_servers_profile_and_reject_unknown_ones(db_url):
    with _client(db_url) as client:
        config = client.get("/api/config").json()
        assert config["default_profile"] == "aggressive_leverage"
        assert {p["id"] for p in config["profiles"]} == {"aggressive_leverage", "balanced"}
        run = client.post("/api/runs", json={"ticker": "NVDA"}).json()
        assert run["options"]["profile"] == "aggressive_leverage"
        run = client.post("/api/runs", json={"ticker": "NVDA", "profile": "balanced"}).json()
        assert run["options"]["profile"] == "balanced"
        assert client.post("/api/runs", json={"ticker": "NVDA", "profile": "yolo"}).status_code == 422
        done = _wait_done(client, run["id"])
        assert done["plan"]["levels_source"] == {"entry_price": "model", "stop_loss": "model",
                                                 "price_target": "model"}


def test_runs_list_pages_without_overlap_or_gaps(db_url):
    with _client(db_url) as client:
        ids = [client.post("/api/runs", json={"ticker": "NVDA"}).json()["id"] for _ in range(5)]
        for run_id in ids:
            _wait_done(client, run_id)
        pages = [client.get(f"/api/runs?page={p}&size=2").json() for p in (1, 2, 3, 4)]
        assert [p["total"] for p in pages] == [5, 5, 5, 5]
        assert [len(p["items"]) for p in pages] == [2, 2, 1, 0]
        seen = [r["id"] for p in pages for r in p["items"]]
        assert sorted(seen) == sorted(ids) and len(set(seen)) == 5
        clamped = client.get("/api/runs?page=0&size=1000").json()
        assert (clamped["page"], clamped["size"]) == (1, 100)
        huge = client.get("/api/runs?page=99999999999999999999").json()
        assert (huge["page"], huge["items"]) == (100_000, [])


def test_delete_run_removes_it_with_its_events_but_not_an_active_one(db_url):
    with _client(db_url) as client:
        run_id = client.post("/api/runs", json={"ticker": "NVDA"}).json()["id"]
        _wait_done(client, run_id)
        assert client.delete(f"/api/runs/{run_id}").status_code == 204
        assert client.get(f"/api/runs/{run_id}").status_code == 404
        assert client.get(f"/api/runs/{run_id}/events").status_code == 404
        engine = make_engine(db_url)
        try:
            with engine.connect() as conn:
                left = conn.execute(select(func.count()).where(events.c.run_id == run_id)).scalar_one()
            assert left == 0
            queued = Store(engine).create_run("AAPL", "2026-11-03", {})   # no worker: stays queued
        finally:
            engine.dispose()
        assert client.delete(f"/api/runs/{queued}").status_code == 409
        assert client.get(f"/api/runs/{queued}").json()["status"] == "queued"
        engine = make_engine(db_url)
        try:
            store = Store(engine)
            store.mark_running(queued)
            failed = store.create_run("MSFT", "2026-11-03", {})
            store.mark_failed(failed, "boom")
        finally:
            engine.dispose()
        assert client.delete(f"/api/runs/{queued}").status_code == 409   # running
        assert client.delete(f"/api/runs/{failed}").status_code == 204
        assert client.delete(f"/api/runs/{run_id}").status_code == 404
        assert client.get("/api/runs").json()["total"] == 1
