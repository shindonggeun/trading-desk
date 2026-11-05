import asyncio
from datetime import UTC, datetime

from desk import live
from desk.prices import Quote
from tests.test_api import OWNER, PASSWORD


def test_market_hours_follow_the_tickers_exchange():
    tue_kst_10 = datetime(2026, 11, 3, 1, 0, tzinfo=UTC)        # 10:00 KST, 20:00 ET the day before
    assert live.market_open("005930.KS", tue_kst_10) and not live.market_open("NVDA", tue_kst_10)
    tue_et_10 = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)         # 10:00 ET
    assert live.market_open("NVDA", tue_et_10) and not live.market_open("005930.KS", tue_et_10)
    saturday = datetime(2026, 11, 7, 15, 0, tzinfo=UTC)
    assert not live.market_open("NVDA", saturday) and live.market_open("BTC-USD", saturday)
    # 수능일: KRX opens an hour late
    assert not live.market_open("005930.KS", datetime(2026, 11, 19, 0, 30, tzinfo=UTC))


class Prices:
    def __init__(self, fail_candles=False):
        self.fail_candles = fail_candles

    async def quote(self, ticker):
        return Quote(ticker, 200.13, "USD", "toss", "2026-11-05T15:00:00+00:00")

    async def candles(self, ticker, interval, count):
        if self.fail_candles:
            raise ConnectionError("down")
        assert interval == "1m"
        bars = [{"at": f"2026-11-05T14:{m:02d}:00+00:00", "open": 1, "high": 2, "low": 0.5, "close": 1.5,
                 "volume": 10} for m in range(10)]
        return {"bars": bars, "source": "toss"}


def test_a_frame_carries_the_quote_and_the_newest_bars_and_survives_a_failing_part():
    out = asyncio.run(live.frame(Prices(), "NVDA"))
    assert out["state"] == "open" and out["quote"]["price"] == 200.13
    assert [b["at"][-11:-9] for b in out["bars"]] == ["07", "08", "09"] and out["source"] == "toss"
    broken = asyncio.run(live.frame(Prices(fail_candles=True), "NVDA"))
    assert broken["quote"]["price"] == 200.13 and "bars" not in broken and broken["bars_error"] == "down"


def test_the_stream_needs_a_session_and_checks_the_ticker(db_url):
    from tests.test_prices import _client

    with _client(db_url, Prices(), fake_runs=True) as client:
        assert client.get("/api/live/stream?ticker=NVDA").status_code == 401
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        assert client.get("/api/live/stream?ticker=NV%20DA").status_code == 422


def test_the_stream_sends_a_closed_frame_when_the_market_is_shut(db_url, monkeypatch):
    from tests.test_prices import _client

    monkeypatch.setattr(live, "market_open", lambda ticker, now=None: False)
    with _client(db_url, Prices(), fake_runs=True) as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        with client.stream("GET", "/api/live/stream?ticker=NVDA") as response:
            assert response.headers["content-type"].startswith("text/event-stream")
            lines = []
            for line in response.iter_lines():
                lines.append(line)
                if line.startswith("data:"):
                    break
    assert lines[0].startswith("retry:") and '"state": "closed"' in lines[-1]
