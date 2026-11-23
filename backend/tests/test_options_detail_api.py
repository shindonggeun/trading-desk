from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from desk.api import create_app
from desk.auth import Auth
from desk.db import make_engine
from desk.options_detail import OptionCatalog, OptionChain, OptionContract
from desk.settings import Settings
from tests.test_api import OWNER, PASSWORD, _client


class Source:
    def __init__(self):
        self.expiration = datetime.now(UTC).date() + timedelta(days=7)
        self.calls = []

    async def catalog(self, ticker):
        self.calls.append((ticker, None))
        return OptionCatalog("fixture", (self.expiration,))

    async def chain(self, ticker, expiration):
        self.calls.append((ticker, expiration))
        return OptionChain(expiration, (OptionContract("call", "call", 100, 2, 5, True),
                                        OptionContract("put", "put", 100, 3, 2, True)))


def test_options_detail_uses_session_and_exposes_selected_expiration(db_url):
    source = Source()
    app = create_app(Settings(database_url=db_url, fake_runs=True), options_provider=source)
    engine = make_engine(db_url)
    try:
        Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(OWNER, PASSWORD)
    finally:
        engine.dispose()
    path = "/api/positioning/options"
    with TestClient(app, base_url="https://testserver") as client:
        assert client.get(path, params={"ticker": "nvda"}).status_code == 401
        assert source.calls == []
        assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
        for params in ({"expiration": "2026-02-30"}, {"expiration": "20260101"}, {"strike_min": "NaN"},
                       {"strike_min": 110, "strike_max": 100}, {"ticker": "NQ=F"}):
            invalid = {"ticker": "NVDA", **params}
            assert client.get(path, params=invalid).status_code == 422
        assert source.calls == []
        response = client.get(path, params={"ticker": "nvda", "expiration": source.expiration.isoformat()})
        assert response.status_code == 200
        got = response.json()
        assert (got["ticker"], got["status"], got["source"]) == ("NVDA", "ok", "fixture")
        assert got["expiration"] == source.expiration.isoformat()
        assert got["volume"]["put_call_ratio"] == 1.5 and got["oi"]["put_call_ratio"] == 0.4
        assert source.calls == [("NVDA", None), ("NVDA", source.expiration)]


def test_options_detail_reports_unconfigured_source(db_url):
    with _client(db_url) as client:
        response = client.get("/api/positioning/options", params={"ticker": "SPY"})
        assert response.status_code == 200
        got = response.json()
        assert (got["ticker"], got["status"], got["reason"], got["source"]) == (
            "SPY", "unavailable", "no_provider", None,
        )
        assert got["expiration"] is None and got["expirations"] == []
        assert got["fetched_at"] is None and got["spot"]["price"] is None
        for metric in ("volume", "oi"):
            assert (got[metric]["status"], got[metric]["put_call_ratio"]) == ("missing", None)
        assert got["strikes"] == []
