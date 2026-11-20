"""#775 the broker account is the owner's: holdings, fills and the runs that read them - not another account's."""

from datetime import timedelta

from desk import auth as auth_module
from desk.auth import Auth
from desk.db import make_engine
from tests.test_api import PASSWORD, _client, _wait_done

OTHER = "other@example.com"


def _second_account(client, db_url) -> None:
    engine = make_engine(db_url)
    Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(OTHER, PASSWORD)
    engine.dispose()
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"username": OTHER, "password": PASSWORD}).status_code == 204


def _trader(client, run_id) -> str:
    _wait_done(client, run_id)
    return next(e for e in client.get(f"/api/runs/{run_id}/events").json() if e["agent"] == "trader")["content"]


def test_the_owner_reads_the_account(db_url):
    with _client(db_url) as client:
        assert client.get("/api/portfolio").status_code == 200
        assert client.get("/api/review").status_code == 200
        run_id = client.post("/api/runs", json={"ticker": "NVDA", "use_portfolio": True}).json()["id"]
        assert "Current position in NVDA: 12 units" in _trader(client, run_id)


def test_another_account_never_gets_the_account(db_url):
    with _client(db_url) as client:
        _second_account(client, db_url)
        for path in ("/api/portfolio", "/api/portfolio/stream", "/api/review", "/api/review?days=180"):
            got = client.get(path)
            assert got.status_code == 403, path
            assert got.json() == {"detail": "only the desk's owner sees the account"}, path   # one answer, nothing held
        # a run asked with the portfolio is run without it - no position reaches the agents or the events
        run = client.post("/api/runs", json={"ticker": "NVDA", "use_portfolio": True})
        assert run.status_code == 201
        trader = _trader(client, run.json()["id"])
        assert "Current position" not in trader and "12 units" not in trader
        # the rest of the desk is still theirs to use
        assert client.get("/api/runs").status_code == 200
        assert client.get("/api/holding-watch").json()["holdings"] == []


def test_the_owner_is_one_rule_for_the_whole_desk(db_url):
    """api (portfolio, review, runs, holding watch, alert channels) and market_alerts (pushes, the timeline)
    ask desk.auth.owner_id - the first account."""
    with _client(db_url) as client:
        engine = make_engine(db_url)
        Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(OTHER, PASSWORD)
        first = auth_module.owner_id(engine)
        assert first == Auth(engine, timedelta(hours=1), timedelta(hours=1)).owner_id()
        assert client.app.state.market_alerts.owner() == first
        engine.dispose()
