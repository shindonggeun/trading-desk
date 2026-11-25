"""#874 desk.open_fill: the favorites' spark bars just before their market opens, beside the prewarm passes."""

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta, timezone

from desk import open_fill, prewarm
from desk.open_fill import LEAD_SEC, OpenFill, next_open

KST = timezone(timedelta(hours=9))


def _kst(hhmm: str, day: int = 24, month: int = 11) -> datetime:          # 2026-11-24 is a Tuesday
    h, m = map(int, hhmm.split(":"))
    return datetime(2026, month, day, h, m, tzinfo=KST)


def test_the_next_open_is_todays_while_ahead_and_none_where_no_open_bursts():
    assert next_open("005930.KS", _kst("08:56")) == _kst("09:00")
    assert next_open("^KQ11", _kst("08:56")) == _kst("09:00")
    assert next_open("005930.KS", _kst("09:01")) is None                     # open already
    assert next_open("005930.KS", _kst("08:56", day=28)) is None             # a Saturday
    us = next_open("NVDA", datetime(2026, 11, 24, 14, 20, tzinfo=UTC))       # 09:20 New York
    assert us == datetime(2026, 11, 24, 14, 30, tzinfo=UTC)
    for ticker in ("NQ=F", "BTC-USD", "KRW=X", "DX-Y.NYB"):                   # ~23 h, 24/7, FX
        assert next_open(ticker, _kst("08:56")) is None


def test_no_open_on_a_holiday():
    """#874 review: 휴장일(평일)에는 개장이 없다 — 시장 달력(desk.market_calendar)대로."""
    assert next_open("005930.KS", _kst("08:00", day=1, month=1)) is None    # 2026-01-01 (Thu): KRX closed
    thanksgiving = datetime(2026, 11, 26, 13, 0, tzinfo=UTC)                  # Thu 08:00 New York: NYSE closed
    assert next_open("NVDA", thanksgiving) is None
    assert next_open("NVDA", thanksgiving - timedelta(days=1)) is not None   # the day before trades


def test_a_plan_reads_the_spark_bars_once_just_before_the_open():
    """08:56 의 패스: 09:00 에 여는 즐겨찾기만, LEAD_SEC 전에 5m 78 로 한 번 — 즐겨찾기는 그때 다시 읽는다.
    같은 개장은 다음 패스가 또 채우지 않는다."""
    clock = {"now": _kst("08:56")}
    favorites = ["005930.KS", "NVDA", "BTC-USD", "NQ=F"]
    reads, waits = [], []

    async def candles(ticker, interval, count):
        reads.append((ticker, interval, count, clock["now"]))
        return {"bars": []}

    async def sleep(seconds):
        waits.append(seconds)
        clock["now"] += timedelta(seconds=seconds)
        favorites.append("000660.KS")                  # starred while the plan waited

    fill = OpenFill(candles, lambda n: favorites[:n], now=lambda: clock["now"], sleep=sleep)
    assert asyncio.run(fill.plan()) == 2
    assert waits == [240 - LEAD_SEC]
    assert sorted(r[:3] for r in reads) == [("000660.KS", "5m", 78), ("005930.KS", "5m", 78)]
    assert all(r[3] == _kst("09:00") - timedelta(seconds=LEAD_SEC) for r in reads)
    clock["now"] = _kst("08:59") + timedelta(seconds=40)                     # the next pass, before the open
    assert asyncio.run(fill.plan()) == 0 and len(reads) == 2


def test_the_days_first_pass_plans_an_open_hours_ahead():
    """#874 review: 지평이 없다 — 06:00 의 패스가 09:00 을 맡는다(패스가 길어져 다음 패스가 늦어도 놓치지 않는다)."""
    clock = {"now": _kst("06:00")}
    waits = []

    async def candles(*args):
        return {"bars": []}

    async def sleep(seconds):
        waits.append(seconds)
        clock["now"] += timedelta(seconds=seconds)

    fill = OpenFill(candles, lambda n: ["005930.KS"], now=lambda: clock["now"], sleep=sleep)
    assert asyncio.run(fill.plan()) == 1 and waits == [3 * 3600 - LEAD_SEC]


def _loop_once(aside, ticker_job_check, after_end=None):
    """Run prewarm.run until its first pass reaches the tickers, then stop the loop as the app's end does
    (``after_end`` then, while the event loop still runs - asyncio.run's own end cancels what is left)."""
    async def go():
        prewarm.LAST.clear()
        reached = asyncio.Event()

        async def job():
            await prewarm.off(lambda: None)

        async def ticker_job(ticker):
            await ticker_job_check()
            reached.set()

        task = asyncio.ensure_future(prewarm.run(lambda: {"a": job, "b": job}, lambda: ["T"], ticker_job,
                                                 interval=60, first_delay=0, aside=aside))
        await asyncio.wait_for(reached.wait(), 5)    # a pass waiting for its plan fails here, never hangs
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)                       # the cancelled tasks' callbacks
        if after_end is not None:
            after_end()
        return dict(prewarm.LAST)

    return asyncio.run(go())


def test_the_pass_does_not_wait_for_its_plan_and_the_loops_end_cancels_it():
    """#874 개장까지 기다리는 계획은 패스 밖의 태스크 — 패스는 그 계획이 끝나기 전에 시간을 기록하고 종목 job 까지
    간다. 루프가 끝나면(앱 종료) 계획도 취소된다."""
    plan: dict = {}

    async def aside():
        plan["task"] = asyncio.current_task()
        await asyncio.Event().wait()                 # waiting for an open that never comes

    async def check():
        await asyncio.sleep(0)                       # the plan has started
        plan["done_at_tickers"] = plan["task"].done()
        plan["last_at_tickers"] = dict(prewarm.LAST)

    def ended():
        plan["cancelled_by_the_loop"] = plan["task"].cancelled()

    last = _loop_once(aside, check, ended)
    assert plan["cancelled_by_the_loop"] is True    # prewarm.run's own end, not asyncio.run's
    assert plan["done_at_tickers"] is False          # the pass went on while its plan still waited
    assert set(plan["last_at_tickers"]["jobs"]) == {"a", "b"}   # the pass's time was kept before the plan ended
    assert last["ms"] == plan["last_at_tickers"]["ms"]


def test_a_plan_that_fails_before_its_read_is_planned_again_by_the_next_pass():
    """#874 review: the favorites' read at T-12 s failing leaves that open to the next pass, not lost for the day."""
    clock = {"now": _kst("08:00")}
    asks = {"n": 0}
    reads = []

    def tickers(n):
        asks["n"] += 1
        if asks["n"] == 2:                           # the plan's first read; the second at its open
            raise RuntimeError("db down")
        return ["005930.KS"]

    async def candles(*args):
        reads.append(args)
        return {"bars": []}

    async def sleep(seconds):
        clock["now"] += timedelta(seconds=seconds)

    fill = OpenFill(candles, tickers, now=lambda: clock["now"], sleep=sleep)
    try:
        asyncio.run(fill.plan())
        raise AssertionError("the plan should have failed")
    except RuntimeError:
        pass
    # the next pass, at T-12 s still before the open: it plans that open again and reads it
    assert asyncio.run(fill.plan()) == 1 and reads == [("005930.KS", "5m", 78)]


def test_a_failed_plan_is_logged_and_the_loop_goes_on(caplog):
    caplog.set_level(logging.WARNING, logger="desk.prewarm")
    plan: dict = {}

    async def aside():
        plan["task"] = asyncio.current_task()
        raise ValueError("no favorites table")

    async def check():
        while "task" not in plan or not plan["task"].done():
            await asyncio.sleep(0)
        await asyncio.sleep(0)                       # its done callback

    _loop_once(aside, check)
    assert "prewarm aside failed: ValueError" in caplog.text


def test_the_app_plans_the_favorites_beside_the_prewarm(db_url, monkeypatch):
    """The lifespan's wiring: the app's plan reads the favorites' spark bars through prices.candles."""
    from desk.broker import MockBroker
    from desk.prices import PriceSource
    from tests.test_api import OWNER, PASSWORD
    from tests.test_prices import _client

    asked = []
    source = PriceSource(MockBroker(), yahoo_candles=lambda *a: asked.append(a) or [])
    at = datetime.now(UTC) + timedelta(hours=1)
    monkeypatch.setattr(open_fill, "next_open", lambda t, now: at if now < at else None)

    async def no_wait(seconds):                                             # the hour until that open
        assert seconds > 0

    with _client(db_url, source, fake_runs=True) as client:      # no prewarm loop of its own racing this plan
        plan = client.app.state.open_fill
        plan._sleep = no_wait
        assert asyncio.run(plan.plan()) == 0 and asked == []                 # no favorites yet
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        assert client.post("/api/favorites", json={"ticker": "NVDA"}).status_code < 300
        assert asyncio.run(plan.plan()) == 1
        assert asked == [("NVDA", "5m", 78)]
