"""Program trading's collector-to-API cache, with a synthetic history and Redis."""

import asyncio
import threading
from datetime import UTC, date, datetime, timedelta

import fakeredis
import pytest

from desk import shared_cache as sc
from desk.futures_flow import KST
from desk.program_latest import ProgramLatest

DAY = date(2026, 7, 2)
NOW = datetime(2026, 7, 2, 1, 0, 10, tzinfo=UTC)


@pytest.fixture
def redis():
    server = fakeredis.FakeServer()
    sync = fakeredis.FakeRedis(server=server, decode_responses=True)
    sc.use(sync, fakeredis.FakeAsyncRedis(server=server, decode_responses=True))
    yield sync
    sc.use(None, None)


def _program(net: float, at: str = "10:00:00") -> dict:
    return {"kospi": {"at": at, "arbitrage": 0, "non_arbitrage": net, "total": net}}


class _History:
    def __init__(self):
        self.rows: dict[date, dict] = {}
        self.calls = 0
        self.accept = True
        self.recorded_at: list[datetime] = []

    def record(self, _view, checked_at, _spot, program):
        self.recorded_at.append(checked_at)
        if not self.accept:
            return False
        rows = self.rows.setdefault(checked_at.astimezone(KST).date(), {})
        for market, row in (program or {}).items():
            if row:
                rows[market] = {**row, "at": checked_at.isoformat(timespec="seconds"),
                                "fetched_at": checked_at.isoformat()}
        return True

    def program(self, day):
        self.calls += 1
        return self.rows.get(day, {}).copy()


def test_collector_and_api_share_committed_market_rows_and_timestamps(redis):
    async def run():
        history = _History()
        writer = ProgramLatest(history, clock=lambda: NOW)
        reader = ProgramLatest(history, clock=lambda: NOW)
        program = {**_program(0), "kosdaq": None}
        assert await writer.record({}, None, program)
        result = await reader.read(DAY, "kospi")
        assert result == {
            "at": NOW.isoformat(),
            "arbitrage": 0,
            "non_arbitrage": 0,
            "total": 0,
            "source": "kis",
            "source_updated_at": "2026-07-02T01:00:00+00:00",
            "fetched_at": NOW.isoformat(),
        }
        assert history.recorded_at == [NOW] and history.calls == 0
        [key] = redis.keys("*")
        assert key.startswith("td:program_trade:") and "kospi" not in key
        assert 0 < redis.pttl(key) <= 90_000
        assert await reader.read(DAY, "kosdaq") is None
        assert await reader.read(DAY - timedelta(days=1), "kospi") is None

    asyncio.run(run())


def test_missing_market_and_rejected_snapshot_do_not_replace_published_values(redis):
    async def run():
        history = _History()
        writer = ProgramLatest(history, clock=lambda: NOW)
        assert await writer.record({}, None, _program(4, at="10:00:11"))
        assert await writer.record({}, None, {"kospi": None, "kosdaq": _program(7)["kospi"]})
        history.accept = False
        assert not await writer.record({}, None, _program(9))
        reader = ProgramLatest(history, clock=lambda: NOW)
        kospi = await reader.read(DAY, "kospi")
        assert kospi["total"] == 4 and kospi["source_updated_at"] is None
        assert (await reader.read(DAY, "kosdaq"))["total"] == 7
        assert sc.stats()["writes"] == 2

    asyncio.run(run())


def test_fallback_reads_history_once_and_keeps_source_time_unknown_when_redis_is_down(redis, monkeypatch):
    async def run():
        history = _History()
        writer = ProgramLatest(history, clock=lambda: NOW)
        await writer.record({}, None, _program(9))
        async_client = sc._async

        async def down(*_args, **_kwargs):
            raise ConnectionError("probe")

        monkeypatch.setattr(type(async_client.pipeline()), "execute", down)
        reader = ProgramLatest(history, clock=lambda: NOW)
        first, second, missing = await asyncio.gather(
            reader.read(DAY, "kospi"), reader.read(DAY, "kospi"), reader.read(DAY, "kosdaq"),
        )
        assert first == second and first["total"] == 9 and missing is None
        assert first["source"] == "kis" and first["fetched_at"] == NOW.isoformat()
        assert first["source_updated_at"] is None and history.calls == 1

    asyncio.run(run())


def test_older_writer_cannot_replace_latest_shared_commit(redis):
    async def run():
        history = _History()
        later = NOW + timedelta(seconds=20)
        writer = ProgramLatest(history, clock=lambda: later)
        old_writer = ProgramLatest(history, clock=lambda: NOW)
        await writer.record({}, None, _program(12))
        await old_writer.record({}, None, _program(1))
        reader = ProgramLatest(history, clock=lambda: later)
        assert (await reader.read(DAY, "kospi"))["total"] == 12
        assert sc.stats()["writes"] == 1

    asyncio.run(run())


def test_malformed_and_future_shared_values_fall_back_to_history(redis):
    async def run():
        history = _History()
        history.record({}, NOW, None, _program(4))
        writer = ProgramLatest(_History(), clock=lambda: NOW + timedelta(seconds=20))
        await writer.record({}, None, _program(8))
        result = await ProgramLatest(history, clock=lambda: NOW).read(DAY, "kospi")
        assert result["total"] == 4 and result["source_updated_at"] is None

        await sc.aput("program_trade", (DAY.isoformat(), "kospi"), {"total": 88}, 90)
        result = await ProgramLatest(history, clock=lambda: NOW).read(DAY, "kospi")
        assert result["total"] == 4 and history.calls == 2

    asyncio.run(run())


def test_watch_conflict_retries_without_overwriting_the_winner(redis, monkeypatch):
    pipeline = type(sc._async.pipeline())
    execute = pipeline.execute
    monkeypatch.setattr(sc, "TIMEOUT_SEC", 5)

    async def run():
        paused = asyncio.Event()
        release = asyncio.Event()
        old = ProgramLatest(_History(), clock=lambda: NOW)
        later = NOW + timedelta(seconds=5)
        new = ProgramLatest(_History(), clock=lambda: later)

        async def pause_old_exec(pipe, *args, **kwargs):
            if not paused.is_set():
                paused.set()
                await release.wait()
            return await execute(pipe, *args, **kwargs)

        monkeypatch.setattr(pipeline, "execute", pause_old_exec)
        pending = asyncio.create_task(old.record({}, None, _program(1)))
        try:
            await asyncio.wait_for(paused.wait(), 5)
            assert await new.record({}, None, _program(12))
        finally:
            release.set()
            await pending
        reader = ProgramLatest(_History(), clock=lambda: later)
        assert (await reader.read(DAY, "kospi"))["total"] == 12
        assert sc.stats()["writes"] == 1 and sc.stats()["errors"] == 0

    asyncio.run(run())


def test_redis_disabled_keeps_local_collectors_and_db_fallback_working():
    async def run():
        sc.use(None, None)
        history = _History()
        writer = ProgramLatest(history, clock=lambda: NOW)
        reader = ProgramLatest(history, clock=lambda: NOW)
        assert await writer.record({}, None, _program(5))
        assert (await writer.read(DAY, "kospi"))["source_updated_at"] is not None
        assert (await reader.read(DAY, "kospi"))["source_updated_at"] is None
        assert history.calls == 1

    asyncio.run(run())


def test_db_reader_finishing_late_cannot_hide_new_collector_value(redis):
    started = threading.Event()
    finish = threading.Event()

    class SlowHistory(_History):
        def program(self, day):
            old = super().program(day)
            started.set()
            if not finish.wait(5):
                raise TimeoutError("test did not release DB")
            return old

    async def run():
        history = SlowHistory()
        clock = [NOW]
        service = ProgramLatest(history, clock=lambda: clock[0])
        pending = asyncio.create_task(service.read(DAY, "kospi"))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            clock[0] += timedelta(seconds=1)
            await service.record({}, None, _program(13))
        finally:
            finish.set()
        assert (await pending)["total"] == 13
        assert (await service.read(DAY, "kospi"))["total"] == 13

    asyncio.run(run())


@pytest.mark.parametrize("redis_older", [True, False])
def test_api_rejects_older_shared_value_after_newer_fallback(redis, redis_older):
    async def run():
        history = _History()
        await ProgramLatest(history, clock=lambda: NOW).record({}, None, _program(1))
        [key] = redis.keys("*")
        sealed = redis.get(key)
        redis.delete(key)
        later = NOW + timedelta(seconds=5)
        history.record({}, later, None, _program(5))
        clock = [0.0]
        reader = ProgramLatest(history, clock=lambda: later, cache_clock=lambda: clock[0])
        assert (await reader.read(DAY, "kospi"))["total"] == 5
        if redis_older:
            redis.set(key, sealed, px=90_000)
        clock[0] += 31
        assert (await reader.read(DAY, "kospi"))["total"] == 5

    asyncio.run(run())


def test_db_confirmation_order_is_preserved_within_one_legacy_at_second(redis):
    async def run():
        history = _History()
        later = NOW.replace(microsecond=900_000)
        history.record({}, later, None, _program(5))
        reader = ProgramLatest(history, clock=lambda: later)
        first = await reader.read(DAY, "kospi")
        assert first["total"] == 5 and first["at"] == NOW.isoformat()
        assert first["fetched_at"] == later.isoformat()

        old = ProgramLatest(_History(), clock=lambda: NOW.replace(microsecond=100_000))
        await old.record({}, None, _program(1))
        assert (await reader.read(DAY, "kospi")) == first

    asyncio.run(run())
