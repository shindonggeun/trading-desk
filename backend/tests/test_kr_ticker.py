"""#477: a bare KRX code (005930) is the same instrument as 005930.KS on every path."""

import asyncio

import pytest

from desk import api
from desk.broker import Bar, MockBroker
from desk.db import make_engine
from desk.prices import PriceSource
from desk.universe import Universe
from tests.test_api import OWNER, PASSWORD
from tests.test_prices import _client

LISTED = {"005930": "KOSPI", "247540": "KOSDAQ"}


@pytest.fixture
def listing(monkeypatch):
    monkeypatch.setattr(api, "kr_market", LISTED.get)
    monkeypatch.setattr(api, "learned_kr", {})


def test_a_bare_code_gets_its_markets_suffix(listing):
    assert api.clean_ticker("005930") == "005930.KS"
    assert api.clean_ticker(" 247540 ") == "247540.KQ"
    assert api.clean_ticker("0126z0") == "0126Z0.KS"                 # a newer alphanumeric code, unknown: guessed
    assert api.kr_ticker("0126Z0") == ("0126Z0.KS", True) and api.kr_ticker("247540") == ("247540.KQ", False)


@pytest.mark.parametrize("ticker", ["005930.KS", "247540.KQ", "NVDA", "^KS11", "BRK-B", "KRW=X", "SOXL", "TQQQ"])
def test_suffixed_and_us_tickers_are_left_alone(listing, ticker):
    assert api.clean_ticker(ticker) == ticker


def test_a_learned_market_wins_and_a_broken_list_only_guesses(monkeypatch, listing):
    api.learned_kr["123456"] = ".KQ"
    assert api.clean_ticker("123456") == "123456.KQ"

    def broken(code):
        raise RuntimeError("db down")

    monkeypatch.setattr(api, "kr_market", broken)
    assert api.kr_ticker("005930") == ("005930.KS", True)


def _weekly(ticker_bars: dict[str, int], seen: list[str]):
    def yahoo(ticker, interval, count):
        seen.append(ticker)
        n = ticker_bars.get(ticker, 0)
        return [Bar(f"2026-0{1 + i // 28}-{1 + i % 28:02d}T00:00:00+00:00", 100 + i, 101 + i, 99 + i, 100 + i, 10)
                for i in range(n)]
    return yahoo


def test_the_chart_gives_the_same_bars_for_005930_and_005930_ks(db_url, monkeypatch):
    monkeypatch.setattr(api, "kr_market", api.kr_market)              # restored after create_app replaces it
    monkeypatch.setattr(api, "learned_kr", {})
    seen: list[str] = []
    source = PriceSource(MockBroker(), yahoo_candles=_weekly({"005930.KS": 5, "247540.KQ": 4}, seen))
    with _client(db_url, source, fake_runs=True) as client:
        asyncio.run(Universe(make_engine(db_url)).refresh(MockBroker()))       # the app's list (same database)
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        bare = client.get("/api/candles?ticker=005930&interval=1w&count=5").json()
        full = client.get("/api/candles?ticker=005930.KS&interval=1w&count=5").json()
        assert bare["bars"] and bare["bars"] == full["bars"] and bare["ticker"] == "005930.KS"
        kq = client.get("/api/candles?ticker=247540&interval=1w&count=4").json()
        assert kq["ticker"] == "247540.KQ" and len(kq["bars"]) == 4
    assert "005930" not in seen and "247540" not in seen                    # never asked without the suffix


def test_an_unknown_code_empty_on_kospi_is_tried_once_on_kosdaq_and_remembered(db_url, monkeypatch):
    monkeypatch.setattr(api, "kr_market", api.kr_market)
    monkeypatch.setattr(api, "learned_kr", {})
    seen: list[str] = []
    source = PriceSource(MockBroker(), yahoo_candles=_weekly({"999990.KQ": 3}, seen))
    with _client(db_url, source, fake_runs=True) as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        out = client.get("/api/candles?ticker=999990&interval=1w&count=3").json()
        assert out["ticker"] == "999990.KQ" and len(out["bars"]) == 3
        assert api.learned_kr == {"999990": ".KQ"} and api.clean_ticker("999990") == "999990.KQ"
        none = client.get("/api/candles?ticker=999991&interval=1w&count=3")
        assert none.status_code == 200 and none.json()["bars"] == []          # neither market: empty, as before
    assert seen.count("999990.KS") == 1 and seen.count("999990.KQ") == 1
    assert "999991.KQ" in seen and "999991" not in api.learned_kr


def test_the_list_wins_over_a_learned_market_and_a_listed_non_korean_code_is_left_alone(monkeypatch, listing):
    api.learned_kr["005930"] = ".KQ"                                  # learned earlier; the list now says KOSPI
    assert api.clean_ticker("005930") == "005930.KS"
    monkeypatch.setattr(api, "kr_market", lambda code: "NASDAQ")       # a code-shaped symbol listed in the US
    assert api.resolve_ticker("1ABCDE") == ("1ABCDE", False)


def test_a_source_error_on_the_guess_is_tried_on_kosdaq_and_503_only_when_both_fail(db_url, monkeypatch):
    monkeypatch.setattr(api, "kr_market", api.kr_market)              # restored after create_app replaces it
    monkeypatch.setattr(api, "learned_kr", {})

    def yahoo(ticker, interval, count):
        if ticker == "888880.KQ":
            return [Bar("2026-01-05T00:00:00+00:00", 1, 2, 1, 2, 10)]
        raise RuntimeError("yahoo down")

    source = PriceSource(MockBroker(), yahoo_candles=yahoo)
    with _client(db_url, source, fake_runs=True) as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        out = client.get("/api/candles?ticker=888880&interval=1w&count=1")
        assert out.status_code == 200 and out.json()["ticker"] == "888880.KQ"
        assert client.get("/api/candles?ticker=888881&interval=1w&count=1").status_code == 503
        assert client.get("/api/candles?ticker=NVDA&interval=1w&count=1").status_code == 503   # no retry for others


def test_favorites_store_the_suffix_and_a_legacy_bare_one_can_be_removed(db_url, monkeypatch):
    from desk.favorites import Favorites

    monkeypatch.setattr(api, "kr_market", api.kr_market)
    source = PriceSource(MockBroker(), yahoo_candles=lambda t, i, c: [])
    with _client(db_url, source, fake_runs=True) as client:
        asyncio.run(Universe(make_engine(db_url)).refresh(MockBroker()))
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        client.post("/api/favorites", json={"ticker": "247540"})
        assert [f["ticker"] for f in client.get("/api/favorites").json()] == ["247540.KQ"]
        Favorites(make_engine(db_url), lambda t: None).add(1, "005930")   # saved bare before #477
        assert client.delete("/api/favorites/005930").status_code == 204
        assert [f["ticker"] for f in client.get("/api/favorites").json()] == ["247540.KQ"]
