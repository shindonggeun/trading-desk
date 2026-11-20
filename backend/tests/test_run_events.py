"""#688 a run's events wake SSE readers in another process: PostgreSQL LISTEN/NOTIFY (desk.run_events).
The PostgreSQL tests run in CI (DESK_TEST_DATABASE_URL); SQLite keeps the in-process Hub only."""

import asyncio
import contextlib
import time

import pytest
from sqlalchemy import text

from desk import run_events
from desk.db import make_engine, migrate
from desk.runner import Hub
from desk.store import Store


@pytest.fixture
def engine(db_url):
    migrate(db_url)
    eng = make_engine(db_url)
    yield eng
    eng.dispose()


def _pg(engine):
    if not run_events.shared(engine):
        pytest.skip("PostgreSQL only (CI sets DESK_TEST_DATABASE_URL)")


def test_wake_all_wakes_every_open_stream_once():
    async def scenario():
        hub = Hub()
        a, b = hub.listen("r1"), hub.listen("r2")
        hub.wake_all()
        assert a.is_set() and b.is_set()
        assert not hub.listen("r1").is_set()       # the next round waits again
    asyncio.run(scenario())


def test_sqlite_writes_events_without_a_channel(engine):
    if run_events.shared(engine):
        pytest.skip("SQLite only")
    store = Store(engine)
    run_id = store.create_run("NVDA", "2026-11-20", {})
    assert store.append_event(run_id, {"type": "queued"})["seq"] == 1


async def _following(engine, hub, states):
    task = asyncio.create_task(run_events.follow(hub, engine, lambda up, exc: states.append(up)))
    for _ in range(200):                           # connected and listening
        if states and states[-1]:
            return task
        await asyncio.sleep(0.05)
    task.cancel()
    raise AssertionError("run_events never listened")


async def _stop(task):
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def test_an_event_written_elsewhere_wakes_the_reader_here(engine, db_url):
    _pg(engine)
    other = make_engine(db_url)                    # the analyst: its own engine, its own Hub nowhere near this one

    async def scenario():
        hub, states = Hub(), []
        task = await _following(engine, hub, states)
        try:
            run_id = await asyncio.to_thread(Store(other).create_run, "NVDA", "2026-11-20", {})
            waiter = hub.listen(run_id)
            await asyncio.to_thread(Store(other).append_event, run_id, {"type": "queued"})
            await asyncio.wait_for(waiter.wait(), 5)
            # the woken reader already sees the row: the notification is delivered at commit
            assert [e["seq"] for e in await asyncio.to_thread(Store(engine).events_after, run_id, 0)] == [1]

            waiter = hub.listen(run_id)
            await asyncio.to_thread(Store(other).mark_failed, run_id, "x")
            assert await asyncio.to_thread(Store(other).delete_run, run_id) == "deleted"
            await asyncio.wait_for(waiter.wait(), 5)   # an open stream sees the run is gone
        finally:
            await _stop(task)

    try:
        asyncio.run(scenario())
    finally:
        other.dispose()


def test_a_rolled_back_event_wakes_no_one(engine):
    _pg(engine)

    async def scenario():
        hub, states = Hub(), []
        task = await _following(engine, hub, states)
        try:
            run_id = await asyncio.to_thread(Store(engine).create_run, "NVDA", "2026-11-20", {})
            waiter = hub.listen(run_id)

            def rolled_back():
                with engine.connect() as conn:
                    conn.execute(text("SELECT pg_notify(:c, :r)"), {"c": run_events.CHANNEL, "r": run_id})
                    conn.rollback()

            await asyncio.to_thread(rolled_back)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(waiter.wait(), 1.5)
        finally:
            await _stop(task)

    asyncio.run(scenario())


def test_a_dropped_listener_reconnects_and_wakes_every_stream(engine, monkeypatch):
    _pg(engine)
    monkeypatch.setattr(run_events, "RETRY_SEC", (0.2,))

    async def scenario():
        hub, states = Hub(), []
        task = await _following(engine, hub, states)
        try:
            waiter = hub.listen("some-run")
            await asyncio.to_thread(_terminate_listener, engine)
            await asyncio.wait_for(waiter.wait(), 10)   # wake_all after the reconnect
            for _ in range(200):
                if states[-1] and False in states:
                    break
                await asyncio.sleep(0.05)
            assert states[0] is True and False in states and states[-1] is True
        finally:
            await _stop(task)

    asyncio.run(scenario())


# Only this database's listeners: under xdist every worker has its own test database on the same server, and
# a listener of another worker (or one an earlier test here has not closed yet) is none of this test's business.
_LISTENERS = ("SELECT pid FROM pg_stat_activity WHERE application_name = :n AND datname = current_database() "
              "AND pid <> pg_backend_pid()")


def _listeners(engine) -> set[int]:
    with engine.connect() as conn:
        return set(conn.execute(text(_LISTENERS), {"n": run_events.APP_NAME}).scalars())


def _terminate_listener(engine, pids: set[int] | None = None):
    """Drop the listening connections of this database (or just ``pids``)."""
    targets = _listeners(engine) if pids is None else pids
    with engine.begin() as conn:
        killed = [conn.execute(text("SELECT pg_terminate_backend(:p)"), {"p": pid}).scalar_one() for pid in targets]
    assert any(killed)


def test_the_listener_stops_with_its_task(engine):
    _pg(engine)
    before = _listeners(engine)
    mine: set[int] = set()

    async def scenario():
        hub, states = Hub(), []
        task = await _following(engine, hub, states)
        mine.update(await asyncio.to_thread(_listeners, engine) - before)
        await _stop(task)

    asyncio.run(scenario())
    assert mine                                      # the one this test started
    for _ in range(50):                             # the thread looks up within POLL_SEC and closes
        if not mine & _listeners(engine):
            return
        time.sleep(0.1)
    raise AssertionError("the listening connection outlived its task")


@pytest.mark.parametrize(("role", "listens"), [("api", True), ("analyst", False)])
def test_status_shows_the_listener_off_on_sqlite_and_on_for_the_api_role_on_postgresql(db_url, role, listens):
    from tests.test_api import _client

    def state(client):
        return {j["name"]: j for j in client.get("/api/status").json()["jobs"]}["run_events"]["state"]

    with _client(db_url, role=role) as client:
        if not (listens and db_url.startswith("postgresql")):
            assert state(client) == "off"
            return
        for _ in range(100):                         # listening: fresh, not a "running" that never says more
            if state(client) == "fresh":
                break
            time.sleep(0.05)
        assert state(client) == "fresh"
        engine = make_engine(db_url)
        try:
            _terminate_listener(engine)
            for _ in range(100):                     # a dropped connection shows until it is back
                if state(client) == "error":
                    break
                time.sleep(0.02)
        finally:
            engine.dispose()
        assert state(client) in ("error", "fresh")   # the 1 s retry may already have it back
