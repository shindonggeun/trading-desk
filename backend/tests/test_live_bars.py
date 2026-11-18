"""#653 the analysis chart's last bar carried on with the price now: buckets, the merge, the price's
source by session (the quote board's rules), the short cache, the API."""

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


def _iso(at: datetime) -> str:
    return at.isoformat(timespec="seconds")


def _bar(at: datetime | str, o=100.0, h=101.0, lo=99.0, c=100.5, **kw) -> dict:
    return {"at": at if isinstance(at, str) else _iso(at), "open": o, "high": h, "low": lo, "close": c,
            "volume": 10.0, **kw}


def test_intraday_buckets_carry_the_grid_within_a_day_and_start_from_the_open_on_a_new_one():
    last = _iso(_kst("2026-11-17T15:20:00"))
    assert lb.place(last, _kst("2026-11-17T15:23:10"), "5m", "005930.KS") is None          # same 5-minute bar
    assert lb.place(last, _kst("2026-11-17T15:26:00"), "5m", "005930.KS") == _kst("2026-11-17T15:25:00")
    assert lb.place(last, _kst("2026-11-18T09:03:00"), "5m", "005930.KS") == _kst("2026-11-18T09:00:00")
    # Friday 15:30 EDT ... Monday 10:40 EST: the 120m grid from Monday's 09:30, not 67 hours on
    fri = _iso(_et("2026-10-30T15:30:00"))
    assert lb.place(fri, _et("2026-11-02T10:40:00"), "120m", "NVDA") == _et("2026-11-02T09:30:00")
    assert lb.place(fri, _et("2026-11-02T11:40:00"), "120m", "NVDA") == _et("2026-11-02T11:30:00")
    nq = _iso(_et("2026-11-17T16:55:00"))                                                  # a future keeps its grid
    assert lb.place(nq, _et("2026-11-17T18:07:00"), "5m", "NQ=F") == _et("2026-11-17T18:05:00")
    with pytest.raises(ValueError):
        lb.place(last, _kst("2026-11-17T15:10:00"), "5m", "005930.KS")


def test_days_weeks_and_months_by_the_bars_own_date_and_stamp_form():
    kr = _iso(datetime(2026, 11, 17, 15, 0, tzinfo=UTC))                                  # 11/18 00:00 KST
    assert lb.place(kr, _kst("2026-11-18T14:00:00"), "1d", "005930.KS") is None
    assert lb.place(kr, _kst("2026-11-19T08:10:00"), "1d", "005930.KS") == _kst("2026-11-19T00:00:00")
    toss_us = "2026-11-18T00:00:00+00:00"                                                  # Toss: a bare date, UTC
    assert lb.place(toss_us, _et("2026-11-18T11:00:00"), "1d", "NVDA") is None            # today's bar, not a new one
    assert lb.place(toss_us, _et("2026-11-19T04:05:00"), "1d", "NVDA") == datetime(2026, 11, 19, tzinfo=UTC)
    yahoo_us = _iso(_et("2026-11-17T00:00:00"))                                            # Yahoo: New York midnight
    assert lb.place(yahoo_us, _et("2026-11-17T19:30:00"), "1d", "NVDA") is None
    assert lb.place(yahoo_us, _et("2026-11-18T04:05:00"), "1d", "NVDA") == _et("2026-11-18T00:00:00")
    week = _iso(_kst("2026-11-16T00:00:00"))                                                # Monday
    assert lb.place(week, _kst("2026-11-20T10:00:00"), "1w", "005930.KS") is None
    assert lb.place(week, _kst("2026-11-23T09:00:00"), "1w", "005930.KS") == _kst("2026-11-23T00:00:00")
    month = _iso(_kst("2026-11-02T00:00:00"))
    assert lb.place(month, _kst("2026-12-01T09:00:00"), "1M", "005930.KS") == _kst("2026-12-01T00:00:00")


def test_the_merge_carries_the_bar_on_or_opens_one_and_only_on_a_real_trade_or_an_estimate():
    bars = [_bar(_kst("2026-11-18T10:00:00"))]
    bar, state = lb.merge(bars, 102.5, _kst("2026-11-18T10:03:00"), "5m", "005930.KS")
    assert state == "updated" and (bar["close"], bar["high"], bar["low"], bar["open"]) == (102.5, 102.5, 99.0, 100.0)
    assert bars[0]["close"] == 100.5                                                      # the input is not changed
    bar, state = lb.merge(bars, 98.0, _kst("2026-11-18T10:07:00"), "5m", "005930.KS")
    assert state == "appended" and bar["at"] == _iso(_kst("2026-11-18T10:05:00")) and bar["open"] == 98.0
    bar, state = lb.merge(bars, 120.0, _kst("2026-11-18T10:01:00"), "5m", "005930.KS", estimated=True)
    assert state == "price_only" and bar["close"] == 100.5                                # a real bar stays real
    bar, state = lb.merge(bars, 120.0, _kst("2026-11-18T22:01:00"), "5m", "005930.KS", estimated=True)
    assert state == "appended" and bar["estimated"] is True
    est_bar = [_bar(_kst("2026-11-18T22:00:00"), estimated=True)]
    bar, state = lb.merge(est_bar, 101.0, _kst("2026-11-18T22:02:00"), "5m", "005930.KS", estimated=True)
    assert state == "updated" and bar["close"] == 101.0                                   # an estimated bar moves on
    # nothing trades and no estimate (00:30 KST, a Saturday): no bar for a day that never traded
    assert lb.merge(bars, 100.5, _kst("2026-11-19T00:30:00"), "5m", "005930.KS", live=False)[1] == "price_only"
    assert lb.merge(bars, 1.0, _kst("2026-11-18T09:00:00"), "5m", "005930.KS")[1] == "price_only"   # an old price
    assert lb.merge([], 1.0, _kst("2026-11-18T09:00:00"), "5m", "005930.KS") == (None, "price_only")


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
        self.est = None if state is None else SimpleNamespace(market_state=state, estimate=price, last_close=100.0,
                                                              fetched_at="2026-11-18T01:00:00+00:00")

    async def estimate(self, ticker):
        return self.est


def _session(value):
    async def of(ticker):
        return value
    return of


def _off_hours(found):
    async def of(ticker, session, close):
        return found
    return of


TRADE = {"price": 99.0, "source": "nxt", "at": "2026-11-18T08:00:00+00:00"}


@pytest.mark.parametrize(("ticker", "session", "est_state", "off", "want"), [
    ("005930.KS", "post", "nxt", None, ("nxt", False, True)),                 # NXT's trade while it runs
    ("NVDA", "regular", "open", None, ("kis", False, True)),                  # the live quote
    ("NQ=F", "regular", "open", None, ("binance-estimate", True, True)),      # Yahoo's future is late
    ("000660.KS", "post", None, TRADE, ("nxt", False, True)),                 # the board's off-hours trade
    ("000660.KS", "post", None, None, ("kis", False, False)),                 # none: the close, not live
    ("SOXL", "pre", "closed", None, ("binance-estimate", True, True)),        # no off-hours trade: the estimate
    ("KORU", "closed", "closed", None, ("binance-estimate", True, True)),
    ("^KS11", "closed", None, None, ("kis", False, False)),                   # closed, no contract: not live
])
def test_the_price_follows_the_quote_boards_rules(ticker, session, est_state, off, want):
    watched = []
    got = asyncio.run(lb.live_price(ticker, Prices(), Estimator(est_state), _session(session), watched.append,
                                    _off_hours(off)))
    assert (got["source"], got["estimated"], got["live"]) == want
    assert (watched == [ticker]) == (session == "regular" and want[0] == "kis")


def test_an_nxt_trade_before_the_open_is_the_pre_session():
    est = Estimator("nxt")
    got = asyncio.run(lb.live_price("005930.KS", Prices(), est, _session("closed"), now=_kst("2026-11-18T08:10:00")))
    assert got["session"] == "pre"
    got = asyncio.run(lb.live_price("005930.KS", Prices(), est, _session("closed"), now=_kst("2026-11-18T17:10:00")))
    assert got["session"] == "post"


def test_one_answer_per_key_for_a_few_seconds_and_one_fetch_at_a_time():
    clock, prices = [0.0], Prices()
    desk = lb.LiveBars(prices, Estimator("open"), _session("regular"), clock=lambda: clock[0],
                       now=lambda: _kst("2026-11-18T10:03:00"))

    async def burst():
        return await asyncio.gather(*(desk.get("005930.KS", "5m") for _ in range(5)))

    got = asyncio.run(burst())
    assert prices.quoted == 1 and len(prices.asked) == 1 and prices.asked[0] == ("005930.KS", "5m", lb.BASE_COUNT)
    assert got[0]["state"] == "appended" and got[0]["bar"]["at"] == _iso(_kst("2026-11-18T10:00:00"))
    assert got[0]["source"] == "kis" and got[0]["series_source"] == "toss" and got[0]["delay_sec"] >= 0
    asyncio.run(desk.get("005930.KS", "5m"))
    assert prices.quoted == 1                                                             # cached
    clock[0] = lb.TTL_SEC + 0.1
    asyncio.run(desk.get("005930.KS", "5m"))
    assert prices.quoted == 2


def test_how_late_a_series_and_a_yahoo_price_run():
    assert lb.series_delay("yahoo", "005930.KS") == 1200 and lb.series_delay("yahoo", "NQ=F") == 600
    assert lb.series_delay("yahoo", "NVDA") == 0 and lb.series_delay("toss", "005930.KS") == 0
    assert lb.series_delay("yahoo+toss", "005930.KS") == 0
    prices = Prices(source="yahoo")
    prices.q.fetched_at = _iso(_kst("2026-11-18T10:03:00"))
    desk = lb.LiveBars(prices, Estimator("open"), _session("regular"), now=lambda: _kst("2026-11-18T10:03:00"))
    assert asyncio.run(desk.get("005930.KS", "5m"))["delay_sec"] == 1200                  # fetched now, 20 min late


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
