import asyncio
from datetime import UTC, date, datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from desk import prices as prices_mod
from desk.broker import Bar, MockBroker, TossBroker
from desk.levels import fill_levels
from desk.prices import QUOTE_TTL_SEC, PriceSource, broker_symbol
from desk.prices import yahoo_candles as real_yahoo_candles  # conftest 가 막기 전의 것
from desk.runner import make_fake_runner
from desk.settings import Settings
from tests.test_api import OWNER, PASSWORD, _wait_done


class LiveBroker(MockBroker):
    name = "toss"

    def __init__(self, price=181.5, fail=False):
        self.price, self.fail, self.calls = price, fail, 0

    async def quote(self, symbol):
        self.calls += 1
        if self.fail:
            raise ConnectionError("403 not an allowed IP")
        return self.price, "USD"

    async def candles(self, symbol, interval, count):
        return [Bar("2026-11-04T14:30:00+00:00", 1, 2, 0.5, 1.5, 100)]


def _yahoo(price=180.0):
    calls = []

    def quote(ticker):
        calls.append(ticker)
        return price, "USD"
    return quote, calls


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_broker_symbols_drop_the_yahoo_suffix():
    assert broker_symbol("005930.KS") == "005930"
    assert broker_symbol("247540.kq") == "247540"
    assert broker_symbol("BRK-B") == "BRK.B"
    assert broker_symbol("NVDA") == "NVDA"


def test_toss_quote_is_cached_for_a_few_seconds():
    broker, clock = LiveBroker(), Clock()
    yahoo, yahoo_calls = _yahoo()
    source = PriceSource(broker, yahoo_quote=yahoo, clock=clock)
    first = asyncio.run(source.quote("NVDA"))
    assert (first.price, first.currency, first.source) == (181.5, "USD", "toss")
    asyncio.run(source.quote("NVDA"))
    assert broker.calls == 1
    clock.t += QUOTE_TTL_SEC + 1
    asyncio.run(source.quote("NVDA"))
    assert broker.calls == 2 and yahoo_calls == []


def test_askers_of_one_quote_at_once_share_one_broker_call():
    """#858: the bottom zone, the playbooks and the board asked one ticker together - each called Toss."""
    class SlowBroker(LiveBroker):
        async def quote(self, symbol):
            await asyncio.sleep(0.05)
            return await super().quote(symbol)

    broker, clock = SlowBroker(), Clock()
    source = PriceSource(broker, yahoo_quote=_yahoo()[0], clock=clock)

    async def main():
        return await asyncio.gather(*(source.quote("NVDA") for _ in range(5)), source.quote("AAPL"))

    got = asyncio.run(main())
    assert [q.price for q in got] == [181.5] * 6 and broker.calls == 2
    clock.t += QUOTE_TTL_SEC + 1
    asyncio.run(source.quote("NVDA"))
    assert broker.calls == 3


@pytest.mark.parametrize("broker", [MockBroker(), LiveBroker(fail=True)], ids=["no-market-data", "toss-fails"])
def test_falls_back_to_yahoo_and_says_so(broker):
    yahoo, calls = _yahoo(179.0)
    quote = asyncio.run(PriceSource(broker, yahoo_quote=yahoo).quote("005930.KS"))
    assert (quote.price, quote.source) == (179.0, "yahoo")
    assert calls == ["005930.KS"]


def test_candles_prefer_the_broker_and_reject_odd_intervals():
    source = PriceSource(LiveBroker(), yahoo_candles=lambda *_: pytest.fail("should not reach Yahoo"))
    result = asyncio.run(source.candles("NVDA", "1m", 5))
    assert result["source"] == "toss" and result["bars"][0]["close"] == 1.5
    with pytest.raises(ValueError):
        asyncio.run(source.candles("NVDA", "5h", 5))
    fallback = PriceSource(MockBroker(), yahoo_candles=lambda t, i, c: [Bar("2026-11-04T00:00:00+00:00", 1, 1, 1, 1, 0)])
    assert asyncio.run(fallback.candles("NVDA"))["source"] == "yahoo"


def _toss(handler) -> TossBroker:
    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return handler(request)
    return TossBroker("id", "secret", http=httpx.AsyncClient(base_url="https://toss.test",
                                                                transport=httpx.MockTransport(route)))


def test_toss_reads_string_numbers_from_prices_and_candles():
    def handler(request):
        if request.url.path == "/api/v1/prices":
            assert request.url.params["symbols"] == "005930"
            return httpx.Response(200, json={"result": [
                {"symbol": "005930", "lastPrice": "74,800", "currency": "KRW"}]})
        assert request.url.params["interval"] == "1m"
        return httpx.Response(200, json={"result": {"candles": [
            {"timestamp": "2026-11-04T09:01:00+09:00", "openPrice": "74700", "highPrice": "74900",
             "lowPrice": "74600", "closePrice": "74800", "volume": "1200"},
            {"timestamp": "2026-11-04T09:00:00+09:00", "openPrice": "74500", "highPrice": "74800",
             "lowPrice": "74400", "closePrice": "74700", "volume": "900"},
        ]}})
    broker = _toss(handler)
    assert asyncio.run(broker.quote("005930")) == (74800.0, "KRW")
    bars = asyncio.run(broker.candles("005930", "1m", 2))
    assert [b.at for b in bars] == ["2026-11-04T00:00:00+00:00", "2026-11-04T00:01:00+00:00"]
    assert bars[1].close == 74800.0


def test_toss_without_a_price_raises_so_yahoo_takes_over():
    broker = _toss(lambda request: httpx.Response(200, json={"result": []}))
    yahoo, _ = _yahoo(70000.0)
    quote = asyncio.run(PriceSource(broker, yahoo_quote=yahoo).quote("005930.KS"))
    assert quote.source == "yahoo"


def test_live_quote_moves_a_computed_entry_off_the_stale_close():
    live = {"price": 210.0, "source": "toss", "fetched_at": "2026-11-04T05:00:00+00:00"}
    plan = fill_levels({"bias": "long", "entry_price": None, "stop_loss": None, "price_target": None},
                       "NVDA", "2026-11-04", lambda *_: (200.0, 6.0), live=live)
    assert (plan["entry_price"], plan["stop_loss"], plan["price_target"]) == (210.0, 201.0, 228.0)
    assert plan["reference_price"] == {"price": 210.0, "source": "toss"}
    assert plan["live_price"] == live
    model = fill_levels({"bias": "long", "entry_price": 195.0, "stop_loss": 190.0, "price_target": 230.0},
                        "NVDA", "2026-11-04", lambda *_: pytest.fail("complete plan"), live=live)
    assert model["entry_price"] == 195.0 and model["live_price"] == live


def _client(db_url, prices, **overrides):
    from datetime import timedelta

    from desk.api import create_app
    from desk.auth import Auth
    from desk.db import make_engine

    settings = Settings(database_url=db_url, fake_delay=0, **overrides)
    client = TestClient(create_app(settings, runner=make_fake_runner(0), prices=prices),
                        base_url="https://testserver")
    engine = make_engine(db_url)
    Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(OWNER, PASSWORD)
    engine.dispose()
    return client


def test_quote_endpoint_needs_a_session_and_reports_the_source(db_url):
    yahoo, _ = _yahoo()
    with _client(db_url, PriceSource(LiveBroker(), yahoo_quote=yahoo), fake_runs=True) as client:
        assert client.get("/api/quote?ticker=NVDA").status_code == 401
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        body = client.get("/api/quote?ticker=nvda").json()
        assert body["ticker"] == "NVDA" and body["price"] == 181.5 and body["source"] == "toss" and body["fetched_at"]
        assert client.get("/api/quote?ticker=NV%20DA").status_code == 422


def test_quote_endpoint_is_503_when_every_source_fails(db_url):
    def down(_):
        raise ConnectionError("yahoo down")
    with _client(db_url, PriceSource(LiveBroker(fail=True), yahoo_quote=down), fake_runs=True) as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        assert client.get("/api/quote?ticker=NVDA").status_code == 503


def test_same_day_run_records_the_live_quote_in_its_plan(db_url):
    yahoo, _ = _yahoo()
    with _client(db_url, PriceSource(LiveBroker(), yahoo_quote=yahoo), fake_runs=False) as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        run = client.post("/api/runs", json={"ticker": "NVDA", "trade_date": date.today().isoformat()}).json()
        done = _wait_done(client, run["id"])
        assert done["status"] == "done"
        assert done["plan"]["live_price"]["price"] == 181.5
        assert done["plan"]["live_price"]["source"] == "toss"
        assert done["plan"]["entry_vs_live"]["live"] == 181.5   # the fake plan enters at 182.5


def test_past_date_run_does_not_fetch_a_quote(db_url):
    broker = LiveBroker()
    with _client(db_url, PriceSource(broker), fake_runs=False) as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        run = client.post("/api/runs", json={"ticker": "NVDA", "trade_date": "2026-01-02"}).json()
        assert _wait_done(client, run["id"])["status"] == "done"
    assert broker.calls == 0


def test_live_quote_far_from_the_close_is_ignored_for_levels():
    live = {"price": 18.0, "source": "toss", "fetched_at": "2026-11-04T05:00:00+00:00"}  # after a 10:1 split
    plan = fill_levels({"bias": "long", "entry_price": None, "stop_loss": None, "price_target": None},
                       "NVDA", "2026-11-04", lambda *_: (180.0, 6.0), live=live)
    assert plan["entry_price"] == 180.0 and plan["reference_price"]["source"] == "close"
    assert "levels_warning" in plan


def test_cache_drops_expired_tickers_once_it_is_full(monkeypatch):
    from desk import prices

    monkeypatch.setattr(prices, "CACHE_MAX", 3)
    clock = Clock()
    yahoo, _ = _yahoo()
    source = PriceSource(MockBroker(), yahoo_quote=yahoo, clock=clock)
    for ticker in ("A", "B", "C"):
        asyncio.run(source.quote(ticker))
    clock.t += QUOTE_TTL_SEC + 1
    asyncio.run(source.quote("D"))
    assert list(source._quotes) == ["D"]


def test_a_batch_read_takes_the_bars_it_had_at_once_and_a_background_read_brings_new_ones():
    """#858 stale-while-revalidate for the screens' batch reads only (``stale_ok``); everyone else waits."""
    from contextlib import suppress

    from desk.prices import CANDLE_TTL_SEC, CANDLES_EMPTY_RETRY_SEC, family_key, stale_ok

    clock, answers, calls = Clock(), [], []

    def yahoo_candles(ticker, interval, count):
        calls.append(interval)
        got = answers.pop(0)
        if isinstance(got, Exception):
            raise got
        return got

    def bars(close):
        return [Bar("2026-11-20T00:00:00+00:00", 1, 2, 0.5, close, 100)]

    source = PriceSource(MockBroker(), yahoo_candles=yahoo_candles, clock=clock)
    key = family_key("^KS11", "1d", 5)

    async def last(stale: float = 0.0) -> float:
        with stale_ok(stale):
            return (await source.candles("^KS11", "1d", 5))["bars"][-1]["close"]

    async def settle():                                  # the background read, when one runs
        if (task := source._inflight.get(key)) is not None:
            with suppress(Exception):
                await task

    async def main():
        answers.append(bars(100.0))
        assert await last() == 100.0 and len(calls) == 1
        clock.t += CANDLE_TTL_SEC + 1
        answers.append(bars(101.0))
        assert await last(90) == 100.0                  # at once: the bars it had
        assert await last() == 101.0 and len(calls) == 2    # one that waits reads now: one read in all
        clock.t += CANDLE_TTL_SEC + 1
        answers.append([])                              # Yahoo throttling: an empty answer
        assert await last(90) == 101.0
        await settle()
        assert len(calls) == 3
        # (#391) the kept bars answer the empty read and stay fresh a short while: no read on every ask
        assert await last() == 101.0 and len(calls) == 3
        clock.t += CANDLES_EMPTY_RETRY_SEC + 1
        answers.append(ConnectionError("Yahoo down"))
        assert await last(90) == 101.0
        await settle()
        answers.append(ConnectionError("Yahoo down"))
        assert await last(90) == 101.0 and len(calls) == 4      # failed in the background: the kept bars stand
        await settle()
        clock.t += 120                                  # past the stale window: a batch waits like everyone
        answers.append(bars(103.0))
        assert await last(90) == 103.0 and len(calls) == 6 and not answers

    asyncio.run(main())


def test_the_background_read_never_takes_old_bars_from_another_key():
    """#858 review: a batch's stale_ok went into its background read's task, so a KRX 15-minute chart rebuilt
    its tail from the old 1m bars and kept the result as new - for single charts and alerts too."""
    from desk.prices import family_key, stale_ok

    def minute(kst, close):
        at = datetime.fromisoformat(kst).replace(tzinfo=timezone(timedelta(hours=9))).astimezone(UTC)
        return Bar(at.isoformat(timespec="seconds"), close, close + 1, close - 1, close, 1.0)

    last = {"minute": 29}

    class Toss(MockBroker):
        name = "toss"

        async def candles(self, symbol, interval, count):
            return [minute(f"2026-11-13 10:{m:02d}", 300 + m) for m in range(last["minute"] + 1)]

    clock = Clock()
    yahoo = [minute("2026-11-13 09:00", 100), minute("2026-11-13 10:00", 101)]
    source = PriceSource(Toss(), yahoo_candles=lambda *_: list(yahoo), clock=clock)

    async def main():
        assert (await source.candles("005930.KS", "15m", 10))["bars"][-1]["close"] == 329
        clock.t += 61                                   # 15m (60 s) and 1m (30 s) both past their TTL
        last["minute"] = 44                             # the broker has bars to 10:44 now
        with stale_ok(300):
            assert (await source.candles("005930.KS", "15m", 10))["bars"][-1]["close"] == 329
        await source._inflight[family_key("005930.KS", "15m", 10)]
        assert (await source.candles("005930.KS", "15m", 10))["bars"][-1]["close"] == 344

    asyncio.run(main())


def test_a_caller_that_waits_never_queues_behind_the_background_reads():
    """#858 review: a chart asking a key whose background read still waited for REFRESH_PARALLEL waited
    behind every read ahead of it; it reads at once now (and that queued read gives way)."""
    import threading

    from desk.prices import CANDLE_TTL_SEC, REFRESH_PARALLEL, stale_ok

    hold, reads, filled = threading.Event(), [], threading.Event()

    def yahoo_candles(ticker, interval, count):
        reads.append(ticker)
        if filled.is_set() and ticker != "T11":
            hold.wait(5)                                # the reads ahead in the queue do not finish
        return [Bar("2026-11-20T00:00:00+00:00", 1, 2, 0.5, 100, 100)]

    clock = Clock()
    source = PriceSource(MockBroker(), yahoo_candles=yahoo_candles, clock=clock)
    tickers = [f"T{i}" for i in range(12)]

    async def main():
        await asyncio.gather(*(source.candles(t, "1d", 5) for t in tickers))
        filled.set()
        clock.t += CANDLE_TTL_SEC + 1
        with stale_ok(90):                              # a dashboard batch: twelve background reads
            await asyncio.gather(*(source.candles(t, "1d", 5) for t in tickers))
        await asyncio.sleep(0.05)
        try:
            got = await asyncio.wait_for(source.candles("T11", "1d", 5), 2)
        finally:
            hold.set()
        assert got["bars"] and reads.count("T11") == 2 and REFRESH_PARALLEL < 11
        await asyncio.gather(*(t for t in list(source._inflight.values())), return_exceptions=True)
        assert reads.count("T11") == 2                  # its queued read gave way, never ran

    asyncio.run(main())


def test_run_still_finishes_when_no_price_source_answers(db_url):
    def down(_):
        raise ConnectionError("yahoo down")
    with _client(db_url, PriceSource(LiveBroker(fail=True), yahoo_quote=down), fake_runs=False) as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        run = client.post("/api/runs", json={"ticker": "NVDA", "trade_date": date.today().isoformat()}).json()
        done = _wait_done(client, run["id"])
        assert done["status"] == "done" and "live_price" not in done["plan"]


# --- #379 120분봉 · 국장 분봉의 늦은 구간을 토스 1분봉으로 ---------------------------------

def _min(kst: str, close: float, volume: float = 1.0) -> Bar:
    """1분봉 하나 — kst 는 "2026-11-13 09:07" 꼴."""
    at = datetime.fromisoformat(kst).replace(tzinfo=timezone(timedelta(hours=9))).astimezone(UTC)
    return Bar(at.isoformat(timespec="seconds"), close, close + 1, close - 1, close, volume)


def test_krx_buckets_count_from_the_open_and_skip_the_after_market():
    from desk.prices import _at, krx_bucket
    at = lambda s: _at(_min(s, 1).at)
    assert krx_bucket(at("2026-11-13 09:07"), 5) == at("2026-11-13 09:05")
    assert krx_bucket(at("2026-11-13 12:59"), 120) == at("2026-11-13 11:00")
    assert krx_bucket(at("2026-11-13 15:10"), 240) == at("2026-11-13 13:00")
    assert krx_bucket(at("2026-11-13 15:30"), 15) == at("2026-11-13 15:15")  # 종가 단일가는 마지막 봉에
    assert krx_bucket(at("2026-11-13 08:30"), 5) is None and krx_bucket(at("2026-11-13 16:00"), 5) is None


def test_recent_one_minute_bars_replace_the_late_tail_of_the_history():
    from desk.prices import fill_recent, fold_minutes
    history = [_min("2026-11-13 09:00", 100), _min("2026-11-13 09:05", 101), _min("2026-11-13 09:10", 102)]
    # 토스 1분봉: 09:07 부터(09:05 봉은 일부만) 09:16 까지
    recent = [_min(f"2026-11-13 09:{m:02d}", 200 + m) for m in range(7, 17)]
    folded = fold_minutes(recent, 5)
    assert [b.close for b in folded] == [209, 214, 216]
    assert folded[1].open == 210 and folded[1].high == 215 and folded[1].volume == 5
    out = fill_recent(history, recent, 5)
    # 09:05 는 토스가 뒷부분만 덮는다 — 시가는 야후, 종가는 토스, 고저는 둘을 합친다. 09:15 는 새 봉
    assert [b.close for b in out] == [100, 209, 214, 216]
    assert (out[1].open, out[1].high, out[1].low) == (101, 210, 100)
    assert out[-1].at == _min("2026-11-13 09:15", 0).at
    # 야후에 없는 구간이면 토스 것만
    assert [b.open for b in fill_recent(history[:1], recent, 5)][1] == 207
    # 장외(NXT) 1분봉만 있으면 그대로
    assert fill_recent(history, [_min("2026-11-13 19:00", 1)], 5) == history


def test_krx_minute_charts_take_the_brokers_recent_bars_and_120m_exists():
    class Toss(MockBroker):
        name = "toss"

        async def candles(self, symbol, interval, count):
            assert interval == "1m"
            return [_min(f"2026-11-13 10:{m:02d}", 300 + m) for m in range(30)]

    yahoo = [_min("2026-11-13 09:00", 100), _min("2026-11-13 10:00", 101)]
    source = PriceSource(Toss(), yahoo_candles=lambda t, i, c: list(yahoo))
    out = asyncio.run(source.candles("005930.KS", "15m", 10))
    assert out["source"] == "yahoo+toss"
    assert [b["close"] for b in out["bars"]] == [100, 314, 329]
    two_hours = asyncio.run(source.candles("005930.KS", "120m", 10))
    assert two_hours["source"] == "yahoo+toss"
    assert [(b["open"], b["close"]) for b in two_hours["bars"]] == [(100, 329)]  # 09:00 봉: 시가는 야후
    # 미국 종목 · 브로커가 없으면 야후만
    assert asyncio.run(source.candles("NVDA", "15m", 10))["source"] == "yahoo"
    plain = PriceSource(MockBroker(), yahoo_candles=lambda t, i, c: list(yahoo))
    assert asyncio.run(plain.candles("005930.KS", "15m", 10))["source"] == "yahoo"


def test_yahoo_120m_bars_pair_up_hours_inside_one_day(monkeypatch):
    import sys
    import types

    import pandas as pd

    index = pd.DatetimeIndex(["2026-11-12 13:00", "2026-11-12 14:00", "2026-11-12 15:00",
                              "2026-11-13 09:00", "2026-11-13 10:00", "2026-11-13 11:00"], tz="Asia/Seoul")
    frame = pd.DataFrame({"Open": [1, 2, 3, 4, 5, 6], "High": [1, 2, 3, 4, 5, 6], "Low": [1, 2, 3, 4, 5, 6],
                          "Close": [1, 2, 3, 4, 5, 6], "Volume": [1, 1, 1, 1, 1, 1]}, index=index)
    fake = types.SimpleNamespace(Ticker=lambda t: types.SimpleNamespace(history=lambda **kw: frame))
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    bars = real_yahoo_candles("005930.KS", "120m", 10)
    assert [(b.open, b.close, b.volume) for b in bars] == [(1, 2, 2), (3, 3, 1), (4, 5, 2), (6, 6, 1)]


def _day(kst_date: str, close: float, volume: float) -> Bar:
    """야후 일봉 하나 — 그날 0시(KST)."""
    at = datetime.fromisoformat(kst_date).replace(tzinfo=timezone(timedelta(hours=9))).astimezone(UTC)
    return Bar(at.isoformat(timespec="seconds"), close, close, close, close, volume)


def test_a_finished_krx_day_gets_its_15h_bucket_from_the_daily_close():
    """#379 운영: 야후 국장 분봉은 매일 14:59 에서 끝난다(15:00~15:30 이 없다). 장이 끝난 날은
    일봉 종가 · 남은 거래량으로 15:00 봉을 만든다. 장중인 오늘은 만들지 않는다."""
    from desk.prices import close_tail

    history = [_min("2026-11-13 09:00", 214, 10), _min("2026-11-13 14:45", 207.5, 10),
               _min("2026-11-16 09:00", 206.5, 5)]
    daily = [_day("2026-11-13", 206, 50), _day("2026-11-16", 196.8, 99)]
    at_noon = datetime(2026, 11, 16, 12, 0, tzinfo=timezone(timedelta(hours=9)))
    out = close_tail(history, daily, 15, at_noon)
    tail = out[2]
    assert len(out) == 4 and tail.at == "2026-11-13T06:00:00+00:00"          # 15:00 KST
    assert (tail.open, tail.high, tail.low, tail.close, tail.volume) == (207.5, 207.5, 206, 206, 30)  # 50 - (10 + 10)
    assert out[3] == history[2]                                                 # 오늘은 장중 — 손대지 않는다
    after_close = close_tail(history, daily, 15, datetime(2026, 11, 16, 15, 31, tzinfo=timezone(timedelta(hours=9))))
    assert after_close[-1].at == "2026-11-16T06:00:00+00:00" and after_close[-1].close == 196.8
    # 240m: 15:00 은 13:00 봉 안이다 — 새 봉이 아니라 그 봉을 닫는다
    four = [Bar("2026-11-13T04:00:00+00:00", 210, 211, 207, 207.5, 40)]
    closed = close_tail(four, daily, 240, at_noon)
    assert len(closed) == 1 and (closed[0].open, closed[0].low, closed[0].close, closed[0].volume) == (210, 206, 206, 40)
    # 그날 봉이 09:00 부터 다 있지 않으면 남은 거래량을 모른다 — 0
    assert close_tail(history[1:2], daily, 15, at_noon)[-1].volume == 0
    # 이미 15:00 뒤 봉이 있거나 일봉이 없으면 그대로
    assert close_tail([_min("2026-11-13 15:00", 206)], daily, 5, at_noon) == [_min("2026-11-13 15:00", 206)]
    assert close_tail(history[:2], [], 15, at_noon) == history[:2]


def test_after_the_close_a_krx_minute_chart_ends_with_the_official_close():
    """브로커 1분봉이 정규장에 닿지 않을 때(장 마감 뒤 NXT 애프터만) 마지막 봉은 일봉 종가다."""
    class AfterHours(MockBroker):
        name = "toss"

        async def candles(self, symbol, interval, count):
            assert interval == "1m"
            return [_min(f"2026-11-13 19:{m:02d}", 205) for m in range(30)]     # NXT 애프터만

    def yahoo(t, interval, c):
        if interval == "1d":
            return [_day("2026-11-13", 206, 50)]
        return [_min("2026-11-13 14:30", 207, 10), _min("2026-11-13 14:45", 207.5, 10)]

    out = asyncio.run(PriceSource(AfterHours(), yahoo_candles=yahoo).candles("005930.KS", "15m", 10))
    assert [(b["at"], b["close"]) for b in out["bars"]][-1] == ("2026-11-13T06:00:00+00:00", 206)
    failing = PriceSource(AfterHours(), yahoo_candles=lambda t, i, c: (_ for _ in ()).throw(OSError("down"))
                          if i == "1d" else yahoo(t, i, c))
    assert asyncio.run(failing.candles("005930.KS", "15m", 10))["bars"][-1]["close"] == 207.5   # 일봉이 없으면 분봉만


def _yahoo_fake(monkeypatch, answers):
    """yfinance stand-in: history() hands out the given frames in order and counts the asks."""
    import sys
    import types

    asked = []

    def history(**kw):
        asked.append(kw["interval"])
        return answers[min(len(asked), len(answers)) - 1]

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=lambda t: types.SimpleNamespace(history=history)))
    monkeypatch.setattr(prices_mod, "YAHOO_EMPTY_RETRY_SEC", 0.0)
    return asked


def test_an_empty_yahoo_daily_answer_is_asked_once_more(monkeypatch, caplog):
    """#861 운영: CL=F 일봉을 야후가 한 번 비워 줬다(쿠키 실패 직후 "possibly delisted") — 플레이북이 ValueError."""
    import pandas as pd

    caplog.set_level("INFO", logger="desk.prices")
    day = pd.DatetimeIndex(["2026-11-20"], tz="America/New_York")
    full = pd.DataFrame({"Open": [70.0], "High": [71.0], "Low": [69.0], "Close": [70.5], "Volume": [1.0]}, index=day)
    asked = _yahoo_fake(monkeypatch, [full.iloc[0:0], full])     # yfinance's empty answer keeps its columns
    rows = prices_mod._yahoo_rows("CL=F", "400d", "1d")
    assert [bar.close for _, bar in rows] == [70.5] and asked == ["1d", "1d"]
    assert "Yahoo returned no 1d bars for CL=F" in caplog.text


def test_empty_yahoo_minutes_are_not_asked_again(monkeypatch):
    """분봉은 장 밖이면 원래 비어 있다 — 다시 묻지 않는다(그런 읽기를 모두 늦추지 않게)."""
    import pandas as pd

    empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    asked = _yahoo_fake(monkeypatch, [empty])
    assert prices_mod._yahoo_rows("005930.KS", "5d", "5m") == [] and asked == ["5m"]
    asked.clear()
    assert prices_mod._yahoo_rows("GONE", "400d", "1d") == [] and asked == ["1d", "1d"]   # still nothing: no bars


def _days(n: int) -> list[Bar]:
    """n daily bars, oldest first, each closing at its own index (a cut shows which ones it kept)."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [Bar((start + timedelta(days=i)).isoformat(timespec="seconds"), 1, 2, 0.5, float(i), 100) for i in range(n)]


def test_one_symbols_counts_share_one_read_and_each_takes_its_last_bars():
    """#874 시세판의 1d 63, 지지 · 저항의 1d 200, 바닥 구간의 1d 5 — 같은 종목 · 주기 · 원천이면 야후 한 번을 나눠 쓴다.
    잘라 준 봉은 마지막 count 개, ``truncated`` 는 그 count 기준. 다시 읽을 땐 최근 물은 가장 큰 count 로 읽는다."""
    from desk.prices import CANDLE_TTL_SEC, MERGE_REMEMBER_SEC

    calls, clock = [], Clock()

    def yahoo_candles(ticker, interval, count):
        calls.append(count)
        return _days(150)[-count:]                     # Yahoo keeps 150 days of this one

    source = PriceSource(MockBroker(), yahoo_candles=yahoo_candles, clock=clock)

    async def asks():
        wide = await source.candles("^KS11", "1d", 200)
        return wide, *await asyncio.gather(source.candles("^KS11", "1d", 63), source.candles("^KS11", "1d", 5))

    wide, mid, small = asyncio.run(asks())
    assert calls == [200]
    assert len(wide["bars"]) == 150 and wide["truncated"]
    assert mid["bars"] == wide["bars"][-63:] and mid["truncated"] is False
    assert small["bars"] == wide["bars"][-5:] and small["truncated"] is False
    assert [b["close"] for b in small["bars"]] == [145.0, 146.0, 147.0, 148.0, 149.0]
    clock.t += CANDLE_TTL_SEC + 1
    assert len(asyncio.run(source.candles("^KS11", "1d", 5))["bars"]) == 5 and calls == [200, 200]   # the widest
    clock.t += MERGE_REMEMBER_SEC                      # nobody asked 200 lately: 5 is read as 5 again
    asyncio.run(source.candles("^KS11", "1d", 5))
    assert calls == [200, 200, 5]


def _gated_yahoo(calls):
    """A Yahoo stand-in whose reads each wait for their own release: (ticker, count) -> threading.Event. ``started``
    is set as each read begins, so a test orders the reads' ends without timing them."""
    import threading

    gates, started = {}, {}

    def yahoo_candles(ticker, interval, count):
        calls.append((ticker, count))
        started.setdefault((ticker, count), threading.Event()).set()
        gates.setdefault((ticker, count), threading.Event()).wait(5)
        return _days(count)

    def gate(ticker, count):
        return gates.setdefault((ticker, count), threading.Event())

    async def begun(ticker, count):
        await asyncio.to_thread(started.setdefault((ticker, count), threading.Event()).wait, 5)

    return yahoo_candles, gate, begun


def test_a_narrower_ask_takes_its_bars_from_a_wider_read_under_way():
    """#874 넓은 읽기가 진행 중이면 좁은 요청은 그걸 기다려 잘라 쓴다(원천 한 번)."""
    calls = []
    yahoo_candles, gate, begun = _gated_yahoo(calls)
    source = PriceSource(MockBroker(), yahoo_candles=yahoo_candles, clock=Clock())

    async def main():
        wide = asyncio.ensure_future(source.candles("NQ=F", "1d", 200))
        await begun("NQ=F", 200)                         # the wide read is under way
        narrow = asyncio.ensure_future(source.candles("NQ=F", "1d", 63))
        await asyncio.sleep(0)                           # the narrow ask joined it (or would have read)
        gate("NQ=F", 200).set()
        return await wide, await narrow

    wide, narrow = asyncio.run(main())
    assert calls == [("NQ=F", 200)]
    assert narrow["bars"] == wide["bars"][-63:] and narrow["truncated"] is False


def test_a_narrower_read_ending_last_never_takes_a_fresh_wider_ones_place():
    """#874 좁은 읽기가 진행 중일 때 넓은 요청은 기다리지 않고 넓게 읽는다. 좁은 읽기가 나중에 끝나도(이벤트로 순서를
    정한다) 신선한 넓은 항목을 덮지 않는다 — 그 뒤 좁은 요청 · 넓은 요청 모두 원천 없이 넓은 결과에서 잘라 쓴다."""
    calls = []
    yahoo_candles, gate, begun = _gated_yahoo(calls)
    source = PriceSource(MockBroker(), yahoo_candles=yahoo_candles, clock=Clock())

    async def main():
        narrow = asyncio.ensure_future(source.candles("ES=F", "1d", 63))
        await begun("ES=F", 63)
        wide = asyncio.ensure_future(source.candles("ES=F", "1d", 200))
        await begun("ES=F", 200)                         # a second read: the narrow one could not answer it
        gate("ES=F", 200).set()
        wide = await wide                                # the wider read lands first ...
        gate("ES=F", 63).set()
        narrow = await narrow                            # ... the narrower one after it
        return narrow, wide, await source.candles("ES=F", "1d", 63), await source.candles("ES=F", "1d", 200)

    narrow, wide, after63, after200 = asyncio.run(main())
    assert calls == [("ES=F", 63), ("ES=F", 200)]
    assert len(narrow["bars"]) == 63 and len(wide["bars"]) == 200
    assert after63["bars"] == wide["bars"][-63:] and after200["bars"] == wide["bars"]


def test_the_brokers_counts_and_yahoos_longer_ones_are_never_merged():
    """#874 1d 200 이하는 토스, 넘으면 야후(수정 · 비수정 봉이 다르다) — 120 이 240 의 읽기에 묶여 출처가 바뀌지 않는다.
    토스 쪽 count 끼리, 야후 쪽 count 끼리는 묶인다. 브로커가 없는 종목(지수)은 원천이 하나라 전부 묶인다."""
    class Toss(MockBroker):
        name = "toss"

        def __init__(self):
            self.calls = []

        async def candles(self, symbol, interval, count):
            self.calls.append((symbol, interval, count))
            return _days(count)

    yahoo, broker = [], Toss()

    def yahoo_candles(ticker, interval, count):
        yahoo.append((ticker, interval, count))
        return _days(count)

    source = PriceSource(broker, yahoo_candles=yahoo_candles, clock=Clock())

    async def main():
        return await asyncio.gather(*(source.candles(t, i, c) for t, i, c in (
            ("NVDA", "1d", 240), ("NVDA", "1d", 120), ("NVDA", "1d", 63), ("NVDA", "5m", 390), ("NVDA", "5m", 78),
            ("^SOX", "1d", 240), ("^SOX", "1d", 120))))

    nvda240, nvda120, nvda63, nvda5m390, nvda5m78, sox240, sox120 = asyncio.run(main())
    assert nvda240["source"] == "yahoo" and nvda120["source"] == nvda63["source"] == "toss"
    assert broker.calls == [("NVDA", "1d", 120)]
    assert sorted(yahoo) == [("NVDA", "1d", 240), ("NVDA", "5m", 390), ("^SOX", "1d", 240)]
    assert nvda63["bars"] == nvda120["bars"][-63:] and nvda5m78["bars"] == nvda5m390["bars"][-78:]
    assert sox120["bars"] == sox240["bars"][-120:] and sox120["source"] == "yahoo"


def test_a_chart_scrolled_far_back_never_widens_the_small_reads():
    """#874 MAX_COUNT 2,000 같은 긴 차트는 따로 읽는다 — 시세판의 작은 읽기가 2,000 봉에 끌려가지 않는다."""
    from desk.prices import CANDLE_TTL_SEC

    calls, clock = [], Clock()

    def yahoo_candles(ticker, interval, count):
        calls.append(count)
        return _days(count)

    source = PriceSource(MockBroker(), yahoo_candles=yahoo_candles, clock=clock)
    asyncio.run(source.candles("^KS11", "1d", 2000))
    asyncio.run(source.candles("^KS11", "1d", 63))
    clock.t += CANDLE_TTL_SEC + 1
    asyncio.run(source.candles("^KS11", "1d", 63))
    assert calls == [2000, 63, 63]


def test_a_nearly_stale_entry_answers_at_once_and_is_read_again_behind_it():
    """#874 TTL 의 80% 를 넘긴 값은 그대로 바로 답하고 뒤에서 다시 읽는다(진행 중이면 또 시작하지 않는다) — 호출자는
    그 읽기를 기다리지 않는다. 일반 호출자(STALE_OK 0)도 마찬가지: 아직 TTL 안의 값이다."""
    import threading

    from desk.prices import CANDLE_TTL_SEC, REFRESH_AHEAD, family_key

    hold, begun, calls, clock = threading.Event(), threading.Event(), [], Clock()

    def yahoo_candles(ticker, interval, count):
        calls.append(count)
        if len(calls) > 1:
            begun.set()
            hold.wait(5)                                # the background read takes its time
        return [Bar("2026-11-20T00:00:00+00:00", 1, 2, 0.5, float(len(calls)), 100)]

    source = PriceSource(MockBroker(), yahoo_candles=yahoo_candles, clock=clock)

    async def last() -> float:
        return (await source.candles("^KS11", "1d", 5))["bars"][-1]["close"]

    async def main():
        assert await last() == 1.0
        clock.t = CANDLE_TTL_SEC * REFRESH_AHEAD - 1
        assert await last() == 1.0 and len(calls) == 1          # not yet
        assert family_key("^KS11", "1d", 5) not in source._inflight
        clock.t = CANDLE_TTL_SEC * REFRESH_AHEAD + 1
        assert await last() == 1.0                              # at once, though the read below hangs
        await asyncio.to_thread(begun.wait, 5)                  # the background read is under way
        assert await last() == 1.0 and len(calls) == 2          # one background read at a time
        hold.set()
        await source._inflight[family_key("^KS11", "1d", 5)]
        clock.t += 1
        assert await last() == 2.0 and len(calls) == 2

    asyncio.run(main())


def test_an_empty_or_failed_read_is_not_read_ahead_at_every_ask():
    """#874 review: 1w(TTL 300 초)에서 빈 응답 뒤 남긴 봉은 TTL-30 초 나이로 저장된다 — REFRESH_AHEAD 문턱 위라
    요청마다 뒤에서 야후를 다시 읽었다. 재시도 시각(CANDLES_EMPTY_RETRY_SEC)까지는 앞당겨 읽지 않는다. 앞당긴 읽기가
    실패해도 마찬가지."""
    from desk.prices import CANDLES_EMPTY_RETRY_SEC, REFRESH_AHEAD, family_key

    clock, answers, calls = Clock(), [], []
    ttl = 300.0                                          # 1w

    def yahoo_candles(ticker, interval, count):
        calls.append(clock.t)
        got = answers.pop(0)
        if isinstance(got, Exception):
            raise got
        return got

    source = PriceSource(MockBroker(), yahoo_candles=yahoo_candles, clock=clock)
    key = family_key("^KS11", "1w", 156)

    async def ask():
        got = await source.candles("^KS11", "1w", 156)
        if (task := source._inflight.get(key)) is not None:   # a background read, if one started
            await asyncio.gather(task, return_exceptions=True)
        return got

    async def main():
        answers.append(_days(156))
        await ask()
        clock.t = ttl + 1
        answers.append([])                               # Yahoo throttling: an empty answer, the kept bars stand
        assert len((await ask())["bars"]) == 156 and len(calls) == 2
        for i in range(1, int(CANDLES_EMPTY_RETRY_SEC) - 1):
            clock.t = ttl + 1 + i
            assert len((await ask())["bars"]) == 156
        assert len(calls) == 2                           # no read ahead before the retry
        clock.t = ttl + 1 + CANDLES_EMPTY_RETRY_SEC      # the retry: expired again, read
        answers.append(_days(156))
        await ask()
        assert len(calls) == 3
        # a read ahead that fails: the next asks in the ahead window do not start another at once
        clock.t = calls[-1] + ttl * REFRESH_AHEAD + 1
        answers.append(ConnectionError("Yahoo down"))
        await ask()
        assert len(calls) == 4
        for i in range(1, 10):
            clock.t = calls[-1] + i
            await ask()
        assert len(calls) == 4
        clock.t = calls[-1] + CANDLES_EMPTY_RETRY_SEC + 1     # past the quiet: read ahead again
        answers.append(_days(156))
        await ask()
        assert len(calls) == 5 and not answers

    asyncio.run(main())


def test_an_empty_narrow_read_never_ages_a_fresh_wider_entry():
    """#874 review: 동시에 돌던 좁은 읽기가 넓은 읽기가 들어온 뒤 비어 오면, 신선한 넓은 항목의 시각을 당기지 않는다
    (1w: TTL 300 초 - 당겼다면 TTL-30 초 나이가 되어 30 초 뒤 만료)."""
    calls = []
    yahoo_candles, gate, begun = _gated_yahoo(calls)
    empty_for = {("^KQ11", 63)}

    def yahoo(ticker, interval, count):
        bars = yahoo_candles(ticker, interval, count)
        return [] if (ticker, count) in empty_for else bars

    clock = Clock()
    source = PriceSource(MockBroker(), yahoo_candles=yahoo, clock=clock)

    async def main():
        narrow = asyncio.ensure_future(source.candles("^KQ11", "1w", 63))
        await begun("^KQ11", 63)
        wide = asyncio.ensure_future(source.candles("^KQ11", "1w", 200))
        await begun("^KQ11", 200)
        gate("^KQ11", 200).set()
        await wide
        gate("^KQ11", 63).set()
        got = await narrow                               # empty, after the wide one landed: the wide bars answer
        clock.t = 40                                     # 40 s old of 300: fresh, no read
        return got, await source.candles("^KQ11", "1w", 200)

    narrow, wide = asyncio.run(main())
    assert len(narrow["bars"]) == 63 and len(wide["bars"]) == 200
    assert calls == [("^KQ11", 63), ("^KQ11", 200)]


def test_a_wider_ask_that_meets_an_empty_answer_gets_no_bars_until_its_retry():
    """#874 review: 계열에 63 봉이 남아 있을 때 200 요청이 빈 응답을 받으면 — 63 봉을 200 인 척 주지 않고(레벨이 짧은 봉으로
    정상처럼 계산되지 않게) 변경 전처럼 빈 봉을 준다. 재시도 시각까지 그 사이 count 는 원천 없이 같은 답, 63 이하는 남긴 봉."""
    from desk.prices import CANDLES_EMPTY_RETRY_SEC

    clock, answers, calls = Clock(), [], []

    def yahoo_candles(ticker, interval, count):
        calls.append(count)
        return answers.pop(0)

    source = PriceSource(MockBroker(), yahoo_candles=yahoo_candles, clock=clock)

    async def main():
        answers.append(_days(63))
        await source.candles("^KS11", "1w", 63)
        clock.t = 10
        answers.append([])
        first = await source.candles("^KS11", "1w", 156)
        clock.t = 11
        again, between, narrow = await asyncio.gather(source.candles("^KS11", "1w", 156),
                                                      source.candles("^KS11", "1w", 100),
                                                      source.candles("^KS11", "1w", 63))
        clock.t = 10 + CANDLES_EMPTY_RETRY_SEC + 1       # the retry: asked again
        answers.append(_days(156))
        later = await source.candles("^KS11", "1w", 156)
        return first, again, between, narrow, later

    first, again, between, narrow, later = asyncio.run(main())
    assert first["bars"] == [] and first["truncated"] and again["bars"] == [] and between["bars"] == []
    assert len(narrow["bars"]) == 63 and narrow["truncated"] is False
    assert calls == [63, 156, 156] and len(later["bars"]) == 156 and not answers


def test_a_read_ahead_is_as_wide_as_the_entry():
    """#874 review: 넓게(240) 읽힌 항목을 좁은 요청(120)만 계속 물으면 앞당긴 읽기가 120 으로 읽고 버려졌다(넓은 신선
    항목을 덮지 못해 매 요청 반복). 앞당긴 읽기는 항목만큼 넓게 읽어 그 자리를 잇는다."""
    from desk.prices import family_key

    clock, calls = Clock(), []

    def yahoo_candles(ticker, interval, count):
        calls.append((clock.t, count))
        return _days(count)

    source = PriceSource(MockBroker(), yahoo_candles=yahoo_candles, clock=clock)
    key = family_key("^KS11", "1M", 120)

    async def ask():
        await source.candles("^KS11", "1M", 120)
        if (task := source._inflight.get(key)) is not None:
            await task

    async def main():
        await source.candles("^KS11", "1M", 240)
        for t in (400, 2900, 2960, 3020, 3080):          # 1M lives 3600 s; ahead from 2880
            clock.t = t
            await ask()

    asyncio.run(main())
    assert calls == [(0.0, 240), (2900, 240)]


def test_the_price_sources_blocking_reads_run_on_the_source_threads():
    """#874 야후 봉 · 시세 읽기는 desk.pools.SOURCE(desk-source) 에서 — 화면의 to_thread 풀을 붙잡지 않는다."""
    import threading

    names = []

    def yahoo_candles(ticker, interval, count):
        names.append(threading.current_thread().name)
        return _days(count)

    def yahoo_quote(ticker):
        names.append(threading.current_thread().name)
        return 2900.0, "KRW"

    source = PriceSource(MockBroker(), yahoo_quote=yahoo_quote, yahoo_candles=yahoo_candles)

    async def main():
        await asyncio.gather(source.candles("^KS11", "1d", 5), source.quote("^KS11"))

    asyncio.run(main())
    assert len(names) == 2 and all(n.startswith("desk-source") for n in names), names


def test_a_long_charts_indicators_are_built_off_the_event_loop(monkeypatch):
    """#874 2,000 봉에 지표 전부(~25 ms)는 desk.pools.COMPUTE 에서, 시세판의 63 봉(~0.4 ms)은 그 자리에서."""
    import threading

    names, real = [], prices_mod.annotate

    def spy(rows, indicators):
        names.append(threading.current_thread().name)
        return real(rows, indicators)

    monkeypatch.setattr(prices_mod, "annotate", spy)
    source = PriceSource(MockBroker(), yahoo_candles=lambda ticker, interval, count: _days(count))

    async def main():
        chart = await source.candles("^KS11", "1d", 2000, ("rsi", "bb"))
        tile = await source.candles("^KS11", "1d", 63)
        return chart, tile

    chart, tile = asyncio.run(main())
    assert names[0].startswith("desk-compute") and names[1] == threading.main_thread().name, names
    assert len(chart["bars"]) == 2000 and "rsi" in chart["bars"][-1] and len(tile["bars"]) == 63
