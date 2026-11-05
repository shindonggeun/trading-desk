import asyncio
from datetime import date, timedelta

from desk.related import RelatedSources, collect, prompt_note, relation_for
from desk.related import yahoo_daily as REAL_YAHOO_DAILY
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
    ats = [n["at"] for n in out["news"]]
    assert ats == sorted(ats, reverse=True)


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


def _noisy(start: float, returns: list[float]) -> list[tuple[date, float]]:
    first = TODAY - timedelta(days=len(returns))
    out, price = [(first, start)], start
    for i, r in enumerate(returns, 1):
        price *= 1 + r
        out.append((first + timedelta(days=i), price))
    return out


def test_correlation_and_realized_leverage_come_out_of_the_returns():
    import random
    rng = random.Random(7)
    base = [rng.gauss(0, 0.02) for _ in range(90)]
    gold = [rng.gauss(0, 0.01) for _ in range(90)]
    series = {"GDX": _noisy(80, base), "GDXU": _noisy(70, [3 * r for r in base]),
              "GDXD": _noisy(30, [-3 * r for r in base]), "GC=F": _noisy(4000, gold)}
    sources = RelatedSources(daily=lambda tickers, _end: {t: series[t] for t in tickers if t in series},
                             news=lambda _t: [], holdings=lambda _t: [])
    up = collect("GDXU", "2026-10-01", sources, today=TODAY)
    assert up["tracking"] == {"beta_20d": 3.0, "beta_60d": 3.0}
    rows = {r["ticker"]: r for r in up["instruments"]}
    assert rows["GDX"]["corr_60d"] == 1.0 and abs(rows["GC=F"]["corr_60d"]) < 0.4
    down = collect("GDXD", "2026-10-01", sources, today=TODAY)
    assert down["tracking"]["beta_60d"] == -3.0 and down["proxy"] is True
    assert "(its own index" in prompt_note(down)


def test_a_short_history_says_n_a_not_none():
    note = prompt_note({"ticker": "NEWX", "underlying": "QQQ", "leverage": 3, "tracking": {"beta_20d": None},
                        "instruments": [{"ticker": "QQQ", "role": "underlying", "price": 1.0, "as_of": "2026-11-05",
                                         "chg_1d": None, "chg_5d": None, "chg_20d": None, "vs_sma20": None,
                                         "vs_sma50": None, "corr_60d": None}]})
    assert "None" not in note and "20d n/a, 60d n/a" in note


def test_yahoo_daily_reads_the_multi_ticker_frame_and_skips_gaps(monkeypatch):
    import sys
    import types

    import pandas as pd

    days = pd.to_datetime(["2026-11-03", "2026-11-04", "2026-11-05"])
    frame = pd.concat({"Close": pd.DataFrame({"GDX": [77.6, float("nan"), 84.59], "GC=F": [3940.4, 4150.1, 4149.0]},
                                             index=days),
                       "Open": pd.DataFrame({"GDX": [1, 2, 3], "GC=F": [1, 2, 3]}, index=days)}, axis=1)
    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(download=lambda *a, **k: frame))
    out = REAL_YAHOO_DAILY(["GDX", "GC=F", "NOPE"], TODAY)
    assert out["GDX"] == [(date(2026, 11, 3), 77.6), (date(2026, 11, 5), 84.59)]
    assert len(out["GC=F"]) == 3 and "NOPE" not in out


def test_a_run_hands_the_related_brief_to_the_agents_and_keeps_it_in_the_plan(db_url, monkeypatch):
    from datetime import timedelta as td

    from fastapi.testclient import TestClient

    from desk import api
    from desk.auth import Auth
    from desk.db import make_engine
    from desk.runner import make_fake_runner
    from desk.settings import Settings
    from tests.test_api import OWNER, PASSWORD, _wait_done

    brief = {"ticker": "GDXU", "underlying": "GDX", "leverage": 3, "tracking": {"beta_20d": 3.0, "beta_60d": 3.0},
             "instruments": [{"ticker": "GDX", "role": "underlying", "price": 84.59, "as_of": "2026-11-04",
                              "chg_1d": 9.01, "chg_5d": 0.76, "chg_20d": -1.02, "vs_sma20": 0.76,
                              "vs_sma50": -6.24, "corr_60d": 1.0}]}
    monkeypatch.setattr(api, "collect_related", lambda ticker, day: brief)
    seen = []
    fake = make_fake_runner(0)

    def runner(request, emit, portfolio):
        seen.append(portfolio.render(request.ticker) if portfolio else "")
        return fake(request, emit, portfolio)

    client = TestClient(api.create_app(Settings(database_url=db_url, fake_delay=0), runner=runner),
                        base_url="https://testserver")
    engine = make_engine(db_url)
    Auth(engine, td(hours=1), td(hours=1)).create_user(OWNER, PASSWORD)
    engine.dispose()
    with client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        run = client.post("/api/runs", json={"ticker": "GDXU", "trade_date": "2026-11-04"}).json()
        done = _wait_done(client, run["id"])
    assert done["plan"]["related"] == brief
    assert "+3x daily product on GDX" in seen[0] and "underlying GDX 84.59" in seen[0]
