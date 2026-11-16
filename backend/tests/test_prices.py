import asyncio
from datetime import UTC, date, datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

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
