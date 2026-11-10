"""#250: one trading-session answer, and a quote board that shows a real price whenever anything
trades - KRX stocks on NXT / after hours (with or without a Binance contract), US stocks off hours
on KIS or else Toss - and the Binance estimate only when nothing trades."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from desk import sessions
from desk.broker.mock import MockBroker
from desk.prices import Quote
from tests.test_api import OWNER, PASSWORD
from tests.test_estimate import _client

KST = sessions.KST
NY = sessions.NEW_YORK


class Calendar:
    """The broker calendar: the clock decides opening (None), these days do not trade."""

    def __init__(self, holidays=()):
        self.holidays = set(holidays)

    async def is_open(self, market, now=None):
        return None

    async def trading_day(self, market, day):
        return (market, day) not in self.holidays


def _at(zone, y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=zone).astimezone(UTC)


@pytest.mark.parametrize(("when", "expected"), [
    (_at(KST, 2026, 11, 10, 7, 59), "closed"),
    (_at(KST, 2026, 11, 10, 8, 0), "pre"),
    (_at(KST, 2026, 11, 10, 10, 0), "regular"),
    (_at(KST, 2026, 11, 10, 15, 35), "post"),
    (_at(KST, 2026, 11, 10, 19, 59), "post"),
    (_at(KST, 2026, 11, 10, 20, 0), "closed"),
    (_at(KST, 2026, 11, 14, 10, 0), "closed"),          # Saturday
])
def test_korean_sessions_by_the_clock(when, expected):
    assert asyncio.run(sessions.session(Calendar(), "005930.KS", when)) == expected


def test_a_korean_holiday_has_no_off_hours_session():
    cal = Calendar({("KR", date(2026, 11, 10))})
    assert asyncio.run(sessions.session(cal, "005930.KS", _at(KST, 2026, 11, 10, 17, 0))) == "closed"
    assert asyncio.run(sessions.session(None, "005930.KS", _at(KST, 2026, 11, 10, 17, 0))) == "post"


@pytest.mark.parametrize(("when", "expected"), [
    (_at(NY, 2026, 11, 13, 19, 59), "post"),             # Friday after-market, just before its end
    (_at(NY, 2026, 11, 13, 20, 0), "closed"),            # Friday 20:00: the weekend gap
    (_at(NY, 2026, 11, 15, 19, 59), "closed"),           # Sunday before the day market
    (_at(NY, 2026, 11, 15, 20, 0), "overnight"),         # Sunday 20:00: the day market for Monday
    (_at(NY, 2026, 11, 16, 5, 0), "pre"),
    (_at(NY, 2026, 11, 16, 17, 0), "post"),
])
def test_us_sessions_by_the_clock(when, expected):
    assert asyncio.run(sessions.session(Calendar(), "SOXL", when)) == expected


def test_a_us_holiday_closes_its_off_hours_and_the_night_before():
    thanksgiving = Calendar({("US", date(2026, 11, 26))})
    assert asyncio.run(sessions.session(thanksgiving, "SOXL", _at(NY, 2026, 11, 26, 5, 0))) == "closed"
    # Wednesday night's day market trades for Thursday: closed too
    assert asyncio.run(sessions.session(thanksgiving, "SOXL", _at(NY, 2026, 11, 25, 21, 0))) == "closed"
    assert asyncio.run(sessions.session(Calendar(), "SOXL", _at(NY, 2026, 11, 25, 21, 0))) == "overnight"


def test_other_instruments_are_regular_or_closed():
    assert asyncio.run(sessions.session(Calendar(), "^KS11", _at(KST, 2026, 11, 10, 17, 0))) == "closed"


# --- the board ------------------------------------------------------------------------------------

class Estimator:
    """estimate() for the tickers in ``ests``, else no estimate; NXT trades by KRX code."""

    def __init__(self, ests=None, nxt=None):
        self.ests, self.nxt = ests or {}, nxt or {}

    async def estimate(self, ticker):
        if ticker not in self.ests:
            raise LookupError("no contract")
        return self.ests[ticker]

    async def _nxt_trade(self, code):
        return self.nxt.get(code)

    async def candles(self, ticker, interval):
        return None

    async def aclose(self):
        return None


def _est(close=100.0, value=101.0, state="closed"):
    return SimpleNamespace(last_close=close, last_close_date="2026-11-09", method="contract", contract="XUSDT",
                           basis=None, multiple=None, market_state=state, estimate=value, change_pct=1.0,
                           fetched_at="2026-11-10T09:00:00+00:00")


def _board(db_url, monkeypatch, est, session, tickers):
    async def fixed(calendar, ticker, now=None):
        return session

    monkeypatch.setattr(sessions, "session", fixed)
    with _client(db_url, est) as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        return {r["ticker"]: r for r in client.get(f"/api/estimates?tickers={tickers}").json()["items"]}


def test_a_krx_stock_without_a_contract_shows_its_nxt_trade_after_the_close(db_url, monkeypatch):
    now = datetime.now(UTC)
    est = Estimator(nxt={"000660": (612000.0, now - timedelta(minutes=2), True),
                         "035420": (180000.0, now - timedelta(days=1), False)})   # yesterday's: not live
    rows = _board(db_url, monkeypatch, est, "post", "000660.KS,035420.KS")
    hynix, naver = rows["000660.KS"], rows["035420.KS"]
    assert (hynix["state"], hynix["price"], hynix["quote_source"], hynix["session"]) == ("live", 612000.0, "nxt", "post")
    assert naver["state"] == "unavailable" and naver["session"] == "post" and naver["quote_source"] is None


def test_a_us_stock_off_hours_falls_back_to_toss_only_when_it_moved(db_url, monkeypatch):
    name = MockBroker().name
    moved = {"SOXL": 47.1, "TQQQ": 50.0}

    async def quote(self, ticker):
        return Quote(ticker, moved[ticker], "USD", name, "2026-11-10T09:00:00+00:00")

    monkeypatch.setattr("desk.prices.PriceSource.quote", quote)
    est = Estimator({"SOXL": _est(close=46.0, value=46.5), "TQQQ": _est(close=50.0, value=50.4)})
    rows = _board(db_url, monkeypatch, est, "pre", "SOXL,TQQQ")
    assert (rows["SOXL"]["state"], rows["SOXL"]["price"], rows["SOXL"]["quote_source"]) == ("live", 47.1, "toss")
    assert rows["SOXL"]["change_pct"] == round((47.1 / 46.0 - 1) * 100, 2) and rows["SOXL"]["session"] == "pre"
    # the broker answered the close itself: no off-hours trade, the estimate stays
    assert (rows["TQQQ"]["state"], rows["TQQQ"]["quote_source"]) == ("estimate", "binance-estimate")


def test_closed_markets_show_the_estimate_with_its_source(db_url, monkeypatch):
    rows = _board(db_url, monkeypatch, Estimator({"SOXL": _est()}), "closed", "SOXL,005930.KS")
    assert (rows["SOXL"]["state"], rows["SOXL"]["quote_source"], rows["SOXL"]["session"]) == (
        "estimate", "binance-estimate", "closed")
    assert rows["005930.KS"]["state"] == "unavailable" and rows["005930.KS"]["session"] == "closed"
