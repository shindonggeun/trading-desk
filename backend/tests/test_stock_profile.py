"""#295: the stock card - facts, recent sessions, indicators, the chart's levels as steps, short interest."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

from desk import stock_profile as sp
from desk.ttl_cache import TTLCache
from tests.test_api import _client


def _bars(closes: list[float]) -> list[dict]:
    start = date(2025, 1, 1)
    return [{"at": (start + timedelta(days=i)).isoformat() + "T00:00:00+00:00", "open": c, "high": c * 1.01,
             "low": c * 0.99, "close": c, "volume": 1000} for i, c in enumerate(closes)]


def test_recent_lists_the_last_ten_sessions_newest_first_and_flags_the_extremes():
    bars = _bars([100, 102, 99, 105, 101, 98, 103, 104, 97, 106, 110, 108])
    out = sp.recent(bars)
    days = out["days"]
    assert len(days) == 10 and days[0]["close"] == 108 and days[0]["change_pct"] == -1.82
    assert out["high_close"] == 110 and out["low_close"] == 97
    assert [d["close"] for d in days if d["highest_close"]] == [110]
    assert [d["close"] for d in days if d["lowest_close"]] == [97]
    assert sp.recent(_bars([50]))["days"] == [{"date": "2025-01-01", "close": 50, "change_pct": None,
                                               "highest_close": True, "lowest_close": True}]


def test_a_korean_session_keeps_its_own_date():
    # Yahoo stamps a Seoul session at local midnight = 15:00 UTC the evening before
    bars = [{"at": "2026-11-11T15:00:00+00:00", "close": 210000.0}, {"at": "2026-11-08T15:00:00+00:00", "close": 1.0}]
    bars = [bars[1], bars[0]]
    days = sp.recent(bars, "005930.KS")["days"]
    assert [d["date"] for d in days] == ["2026-11-12", "2026-11-09"]       # Thursday, Monday
    assert sp.recent(bars, "MU")["days"][0]["date"] == "2026-11-11"


def test_indicators_on_a_short_history_leave_what_cannot_be_computed_null():
    bars = _bars([100 + i for i in range(30)])                  # 30 sessions: no 60/120/250 average
    out = sp.indicators(bars, 130.0)
    ma = {m["days"]: m for m in out["moving_averages"]}
    assert ma[5]["value"] == 127.0 and ma[5]["vs_pct"] == 2.36 and ma[20]["value"] == 119.5
    assert ma[60]["value"] is None and ma[250]["vs_pct"] is None
    assert out["week52"]["full"] is False and out["week52"]["high"] == 130.29
    assert out["bollinger"]["mid"] == 119.5 and out["bollinger"]["to_upper_pct"] < 0 < out["bollinger"]["to_lower_pct"]
    assert out["rsi"] == {"value": 100.0, "zone": "overbought"}  # only up days
    short = sp.indicators(_bars([100, 101]), 101.0)
    assert short["bollinger"] is None and short["rsi"] is None


def test_steps_take_the_charts_levels_and_thick_volume_nodes_nearest_first():
    levels = {"reference_price": 100, "as_of": "2026-11-11",
              "levels": [{"price": 120, "kind": "resistance", "strength": 3.0, "basis": "swing"},
                         {"price": 104, "kind": "resistance", "strength": 1.0, "basis": "swing"},
                         {"price": 97, "kind": "support", "strength": 2.0, "basis": "swing"},
                         {"price": 90, "kind": "support", "strength": 2.0, "basis": "swing"},
                         {"price": 80, "kind": "support", "strength": 2.0, "basis": "swing"}],
              "volume_profile": {"interval": "1d", "bars": 90, "poc": 101,
                                 "nodes": [{"price": 104.3, "kind": "thick", "share": 0.1},   # on the 104 level
                                           {"price": 110, "kind": "thick", "share": 0.08},
                                           {"price": 85, "kind": "thick", "share": 0.05},
                                           {"price": 112, "kind": "thin", "share": 0.01}]}}
    out = sp.steps(levels, 100.0)
    assert [(s["step"], s["price"], s["source"]) for s in out["resistances"]] == \
        [(1, 104, "level"), (2, 110, "volume"), (3, 120, "level")]
    assert [s["price"] for s in out["supports"]] == [97, 90, 85]       # three steps, nearest first
    assert out["supports"][0]["distance_pct"] == -3.0 and out["resistances"][0]["distance_pct"] == 4.0
    assert out["poc"] == 101 and out["volume_bars"] == 90
    many = {"levels": [{"price": 100 + i, "kind": "resistance"} for i in range(1, 15)]}
    assert len(sp.steps(many, 100.0)["resistances"]) == sp.RESISTANCE_STEPS


def test_facts_and_short_interest():
    info = {"longName": "Micron Technology, Inc.", "marketCap": 9e11, "currency": "USD",
            "fullExchangeName": "NasdaqGS", "firstTradeDateMilliseconds": 454944600000,
            "sharesShort": 23385015, "shortPercentOfFloat": 0.0207, "shortRatio": 0.86,
            "dateShortInterest": 1793318400}
    f = sp.facts(info, "MU", None)
    assert f["name"] == "Micron Technology, Inc." and f["first_trade_date"] == "1984-06-01" and f["first_trade_note"] is None
    assert sp.facts({"marketCap": 0}, "005930.KS", {"name": "삼성전자", "market": "KOSPI"})["name"] == "삼성전자"
    short = sp.short_interest(info, "MU")
    assert short["available"] and short["percent_of_float"] == 2.07 and short["as_of"] == "2026-10-30"
    assert sp.short_interest(info, "005930.KS") == {"available": False, "reason": sp.KR_SHORT_NOTE}
    assert sp.short_interest({}, "AAPL")["available"] is False
    assert sp._date(-252460800000) == "1962-01-01"                          # listed before 1970 (ms)
    assert sp._date(-252460800) == "1962-01-01" and sp._date("x") is None


class _Prices:
    def __init__(self, bars, quote=None, fail=()):
        self.bars, self._quote, self.fail = bars, quote, fail

    async def candles(self, ticker, interval="1d", count=120, indicators=()):
        if "bars" in self.fail:
            raise RuntimeError("yahoo down")
        return {"bars": self.bars, "source": "yahoo"}

    async def quote(self, ticker):
        if self._quote is None:
            raise RuntimeError("no quote")
        return SimpleNamespace(price=self._quote)


def test_one_source_failing_leaves_the_rest_of_the_card(monkeypatch):
    monkeypatch.setattr(sp, "_INFO", TTLCache(sp.INFO_CACHE_MAX, sp.INFO_TTL_SEC))
    bars = _bars([100 + (i % 7) for i in range(60)])

    async def levels(prices, ticker):
        return {"levels": [{"price": 110, "kind": "resistance"}, {"price": 95, "kind": "support"}]}

    def boom(ticker):
        raise ValueError("yahoo info down")

    card = asyncio.run(sp.Profiles(_Prices(bars), info=boom, levels=levels).one("MU"))
    assert card["errors"] == {"info": "ValueError", "quote": "RuntimeError"}
    assert card["price"] == bars[-1]["close"] and card["price_source"] == "close"
    assert card["facts"]["name"] is None and card["short"] is None
    assert card["recent"]["days"] and card["indicators"]["moving_averages"]
    assert [s["price"] for s in card["levels"]["resistances"]] == [110]

    async def no_levels(prices, ticker):
        raise RuntimeError("too few bars")

    kr = asyncio.run(sp.Profiles(_Prices([], 70000.0, fail=("bars",)), info=lambda t: {},
                                 levels=no_levels, listing=lambda t: {"name": "카카오"}).one("035720.KS"))
    assert kr["errors"] == {"bars": "RuntimeError", "levels": "RuntimeError"}
    assert kr["price"] == 70000.0 and kr["recent"] is None and kr["levels"] is None
    assert kr["facts"]["name"] == "카카오" and kr["short"]["available"] is False


def test_one_card_failing_outright_does_not_fail_the_others(monkeypatch):
    monkeypatch.setattr(sp, "_INFO", TTLCache(sp.INFO_CACHE_MAX, sp.INFO_TTL_SEC))

    async def levels(prices, ticker):
        if ticker == "BAD":
            return {"levels": [{"price": 0, "kind": "support"}], "volume_profile": {"nodes": [{"price": 1, "kind": "thick"}]}}
        return {"levels": []}

    def listing(ticker):
        if ticker == "DB":
            raise ConnectionError("db blip")

    p = sp.Profiles(_Prices(_bars([10 + i for i in range(30)]), 40.0), info=lambda t: {"longName": t},
                    levels=levels, listing=listing)
    cards = {c["ticker"]: c for c in asyncio.run(p.many(["MU", "BAD", "DB"]))}
    assert cards["MU"]["errors"] == {} and cards["MU"]["recent"]
    assert cards["BAD"]["errors"] == {"levels": "ZeroDivisionError"} and cards["BAD"]["levels"] is None
    assert cards["DB"]["errors"] == {"listing": "ConnectionError"} and cards["DB"]["facts"]["name"] == "DB"

    async def broken(self, ticker):
        raise RuntimeError("boom")

    monkeypatch.setattr(sp.Profiles, "one", broken)
    assert asyncio.run(p.many(["MU"])) == [{"ticker": "MU", "error": "RuntimeError"}]


def test_a_failed_or_empty_info_is_asked_again_in_minutes_not_a_day(monkeypatch):
    monkeypatch.setattr(sp, "_INFO", TTLCache(sp.INFO_CACHE_MAX, sp.INFO_TTL_SEC))
    now = [0.0]
    answers = [ValueError("429"), {}, {"longName": "Micron"}]

    def info(ticker):
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a

    async def levels(prices, ticker):
        return {"levels": []}

    p = sp.Profiles(_Prices(_bars([1, 2, 3])), info=info, levels=levels, clock=lambda: now[0])
    assert asyncio.run(p.one("MU"))["errors"]["info"] == "ValueError"
    assert asyncio.run(p.one("MU"))["errors"]["info"] == "ValueError"      # not asked again yet
    now[0] = sp.INFO_RETRY_SEC + 1
    assert asyncio.run(p.one("MU"))["facts"]["name"] is None               # empty: kept only minutes
    now[0] = 2 * sp.INFO_RETRY_SEC + 2
    assert asyncio.run(p.one("MU"))["facts"]["name"] == "Micron" and answers == []


def test_info_is_cached_for_a_day(monkeypatch):
    monkeypatch.setattr(sp, "_INFO", TTLCache(sp.INFO_CACHE_MAX, sp.INFO_TTL_SEC))
    now = [0.0]
    asked = []

    def info(ticker):
        asked.append(ticker)
        return {"longName": ticker}

    async def levels(prices, ticker):
        return {"levels": []}

    p = sp.Profiles(_Prices(_bars([1, 2, 3])), info=info, levels=levels, clock=lambda: now[0])
    asyncio.run(p.one("MU"))
    asyncio.run(p.one("MU"))
    now[0] = sp.INFO_TTL_SEC + 1
    asyncio.run(p.one("MU"))
    assert asked == ["MU", "MU"]


def test_the_profile_api_needs_a_session_and_checks_its_tickers(db_url, monkeypatch):
    async def one(self, ticker):
        return {"ticker": ticker}

    monkeypatch.setattr(sp.Profiles, "one", one)
    with _client(db_url) as client:
        assert client.get("/api/stock/profile?ticker=mu").json() == {"ticker": "MU"}
        many = client.get("/api/stock/profile?tickers=MU,005930.KS,MU").json()
        assert many == {"items": [{"ticker": "MU"}, {"ticker": "005930.KS"}]}
        assert client.get("/api/stock/profile").status_code == 422
        assert client.get("/api/stock/profile?ticker=MU&tickers=NVDA").status_code == 422
        assert client.get("/api/stock/profile?ticker=MU,NVDA").status_code == 422
        assert client.get("/api/stock/profile?tickers=" + ",".join(f"T{i}" for i in range(31))).status_code == 422
        assert client.get("/api/stock/profile?ticker=../etc").status_code == 422


def test_the_profile_api_is_closed_without_a_session(db_url):
    with _client(db_url, login=False) as client:
        assert client.get("/api/stock/profile?ticker=MU").status_code == 401
