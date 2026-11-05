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
    # Korean indices and KOSDAQ follow KRX; FX trades through the week
    assert live.market_open("^KS11", tue_kst_10) and live.market_open("035720.KQ", tue_kst_10)
    assert not live.market_open("^KS11", tue_et_10) and live.market_open("^GSPC", tue_et_10)
    assert live.market_open("KRW=X", tue_kst_10) and not live.market_open("KRW=X", saturday)
    assert live.market_open("KRW=X", datetime(2026, 11, 8, 23, 0, tzinfo=UTC))      # Sunday 18:00 New York


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
    assert broken["quote"]["price"] == 200.13 and "bars" not in broken and broken["bars_error"] == "unavailable"   # no upstream detail to the page


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


def test_an_open_stream_sends_quote_and_bars_and_caps_streams_per_session(db_url, monkeypatch):
    from tests.test_prices import _client

    monkeypatch.setattr(live, "market_open", lambda ticker, now=None: True)
    with _client(db_url, Prices(), fake_runs=True) as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        with client.stream("GET", "/api/live/stream?ticker=NVDA") as response:
            for line in response.iter_lines():
                if line.startswith("data:"):
                    break
            assert '"price": 200.13' in line and '"bars"' in line
        monkeypatch.setattr(live, "MAX_STREAMS", 0)          # as if the session had its streams open
        assert client.get("/api/live/stream?ticker=AAPL").status_code == 429


def test_a_background_recheck_does_not_keep_the_session_alive(db_url):
    from datetime import timedelta

    from sqlalchemy import select, update

    from desk.auth import Auth, hash_token
    from desk.db import make_engine, metadata, sessions

    engine = make_engine(db_url)
    metadata.create_all(engine)
    auth = Auth(engine, timedelta(hours=1), timedelta(hours=12))
    auth.create_user(OWNER, PASSWORD)
    token = auth.open_session(auth.authenticate(OWNER, PASSWORD), None, None)
    with engine.begin() as conn:
        conn.execute(update(sessions).values(last_seen_at=datetime.now(UTC) - timedelta(minutes=30)))
    assert auth.resolve(token, touch=False) is not None
    with engine.connect() as conn:
        seen = conn.execute(select(sessions.c.last_seen_at).where(sessions.c.token_hash == hash_token(token))).scalar()
    engine.dispose()
    assert seen.replace(tzinfo=UTC) < datetime.now(UTC) - timedelta(minutes=29)
