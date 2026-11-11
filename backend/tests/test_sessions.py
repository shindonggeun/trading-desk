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

    def __init__(self, ests=None, nxt=None, overtime=None):
        self.ests, self.nxt, self.overtime = ests or {}, nxt or {}, overtime or {}
        self.asked_overtime = []

    async def estimate(self, ticker):
        if ticker not in self.ests:
            raise LookupError("no contract")
        return self.ests[ticker]

    async def _nxt_trade(self, code):
        return self.nxt.get(code)

    async def _overtime_trade(self, code):
        self.asked_overtime.append(code)
        return self.overtime.get(code)

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


# --- #283: the KRX's own after-hours trading for stocks not on NXT ---------------------------------

def _bars(*rows):
    return [{"localDateTime": f"20261111{t}00", "currentPrice": p} for t, p in rows]


def test_overtime_bars_take_the_close_and_the_last_after_hours_trade():
    from desk.estimate import overtime_bars

    day = date(2026, 11, 11)
    assert overtime_bars(_bars(("1520", 31950), ("1530", 32000), ("1539", 32000)), day) is None   # not yet
    closing = overtime_bars(_bars(("1530", 32000), ("1540", 32000), ("1559", 32000)), day)   # at the close
    assert (closing.price, closing.close, closing.kind) == (32000, 32000, "closing")
    single = overtime_bars(_bars(("1530", 32000), ("1540", 32000), ("1600", 31950), ("1750", 31900)), day)
    assert (single.price, single.kind) == (31900, "single")
    assert single.traded_at == datetime(2026, 11, 11, 8, 50, tzinfo=UTC)
    after = overtime_bars(_bars(("1530", 32000), ("1800", 31850)), day)   # the 18:00 auction is the last
    assert (after.price, after.kind) == (31850, "single")
    assert overtime_bars(_bars(("1600", 31950)), date(2026, 11, 12)) is None                    # another day
    assert overtime_bars([{"localDateTime": "x"}, {"currentPrice": 1}, "junk"], day) is None
    assert overtime_bars({"not": "a list"}, day) is None


def test_naver_overtime_refuses_a_malformed_code():
    from desk.estimate import naver_overtime

    assert naver_overtime("../x?y") is None and naver_overtime("12345") is None


def test_a_krx_stock_not_on_nxt_shows_its_krx_after_hours_trade(db_url, monkeypatch):
    from desk.estimate import Overtime

    at = datetime(2026, 11, 11, 8, 1, tzinfo=UTC)
    est = Estimator(nxt={"000660": (612000.0, datetime.now(UTC) - timedelta(minutes=2), True)},
                    overtime={"035720": Overtime(31900.0, at, 32000.0, "single"),
                              "000660": Overtime(600000.0, at, 610000.0, "single")})
    rows = _board(db_url, monkeypatch, est, "post", "035720.KS,000660.KS,035420.KS")
    kakao, hynix, naver = rows["035720.KS"], rows["000660.KS"], rows["035420.KS"]
    assert (kakao["state"], kakao["price"], kakao["quote_source"], kakao["overtime"]) ==         ("live", 31900.0, "krx-overtime", "single")
    assert kakao["close"] == 32000.0 and kakao["change_pct"] == -0.31 and kakao["traded_at"] == at.isoformat()
    assert kakao["close_date"] == datetime.now(UTC).astimezone(sessions.KST).date().isoformat()
    assert hynix["quote_source"] == "nxt" and hynix["price"] == 612000.0          # NXT first
    assert naver["state"] == "unavailable"                                          # neither: as before


@pytest.mark.parametrize("session", ["pre", "closed"])
def test_the_krx_after_hours_trade_is_only_read_after_the_close(db_url, monkeypatch, session):
    est = Estimator(overtime={"035720": object()})
    rows = _board(db_url, monkeypatch, est, session, "035720.KS")
    assert rows["035720.KS"]["quote_source"] != "krx-overtime" and est.asked_overtime == []


def test_naver_overtime_asks_for_todays_kst_window_and_survives_bad_answers(monkeypatch):
    import httpx

    from desk import estimate

    asked = []

    def fake_get(url, params=None, **kw):
        asked.append((url, params))
        body = fake_get.body
        return httpx.Response(fake_get.status, json=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(estimate.httpx, "get", fake_get)
    late_utc = datetime(2026, 11, 10, 23, 30, tzinfo=UTC)          # 08:30 KST on the 11th
    fake_get.status, fake_get.body = 200, _bars(("1530", 32000), ("1610", 31950))
    ot = estimate.naver_overtime("035720", late_utc)
    assert ot.price == 31950 and asked[-1][0].endswith("/035720/minute")
    assert asked[-1][1] == {"startDateTime": "202611111520", "endDateTime": "202611112000"}
    fake_get.status = 500
    assert estimate.naver_overtime("035720", late_utc) is None
    fake_get.status, fake_get.body = 200, {"error": "x"}
    assert estimate.naver_overtime("035720", late_utc) is None
    assert estimate.naver_overtime("035720" + chr(10), late_utc) is None and len(asked) == 3


def test_a_stock_seen_on_nxt_never_takes_the_krx_after_hours_path():
    from desk.estimate import BinanceEstimator, Overtime

    at = datetime(2026, 11, 11, 7, 30, tzinfo=UTC)
    reads = []
    nxt = {"005930": (205000.0, at, True)}

    def overtime(code):
        reads.append(code)
        return Overtime(31900.0, at, 32000.0, "single")

    est = BinanceEstimator(after_hours=lambda code: nxt.get(code), overtime=overtime)

    async def run():
        await est._nxt_trade("005930")                 # Samsung shows on NXT once
        nxt.clear()                                     # ...then its NXT read fails
        est._nxt.clear()
        assert await est._nxt_trade("005930") is None
        assert await est._overtime_trade("005930") is None            # not the combined feed
        first = await est._overtime_trade("035720")
        again = await est._overtime_trade("035720")                   # cached
        await est.aclose()
        return first, again

    first, again = asyncio.run(run())
    assert first is again and first.price == 31900.0 and reads == ["035720"]
    assert BinanceEstimator()._overtime is None                       # off unless the app turns it on
