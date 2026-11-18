"""#653 the analysis chart's last bar carried on with the price now: buckets, the merge, the price's
source by session, the short cache, the API."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from desk import live_bars as lb
from desk.broker.base import Bar
from desk.prices import PriceSource


def _kst(s: str) -> datetime:
    return datetime.fromisoformat(s + "+09:00").astimezone(UTC)


def _et(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=lb.ET).astimezone(UTC)


def _bar(at: datetime, o=100.0, h=101.0, lo=99.0, c=100.5, **kw) -> dict:
    return {"at": at.isoformat(timespec="seconds"), "open": o, "high": h, "low": lo, "close": c, "volume": 10.0, **kw}


def test_buckets_follow_the_last_bars_grid_across_the_night():
    last = _kst("2026-11-17T15:20:00")
    assert lb.place(last, _kst("2026-11-17T15:23:10"), "5m", lb.KST) is None              # same 5-minute bar
    assert lb.place(last, _kst("2026-11-18T09:03:00"), "5m", lb.KST) == _kst("2026-11-18T09:00:00")
    us = _et("2026-11-17T15:30:00")                                                       # the US 60m grid: :30
    assert lb.place(us, _et("2026-11-18T09:45:00"), "60m", lb.ET) == _et("2026-11-18T09:30:00")
    with pytest.raises(ValueError):
        lb.place(last, _kst("2026-11-17T15:10:00"), "5m", lb.KST)


def test_days_weeks_and_months_by_the_markets_local_date():
    day = datetime(2026, 11, 17, 15, 0, tzinfo=UTC)                                      # 11/18 00:00 KST
    assert lb.place(day, _kst("2026-11-18T14:00:00"), "1d", lb.KST) is None
    assert lb.place(day, _kst("2026-11-19T08:10:00"), "1d", lb.KST) == _kst("2026-11-19T00:00:00")
    us_day = _et("2026-11-17T00:00:00")
    assert lb.place(us_day, _et("2026-11-17T19:30:00"), "1d", lb.ET) is None             # after hours: same day
    assert lb.place(us_day, _et("2026-11-18T04:05:00"), "1d", lb.ET) == _et("2026-11-18T00:00:00")   # pre-market
    week = _kst("2026-11-16T00:00:00")                                                    # Monday
    assert lb.place(week, _kst("2026-11-20T10:00:00"), "1w", lb.KST) is None
    assert lb.place(week, _kst("2026-11-23T09:00:00"), "1w", lb.KST) == _kst("2026-11-23T00:00:00")
    month = _kst("2026-11-02T00:00:00")
    assert lb.place(month, _kst("2026-12-01T09:00:00"), "1M", lb.KST) == _kst("2026-12-01T00:00:00")


def test_the_merge_carries_the_bar_on_or_opens_one_and_never_lets_an_estimate_rewrite_a_real_bar():
    bars = [_bar(_kst("2026-11-18T10:00:00"))]
    bar, state = lb.merge(bars, 102.5, _kst("2026-11-18T10:03:00"), "5m", lb.KST)
    assert state == "updated" and (bar["close"], bar["high"], bar["low"], bar["open"]) == (102.5, 102.5, 99.0, 100.0)
    assert bars[0]["close"] == 100.5                                                      # the input is not changed
    bar, state = lb.merge(bars, 98.0, _kst("2026-11-18T10:07:00"), "5m", lb.KST)
    assert state == "appended" and bar["at"] == _kst("2026-11-18T10:05:00").isoformat() and bar["open"] == 98.0
    bar, state = lb.merge(bars, 120.0, _kst("2026-11-18T10:01:00"), "5m", lb.KST, estimated=True)
    assert state == "price_only" and bar["close"] == 100.5                                # a real bar stays real
    bar, state = lb.merge(bars, 120.0, _kst("2026-11-18T22:01:00"), "5m", lb.KST, estimated=True)
    assert state == "appended" and bar["estimated"] is True
    est_bar = [_bar(_kst("2026-11-18T22:00:00"), estimated=True)]
    bar, state = lb.merge(est_bar, 101.0, _kst("2026-11-18T22:02:00"), "5m", lb.KST, estimated=True)
    assert state == "updated" and bar["close"] == 101.0                                   # an estimated bar moves on
    assert lb.merge(bars, 1.0, _kst("2026-11-18T09:00:00"), "5m", lb.KST)[1] == "price_only"   # an old price
    assert lb.merge([], 1.0, _kst("2026-11-18T09:00:00"), "5m", lb.KST) == (None, "price_only")


class Prices:
    def __init__(self, source="kis", price=101.0, bars=None, series="toss"):
        self.asked, self.quoted = [], 0
        self.q = SimpleNamespace(price=price, source=source, fetched_at="2026-11-18T01:00:00+00:00")
        self.bars, self.series = bars or [_bar(_kst("2026-11-18T09:55:00"))], series

    async def quote(self, ticker):
        self.quoted += 1
        await asyncio.sleep(0)
        return self.q

    async def candles(self, ticker, interval, count):
        self.asked.append((ticker, interval, count))
        return {"bars": self.bars, "source": self.series}


class Estimator:
    def __init__(self, state="closed", price=105.0):
        self.est = None if state is None else SimpleNamespace(market_state=state, estimate=price,
                                                              fetched_at="2026-11-18T01:00:00+00:00")

    async def estimate(self, ticker):
        return self.est


def _session(value):
    async def of(ticker):
        return value
    return of


@pytest.mark.parametrize(("ticker", "session", "est_state", "want"), [
    ("005930.KS", "post", "nxt", ("nxt", False)),                  # NXT's last trade while it runs
    ("NVDA", "regular", "open", ("kis", False)),                   # the live quote (KIS / Toss / Yahoo)
    ("SOXL", "pre", "closed", ("kis", False)),                     # pre-market trades are real prices
    ("NQ=F", "regular", "open", ("binance-estimate", True)),       # Yahoo's future is late: the estimate
    ("KORU", "closed", "closed", ("binance-estimate", True)),      # closed: the estimate
    ("^KS11", "closed", None, ("kis", False)),                     # closed, no contract: the last price there is
])
def test_the_price_comes_from_the_live_source_for_its_session(ticker, session, est_state, want):
    watched = []
    got = asyncio.run(lb.live_price(ticker, Prices(), Estimator(est_state), _session(session), watched.append))
    assert (got["source"], got["estimated"]) == want
    assert (watched == [ticker]) == (want[0] == "kis" and session != "closed")


def test_one_answer_per_key_for_a_few_seconds_and_one_fetch_at_a_time():
    clock, prices = [0.0], Prices()
    desk = lb.LiveBars(prices, Estimator("open"), _session("regular"), clock=lambda: clock[0],
                       now=lambda: _kst("2026-11-18T10:03:00"))

    async def burst():
        return await asyncio.gather(*(desk.get("005930.KS", "5m") for _ in range(5)))

    got = asyncio.run(burst())
    assert prices.quoted == 1 and len(prices.asked) == 1 and prices.asked[0] == ("005930.KS", "5m", lb.BASE_COUNT)
    assert got[0]["state"] == "appended" and got[0]["bar"]["at"] == _kst("2026-11-18T10:00:00").isoformat()
    assert got[0]["source"] == "kis" and got[0]["series_source"] == "toss" and got[0]["delay_sec"] >= 0
    asyncio.run(desk.get("005930.KS", "5m"))
    assert prices.quoted == 1                                                             # cached
    clock[0] = lb.TTL_SEC + 0.1
    asyncio.run(desk.get("005930.KS", "5m"))
    assert prices.quoted == 2


def test_how_late_a_series_runs():
    assert lb.series_delay("yahoo", "005930.KS") == 1200 and lb.series_delay("yahoo", "NQ=F") == 600
    assert lb.series_delay("yahoo", "NVDA") == 0 and lb.series_delay("toss", "005930.KS") == 0
    assert lb.series_delay("yahoo+toss", "005930.KS") == 0


def test_the_api(db_url, monkeypatch):
    from tests.test_api import OWNER, PASSWORD
    from tests.test_prices import MockBroker
    from tests.test_prices import _client as prices_client

    bars = [Bar(f"2026-11-17T{h:02d}:00:00+00:00", 100, 101, 99, 100.5, 1) for h in range(5)]
    source = PriceSource(MockBroker(), yahoo_candles=lambda *a: list(bars))
    with prices_client(db_url, source) as client:
        assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
        got = client.get("/api/candles?ticker=NQ=F&interval=5m&count=3").json()
        assert got["source"] == "yahoo" and got["delay_sec"] == 600                       # #653 added only
        assert client.get("/api/candles/live?ticker=NQ=F&interval=7m").status_code == 422

        async def fake(self, ticker, interval):
            return {"ticker": ticker, "interval": interval, "state": "updated", "price": 1.0}
        monkeypatch.setattr(lb.LiveBars, "get", fake)
        assert client.get("/api/candles/live?ticker=nq=f&interval=5m").json()["ticker"] == "NQ=F"


def test_the_live_bar_needs_a_session(db_url):
    from tests.test_api import _client

    with _client(db_url, login=False) as client:
        assert client.get("/api/candles/live?ticker=NVDA").status_code == 401
