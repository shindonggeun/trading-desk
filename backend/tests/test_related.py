import asyncio
from datetime import date, timedelta

from desk.related import RelatedSources, collect, prompt_note, relation_for
from desk.related import yahoo_news as REAL_YAHOO_NEWS

TODAY = date(2026, 11, 5)


def _series(start: float, step: float, days: int = 80) -> list[tuple[date, float]]:
    first = TODAY - timedelta(days=days - 1)
    return [(first + timedelta(days=i), start * (1 + step) ** i) for i in range(days)]


def _daily(tickers, end):
    base = {"GDX": 0.002, "GDXU": 0.006, "NEM": 0.003, "GC=F": 0.001}
    return {t: _series(100.0, base.get(t, -0.001)) for t in tickers if t != "B"}   # B: Yahoo had nothing


def _news(ticker):
    if ticker == "NEM":
        raise ConnectionError("down")
    return [{"title": f"Gold story {ticker}", "source": "Wire", "at": f"2026-11-05T0{len(ticker)}:00:00Z",
             "ticker": ticker},
            {"title": "Same headline everywhere", "source": "Wire", "at": "2026-11-04T10:00:00Z", "ticker": ticker}]


SOURCES = RelatedSources(daily=_daily, news=_news, holdings=lambda _t: [])


def test_leveraged_products_point_at_their_underlying_and_its_group():
    gdxu = relation_for("gdxu")
    assert (gdxu.underlying, gdxu.leverage) == ("GDX", 3)
    assert "NEM" in gdxu.peers and "GC=F" in gdxu.drivers
    soxs = relation_for("SOXS")
    assert (soxs.underlying, soxs.leverage) == ("SOXX", -3) and "NVDA" in soxs.peers
    assert relation_for("122630.KS").underlying == "069500.KS"


def test_unknown_tickers_fall_back_to_holdings_then_the_market():
    etf = relation_for("ARKK", holdings=lambda _t: ["TSLA", "ROKU", "COIN"])
    assert etf.peers == ("TSLA", "ROKU", "COIN") and "SPY" in etf.drivers
    def down(_t):
        raise ValueError("not a fund")
    plain = relation_for("ZZZZ", holdings=down)
    assert plain.peers == () and plain.drivers == ("SPY", "QQQ", "^VIX", "^TNX")
    assert "^KS11" in relation_for("035720.KS", holdings=lambda _t: []).drivers


def test_collect_summarizes_trend_correlation_tracking_and_same_day_news():
    out = collect("GDXU", TODAY.isoformat(), SOURCES, today=TODAY)
    rows = {r["ticker"]: r for r in out["instruments"]}
    assert rows["GDX"]["role"] == "underlying" and rows["NEM"]["role"] == "peer" and rows["GC=F"]["role"] == "driver"
    assert "B" not in rows                                   # nothing from Yahoo: left out, not an error
    assert rows["GDX"]["chg_1d"] == 0.2 and rows["GDX"]["vs_sma20"] > 0
    assert out["self"]["ticker"] == "GDXU" and out["leverage"] == 3
    titles = [n["title"] for n in out["news"]]
    assert titles.count("Same headline everywhere") == 1     # de-duplicated across tickers
    assert not any("NEM" in t for t in titles)               # a failing source is skipped
    assert titles == sorted(titles, key=lambda t: next(n["at"] for n in out["news"] if n["title"] == t), reverse=True)


def test_past_dates_get_no_headlines():
    assert "news" not in collect("GDXU", "2026-10-01", SOURCES, today=TODAY)


def test_prompt_note_reads_like_a_brief_and_is_empty_without_data():
    note = prompt_note(collect("GDXU", TODAY.isoformat(), SOURCES, today=TODAY))
    assert "+3x daily product on GDX" in note and "driver GC=F" in note and "Gold story GDX" in note
    assert "contradict" in note
    assert prompt_note(None) is None and prompt_note({"instruments": []}) is None


def test_a_slow_or_failing_lookup_does_not_hold_the_run(monkeypatch):
    from desk import runner

    class Request:
        ticker, trade_date = "GDXU", "2026-11-05"

    def hang(*_args):
        import time
        time.sleep(1)

    manager = runner.RunManager.__new__(runner.RunManager)
    monkeypatch.setattr(runner, "RELATED_TIMEOUT_SEC", 0.05)
    manager.related = hang
    assert asyncio.run(manager._related(Request())) is None
    manager.related = lambda *_: (_ for _ in ()).throw(ConnectionError("yahoo down"))
    assert asyncio.run(manager._related(Request())) is None


def test_the_yahoo_news_reader_keeps_only_stories_tagged_with_the_ticker(monkeypatch):
    class Ticker:
        def __init__(self, _t):
            self.news = [
                {"content": {"title": "Newmont beats", "pubDate": "2026-11-05T01:00:00Z",
                             "provider": {"displayName": "Wire"}, "finance": {"stockTickers": [{"symbol": "NEM"}]}}},
                {"content": {"title": "Burger chain pops", "pubDate": "2026-11-05T02:00:00Z",
                             "finance": {"stockTickers": [{"symbol": "MCD"}]}}},
                {"content": {"title": "Untagged gold note", "pubDate": "2026-11-05T03:00:00Z"}},
            ]

    import sys
    import types
    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=Ticker))
    items = REAL_YAHOO_NEWS("NEM")    # conftest swaps related.yahoo_news for an offline stub
    assert [i["title"] for i in items] == ["Newmont beats", "Untagged gold note"]
    assert items[0] == {"title": "Newmont beats", "source": "Wire", "at": "2026-11-05T01:00:00Z", "ticker": "NEM"}
