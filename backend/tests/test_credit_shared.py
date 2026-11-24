"""The collector's existing KOFIA refresh and the API's optional Redis read."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import fakeredis
import httpx
import pytest

from desk import credit
from desk import shared_cache as sc

THURSDAY = "20261008"
FRIDAY = "20261002"
SUNDAY = datetime(2026, 10, 11, 3, tzinfo=UTC)


@pytest.fixture
def clock(monkeypatch):
    clock = SimpleNamespace(now=SUNDAY, monotonic=10.0)
    monkeypatch.setattr(credit, "_now", lambda: clock.now)
    monkeypatch.setattr(credit, "monotonic", lambda: clock.monotonic)
    return clock


@pytest.fixture
def redis():
    server = fakeredis.FakeServer()
    sync = fakeredis.FakeRedis(server=server, decode_responses=True)
    sc.use(sync, fakeredis.FakeAsyncRedis(server=server, decode_responses=True))
    yield server, sync
    sc.use(None, None)


def _tables(day, *, loans=None, credit_total=28_000_000):
    return {
        credit.FUNDS: [{"TMPV1": day, "TMPV2": "88000000", "TMPV5": "910000",
                        "TMPV6": "8500", "TMPV7": "0.9", "raw": "source-body-only"}],
        credit.CREDIT: [{"TMPV1": loans or day, "TMPV2": credit_total, "TMPV3": "21000000", "TMPV4": "7000000"}],
    }


async def _refresh(cache, data, *, fail=None, calls=None):
    def handler(request):
        table = json.loads(request.content)["dmSearch"]["OBJ_NM"]
        if calls is not None:
            calls.append(table)
        return httpx.Response(503) if table == fail else httpx.Response(200, json={"ds1": data[table]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await cache.refresh_and_publish(client)


async def _snapshot():
    hit = await sc.aget(credit.SHARED_NS, credit.SHARED_KEY)
    assert hit is not None
    return hit[0]


def test_sealed_snapshot_uses_common_statistical_date_and_existing_two_calls(redis, clock):
    async def go():
        writer, reader = credit.CreditCache(), credit.CreditCache()
        data = _tables(THURSDAY)
        data[credit.CREDIT] += _tables("20261009")[credit.CREDIT]   # only one table has a later day
        calls = []
        assert await _refresh(writer, data, calls=calls)
        assert calls == [credit.FUNDS, credit.CREDIT]

        _server, redis_sync = redis
        key, = redis_sync.keys("td:*")
        encoded = redis_sync.get(key)
        assert key.startswith("td:kofia_credit:") and "2026-10-08" not in key
        assert all(fragment not in encoded for fragment in ("2026-10-08", "88000000", "source-body-only", "refreshed_at"))
        sealed = await _snapshot()
        assert set(sealed) == {"series", "source_date", "refreshed_at"}
        assert set(sealed["series"][0]) == {
            "date", "credit", "deposits", "ratio", "credit_kospi", "credit_kosdaq", "forced_sale", "forced_sale_pct",
        }
        view = await reader.shared_view(7)
        assert view == writer.view(7)
        assert view["source_date"] == view["summary"]["date"] == "2026-10-08"
        assert view["refreshed_at"] == clock.now.isoformat()

    asyncio.run(go())


def test_partial_error_keeps_both_local_and_redis_last_good_view(redis, clock):
    async def go():
        writer = credit.CreditCache()
        assert await _refresh(writer, _tables(THURSDAY))
        prior = await _snapshot()
        clock.now += timedelta(hours=6)
        assert not await _refresh(writer, _tables(THURSDAY, credit_total=29_000_000), fail=credit.CREDIT)
        assert not await _refresh(writer, _tables(THURSDAY, loans="20261009"))
        assert (await writer.shared_view())["error"] == "ValueError"
        assert writer.view()["refreshed_at"] == prior["refreshed_at"]
        assert await _snapshot() == prior
        remote = await credit.CreditCache().shared_view()
        assert remote["error"] is None and remote["summary"]["credit_trillion"] == 28.0
        assert sc.stats()["writes"] == 1

    asyncio.run(go())


def test_older_source_cannot_overwrite_redis_or_the_collector_local_view(redis, clock):
    async def go():
        writer = credit.CreditCache()
        assert await _refresh(writer, _tables(THURSDAY))
        original = await _snapshot()
        clock.now += timedelta(hours=6)
        assert await _refresh(credit.CreditCache(), _tables(FRIDAY, credit_total=24_000_000))
        assert not await _refresh(writer, _tables(FRIDAY, credit_total=23_000_000))
        assert writer.view()["source_date"] == "2026-10-08"
        assert await _snapshot() == original

    asyncio.run(go())


def test_truncated_tables_keep_history_but_normal_window_rollover_can_refresh(redis, clock):
    async def go():
        writer = credit.CreditCache()
        history = _tables(THURSDAY)
        for day in ("20250907", "20250910", "20260905", FRIDAY):
            for table, rows in _tables(day, credit_total=30_000_000).items():
                history[table] += rows
        assert await _refresh(writer, history)
        baseline = await _snapshot()
        assert writer.view()["summary"]["ratio_percentile_1y"] < 100

        clock.now += timedelta(hours=6)
        one_table_missing_history = {**history, credit.CREDIT: _tables(THURSDAY)[credit.CREDIT]}
        assert not await _refresh(writer, one_table_missing_history)
        assert writer.view()["refreshed_at"] == baseline["refreshed_at"]
        assert writer.view()["error"] == "IncompleteHistory"
        assert await _refresh(credit.CreditCache(), one_table_missing_history)
        assert await _snapshot() == baseline  # even a collector without its previous local state

        clock.now += timedelta(hours=6)
        rolled = {table: [row for row in rows if row["TMPV1"] != "20250907"] for table, rows in history.items()}
        assert await _refresh(writer, rolled)
        assert writer.view()["refreshed_at"] == clock.now.isoformat()
        assert (await _snapshot())["refreshed_at"] == clock.now.isoformat()

    asyncio.run(go())


def test_two_same_date_publishers_keep_newer_confirmation_even_when_it_writes_first(redis, clock, monkeypatch):
    old_ready, new_done = asyncio.Event(), asyncio.Event()
    original_put = sc.aput_newer

    async def flip_writes(namespace, key, value, ttl, *, rank):
        newest = value["series"][-1]["credit"] == 29_000_000
        if not newest:
            old_ready.set()
            await new_done.wait()
        await original_put(namespace, key, value, ttl, rank=rank)
        if newest:
            new_done.set()

    async def go():
        monkeypatch.setattr(sc, "aput_newer", flip_writes)
        older = asyncio.create_task(_refresh(credit.CreditCache(), _tables(THURSDAY)))
        await old_ready.wait()
        clock.now += timedelta(microseconds=1)
        assert await _refresh(credit.CreditCache(), _tables(THURSDAY, credit_total=29_000_000))
        assert await older
        value = await credit.CreditCache().shared_view()
        assert value["summary"]["credit_trillion"] == 29.0
        assert value["refreshed_at"] == clock.now.isoformat()

    asyncio.run(go())


def test_read_miss_is_single_flight_and_retries_after_local_window(redis, clock, monkeypatch):
    read_count = 0
    read_started, release = asyncio.Event(), asyncio.Event()
    original_read = sc.aget

    async def hold_first(namespace, key):
        nonlocal read_count
        read_count += 1
        if read_count == 1:
            read_started.set()
            await release.wait()
        return await original_read(namespace, key)

    async def go():
        reader = credit.CreditCache()
        monkeypatch.setattr(sc, "aget", hold_first)
        pending = [asyncio.create_task(reader.shared_view()) for _ in range(12)]
        await read_started.wait()
        release.set()
        assert all(view["summary"] is None for view in await asyncio.gather(*pending))
        assert read_count == 1
        assert await _refresh(credit.CreditCache(), _tables(THURSDAY))
        clock.monotonic += credit.LOCAL_TTL - 1
        assert (await reader.shared_view())["summary"] is None
        clock.monotonic += 1
        assert (await reader.shared_view())["source_date"] == "2026-10-08"
        assert read_count == 2

    asyncio.run(go())


@pytest.mark.parametrize("malformed", [
    {"source_date": "2026-10-09"},
    {"refreshed_at": (SUNDAY + timedelta(days=1)).isoformat()},
    {"refreshed_at": (SUNDAY - timedelta(days=7)).isoformat()},
    {"source_date": "2026-10-12", "series": [{"date": "2026-10-12", "credit": 1, "deposits": 2, "ratio": 50}]},
    {"series": [{"date": "2026-10-08", "credit": True, "deposits": 2, "ratio": 50}]},
])
def test_bad_sealed_observation_falls_back_and_cannot_block_valid_write(redis, clock, malformed):
    async def go():
        writer = credit.CreditCache()
        assert await _refresh(writer, _tables(THURSDAY))
        normal = await _snapshot()
        await sc.aput(credit.SHARED_NS, credit.SHARED_KEY, {**normal, **malformed}, credit.SHARED_TTL)
        assert (await credit.CreditCache().shared_view())["source_date"] is None
        assert await writer.shared_view() == writer.view()

        clock.now += timedelta(seconds=1)
        assert await _refresh(writer, _tables(THURSDAY, credit_total=29_000_000))
        assert (await credit.CreditCache().shared_view())["summary"]["credit_trillion"] == 29.0

    asyncio.run(go())


def test_cache_outage_uses_local_or_last_seen_until_retention_expires(redis, clock):
    async def go():
        writer, reader = credit.CreditCache(), credit.CreditCache()
        assert await _refresh(writer, _tables(THURSDAY))
        assert await reader.shared_view() == writer.view()
        server, _sync = redis
        server.connected = False
        clock.monotonic += credit.LOCAL_TTL
        assert await reader.shared_view() == writer.view()
        assert sc.stats()["errors"] == 1
        assert await writer.shared_view() == writer.view()
        sc.use(None, None)
        assert await reader.shared_view() == writer.view()
        clock.now += timedelta(seconds=credit.SHARED_TTL)
        assert (await reader.shared_view())["source_date"] is None
        assert await writer.shared_view() == writer.view()

    asyncio.run(go())
