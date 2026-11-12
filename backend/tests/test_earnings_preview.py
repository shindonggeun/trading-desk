"""#293: the earnings preview - parsing the free sources, the implied and past moves, the cache and the API."""

import asyncio
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from desk import earnings_preview as ep
from tests.test_api import OWNER, PASSWORD, _client

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 11, 20, tzinfo=UTC)


def test_estimates_are_read_per_period_with_growth_in_percent():
    rows = {"0q": {"avg": -1.238, "low": -2.67, "high": -0.56, "yearAgoEps": -0.475, "numberOfAnalysts": 5,
                   "growth": -1.6063, "currency": "USD"},
            "+1y": {"avg": 9.07e8, "low": 8.7e8, "high": 9.4e8, "yearAgoRevenue": 1.46e8, "numberOfAnalysts": 17.0,
                    "growth": 5.2093, "currency": "USD"},
            "0y": {"avg": float("nan")}}
    out = ep.estimates(rows)
    assert out["this_quarter"] == {"avg": -1.238, "low": -2.67, "high": -0.56, "analysts": 5, "year_ago": -0.475,
                                   "growth_pct": -160.6, "currency": "USD"}
    assert out["next_year"]["year_ago"] == 1.46e8 and out["next_year"]["analysts"] == 17
    assert "this_year" not in out and ep.estimates({}) is None


def _report(y, m, d, hh, mm, est, act, surprise=None):
    return (datetime(y, m, d, hh, mm, tzinfo=NY), {"EPS Estimate": est, "Reported EPS": act, "Surprise(%)": surprise})


def test_history_reads_the_time_of_day_and_the_surprise_and_finds_the_next_report():
    rows = ep.history([_report(2026, 8, 12, 7, 0, -0.86, -0.68, 21.2), _report(2026, 11, 12, 8, 0, -1.24, float("nan")),
                       _report(2026, 5, 13, 16, 5, 2.0, 2.5), _report(2026, 2, 12, 12, 0, 1.0, 0.9)])
    assert [r["date"] for r in rows] == ["2026-11-12", "2026-08-12", "2026-05-13", "2026-02-12"]
    assert [r["when"] for r in rows] == ["pre", "pre", "after", "during"]
    assert rows[0]["actual"] is None and rows[0]["beat"] is None
    assert rows[1]["surprise_pct"] == 21.2 and rows[1]["beat"] is True
    assert rows[2]["surprise_pct"] == 25.0                    # computed when Yahoo gives none
    assert rows[3]["beat"] is False
    nxt = ep.next_report(rows, [], NOW)
    assert nxt == {"date": "2026-11-12", "when": "pre", "estimate": -1.24, "time_kst": "11-12 22:00", "days": 1}
    # nothing ahead in the history: Yahoo's calendar date, no time
    assert ep.next_report(rows[1:], [date(2027, 2, 10)], NOW)["date"] == "2027-02-10"
    assert ep.next_report(rows[1:], [], NOW) is None
    # Korean reports: the Korean date, no time of day (Yahoo's times for them are not the release)
    kr = ep.history([(datetime(2026, 10, 28, 22, tzinfo=NY), {"EPS Estimate": 13710.0, "Reported EPS": 11462.0})], True)
    assert kr[0]["date"] == "2026-10-29" and kr[0]["when"] is None


def test_targets_and_ratings():
    t = ep.targets({"current": 154.69, "high": 410.0, "low": 84.0, "mean": 278.05, "median": 287.5})
    assert t["upside_pct"] == 79.7 and t["median"] == 287.5
    assert ep.targets({}) is None and ep.targets(None) is None
    r = ep.ratings([{"period": "-1m", "strongBuy": 1, "buy": 1, "hold": 1, "sell": 0, "strongSell": 0},
                    {"period": "0m", "strongBuy": 2, "buy": 13, "hold": 6, "sell": 1, "strongSell": 0}])
    assert r == {"strong_buy": 2, "buy": 13, "hold": 6, "sell": 1, "strong_sell": 0, "total": 22, "buy_pct": 68}
    assert ep.ratings([{"period": "0m", "strongBuy": 0, "buy": 0, "hold": 0, "sell": 0, "strongSell": 0}]) is None


def test_the_expiry_must_include_the_report():
    exp = ["2026-11-13", "2026-11-12", "2026-11-20"]
    assert ep.pick_expiry(exp, date(2026, 11, 12), "pre") == "2026-11-12"      # before the open: that day's expiry
    assert ep.pick_expiry(exp, date(2026, 11, 12), "after") == "2026-11-13"    # after the close: the next one
    assert ep.pick_expiry(exp, date(2026, 11, 12), None) == "2026-11-13"
    assert ep.pick_expiry(["2026-11-06"], date(2026, 11, 12), "pre") is None


def _q(strike, bid, ask, last=0.0):
    return {"strike": strike, "bid": bid, "ask": ask, "lastPrice": last}


def test_the_implied_move_is_the_at_the_money_straddle_over_the_price():
    calls = [_q(150, 12.0, 12.4), _q(155, 10.0, 10.4), _q(160, 8.0, 8.4)]
    puts = [_q(150, 8.0, 8.4), _q(155, 10.0, 10.4), _q(160, 12.0, 12.4)]
    m = ep.implied_move(calls, puts, 154.69, "2026-11-13")
    assert m == {"expiry": "2026-11-13", "strike": 155.0, "call": 10.2, "put": 10.2, "move_pct": 13.2}
    # no quote: the last trade; a quote wider than half its mid is no price; no common strike
    assert ep.implied_move([_q(155, 0, 0, 9.0)], [_q(155, 0, 0, 11.0)], 155.0, "x")["move_pct"] == 12.9
    assert ep.implied_move([_q(155, 1.0, 9.0)], [_q(155, 10.0, 10.4)], 155.0, "x") is None
    assert ep.implied_move([_q(155, 0, 5.0, 9.0)], [_q(155, 10.0, 10.4)], 155.0, "x") is None   # one side only
    assert ep.implied_move([_q(150, 1, 1.1)], [_q(160, 1, 1.1)], 155.0, "x") is None
    assert ep.implied_move(calls, puts, 0, "x") is None


def test_past_moves_take_the_first_session_that_could_react():
    closes = [(date(2026, 8, 11), 100.0), (date(2026, 8, 12), 110.0), (date(2026, 8, 13), 99.0),
              (date(2026, 5, 13), 50.0), (date(2026, 5, 14), 45.0)]
    closes.sort()
    reports = [{"date": "2026-11-12", "when": "pre", "actual": None},          # not reported yet: skipped
               {"date": "2026-08-12", "when": "pre", "actual": 1.0},           # before the open: that day
               {"date": "2026-05-13", "when": "after", "actual": 1.0},         # after the close: the next day
               {"date": "2026-01-01", "when": "pre", "actual": 1.0}]           # before the bars: skipped
    out = ep.past_moves(reports, closes)
    assert out["moves"] == [{"date": "2026-08-12", "session": "2026-08-12", "move_pct": 10.0},
                            {"date": "2026-05-13", "session": "2026-05-14", "move_pct": -10.0}]
    assert out["avg_abs_pct"] == 10.0 and out["up"] == 1 and out["n"] == 2
    # a Korean report without a time reacts on its own day
    kr = ep.past_moves([{"date": "2026-08-12", "when": None, "actual": 1.0}], closes, unknown_same_day=True)
    assert kr["moves"][0]["session"] == "2026-08-12"
    assert ep.past_moves(reports, []) is None


NAVER = {"financeInfo": {
    "trTitleList": [{"isConsensus": "N", "title": "2026.06.", "key": "202606"},
                    {"isConsensus": "Y", "title": "2026.09.", "key": "202609"}],
    "rowList": [{"title": "매출액", "columns": {"202606": {"value": "1,714,995"}, "202609": {"value": "2,007,657"}}},
                {"title": "영업이익", "columns": {"202606": {"value": "894,924"}, "202609": {"value": "1,069,435"}}},
                {"title": "지배주주순이익", "columns": {"202606": {"value": "712,695"}, "202609": {"value": "-"}}},
                {"title": "영업이익률", "columns": {"202606": {"value": "52.18"}, "202609": {"value": "53.27"}}}]}}


def test_naver_consensus_columns_in_eok_won():
    rows = ep.naver_consensus(NAVER)
    assert rows[1] == {"period": "2026.09", "consensus": True, "revenue": 2007657.0, "op_income": 1069435.0,
                       "net_income": None, "op_margin": 53.27}             # every key, null when Naver has none
    assert rows[0]["consensus"] is False and ep.naver_consensus({}) == []


def test_a_preview_keeps_what_it_got_and_names_what_has_no_free_source(monkeypatch):
    def yahoo(ticker, now):
        return {"eps": {"this_quarter": {"avg": 1.0}}, "next": None}, ["targets: HTTPError"]

    monkeypatch.setattr(ep, "yahoo", yahoo)
    monkeypatch.setattr(ep, "naver", lambda t: ({"unit": "억원", "quarter": []}, ["kr_consensus.annual: ConnectError"]))
    us = ep.build("NBIS", NOW)
    assert us["eps"] and us["targets"] is None and us["kr_consensus"] is None
    assert us["errors"] == ["targets: HTTPError"] and list(us["sources"]) == ["estimates"]
    assert us["sources"]["estimates"] == {"source": "Yahoo Finance", "at": NOW.isoformat(timespec="seconds")}
    assert set(us) == set(ep.blank("X", []))                # the same keys as an empty preview
    assert {u["id"] for u in us["unavailable"]} >= {"whisper", "bloomberg", "ebitda"}
    kr = ep.build("005930.KS", NOW)
    assert kr["kr_consensus"]["unit"] == "억원" and "kr_consensus.annual: ConnectError" in kr["errors"]


def test_the_cache_fetches_once_and_sooner_near_the_report():
    calls = []
    clock = [0.0]

    def fetch(ticker):
        calls.append(ticker)
        return {"eps": {"x": 1}, "next": {"days": 5 if ticker == "MU" else 0}}

    cache = ep.Cache(fetch, clock=lambda: clock[0])

    async def go():
        return await asyncio.gather(cache.get("MU"), cache.get("MU"), cache.get("NBIS"))
    asyncio.run(go())
    assert sorted(calls) == ["MU", "NBIS"]                 # one fetch per ticker, even at the same time
    clock[0] = ep.NEAR_TTL_SEC + 1                          # NBIS reports today: refreshed after 30 min
    asyncio.run(cache.get("MU"))
    asyncio.run(cache.get("NBIS"))
    assert calls.count("NBIS") == 2 and calls.count("MU") == 1
    assert ep.ttl({}) == ep.FAIL_TTL_SEC                    # nothing at all: asked again soon


def test_preview_api_needs_a_session_and_cleans_the_ticker(db_url, monkeypatch):
    async def get(ticker):
        return {"ticker": ticker, "eps": None}

    monkeypatch.setattr(ep.CACHE, "get", get)
    monkeypatch.setattr(ep, "checkpoints", lambda t: [{"title_ko": "헤드라인"}])
    with _client(db_url, login=False) as client:
        assert client.get("/api/earnings/preview?ticker=NBIS").status_code == 401
        assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
        res = client.get("/api/earnings/preview?ticker=nbis")
        assert res.status_code == 200 and res.json()["ticker"] == "NBIS"
        assert res.json()["checkpoints"] == [{"title_ko": "헤드라인"}]
        assert client.get("/api/earnings/preview?ticker=NB%20IS").status_code == 422


def test_checkpoints_are_the_tickers_digested_topic_headlines(monkeypatch):
    from desk import news_digest, sector_news

    cache = sector_news.Cache()
    cache.topics = {"nvidia": {"headlines": [
        {"title": "a", "url": "https://x/1", "seen": "2026-11-11T10:00:00+00:00"},
        {"title": "b", "url": "https://x/2", "seen": "2026-11-11T12:00:00+00:00"},
        {"title": "c", "url": "https://x/3", "seen": "2026-11-11T11:00:00+00:00"}]}}
    digest = news_digest.Digest(news_digest.fake_digest)
    digest.reads = {"https://x/1": {"title_ko": "가", "why_ko": "이유", "sentiment": {"pos": 10, "neg": 70},
                                    "impact": "high"},
                    "https://x/2": {"title_ko": "나", "why_ko": None, "sentiment": None, "impact": "low"}}
    monkeypatch.setattr(sector_news, "CACHE", cache)
    monkeypatch.setattr(news_digest, "DIGEST", digest)
    out = ep.checkpoints("NVDA")
    assert [c["title_ko"] for c in out] == ["나", "가"]           # newest first; no read: left out
    assert ep.checkpoints("KO") == []


def test_the_time_of_day_boundaries_and_an_unannounced_time():
    at = lambda h, m: datetime(2026, 11, 12, h, m, tzinfo=NY)
    assert [ep._when(at(h, m), False) for h, m in ((9, 29), (9, 30), (15, 59), (16, 0))] == \
        ["pre", "during", "during", "after"]
    assert ep._when(at(0, 0), False) is None                   # no time announced: midnight on Yahoo
    assert ep.pick_expiry(["2026-11-12", "2026-11-13"], date(2026, 11, 12), None) == "2026-11-13"


def test_next_report_days_on_the_markets_own_date():
    pending = {"at": "2026-11-11T21:00:00+00:00", "date": "2026-11-11", "when": "after", "estimate": 1.0,
               "actual": None}
    # the day after an after-close report whose actual is not in yet: days -1 (no implied move for it)
    assert ep.next_report([pending], [], datetime(2026, 11, 12, 20, tzinfo=UTC))["days"] == -1
    kr = {"at": "2027-01-27T00:00:00+00:00", "date": "2027-01-27", "when": None, "estimate": 1.0, "actual": None}
    # 09:00 KST on the 26th is still the 25th in New York: a Korean report counts in Korean days
    assert ep.next_report([kr], [], datetime(2027, 1, 26, 0, tzinfo=UTC), korean=True)["days"] == 1


def test_the_shown_history_is_the_next_report_and_the_last_reported_ones():
    rows = [{"at": f"2027-0{m}-01", "actual": None} for m in (4, 3, 2)] + \
           [{"at": f"2026-{m:02d}-01", "actual": 1.0} for m in range(12, 0, -1)]
    shown = ep._shown(rows)
    assert shown[0]["at"] == "2027-02-01" and len(shown) == 1 + ep.HISTORY
    assert all(r["actual"] is not None for r in shown[1:])


class FakeTicker:
    """yfinance.Ticker with one part broken."""

    def __init__(self, *_):
        import pandas as pd

        self.earnings_estimate = pd.DataFrame({"avg": [1.0], "numberOfAnalysts": [3]}, index=["0q"])
        self.revenue_estimate = None
        self.calendar = {"Earnings Date": [datetime(2027, 2, 3, 0, 0)]}   # a datetime, not a date
        self.analyst_price_targets = {"current": 10.0, "mean": 12.0}
        self.recommendations_summary = None
        self.info = {}
        self.options = ()

    def get_earnings_dates(self, limit=12):
        raise ConnectionError("down")


def test_one_broken_part_leaves_the_rest(monkeypatch):
    import yfinance

    from desk import prices

    monkeypatch.setattr(yfinance, "Ticker", FakeTicker)
    monkeypatch.setattr(prices, "yahoo_candles", lambda *a: [])
    out, errors = ep.yahoo("NBIS", NOW)
    assert out["eps"]["this_quarter"]["avg"] == 1.0 and out["targets"]["upside_pct"] == 20.0
    assert out["next"]["date"] == "2027-02-03" and "history: ConnectionError" in errors


def test_a_failed_fetch_is_an_empty_preview_kept_briefly_not_a_500():
    calls = []

    def fetch(ticker):
        calls.append(ticker)
        raise RuntimeError("yahoo changed")

    cache = ep.Cache(fetch, clock=lambda: 0.0)
    first = asyncio.run(cache.get("ZZZZ"))
    assert first["errors"] == ["preview: RuntimeError"] and first["eps"] is None
    asyncio.run(cache.get("ZZZZ"))
    assert calls == ["ZZZZ"]                                 # kept for FAIL_TTL_SEC


def test_the_cache_keeps_at_most_its_limit_and_a_slow_fetch_is_still_kept():
    import threading

    gate = threading.Event()

    def fetch(ticker):
        if ticker == "SLOW":
            gate.wait(5)
        return {"eps": {"x": 1}, "next": None, "ticker": ticker}

    cache = ep.Cache(fetch, clock=lambda: 0.0, limit=2)

    async def go():
        for t in ("A", "B", "C"):
            await cache.get(t)
        assert list(cache._values) == ["B", "C"]             # the least recently used left
        waiting = await cache.get("SLOW", wait=0.05)
        assert waiting["errors"] == ["preview: still loading"]
        gate.set()
        await asyncio.sleep(0.2)
        assert (await cache.get("SLOW"))["ticker"] == "SLOW"   # the fetch went on and was kept
    asyncio.run(go())
