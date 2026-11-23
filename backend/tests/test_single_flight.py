"""#858 desk.single_flight: one read per key at a time."""

import asyncio
import threading
import time

import pytest

from desk.single_flight import Flights, KeyLocks


def test_askers_of_one_key_at_once_share_one_read_and_other_keys_get_their_own():
    flights, calls = Flights(), []

    async def read(key):
        calls.append(key)
        await asyncio.sleep(0.05)
        return f"value of {key}"

    async def main():
        return await asyncio.gather(*(flights.run(k, lambda k=k: read(k)) for k in ["a"] * 5 + ["b"] * 3))

    got = asyncio.run(main())
    assert got == ["value of a"] * 5 + ["value of b"] * 3
    assert sorted(calls) == ["a", "b"] and len(flights) == 0


def test_a_failure_reaches_every_asker_and_the_next_ask_reads_again():
    flights, calls = Flights(), []

    async def read():
        calls.append(1)
        await asyncio.sleep(0.01)
        raise RuntimeError("source down")

    async def main():
        got = await asyncio.gather(*(flights.run("k", read) for _ in range(4)), return_exceptions=True)
        assert all(isinstance(g, RuntimeError) for g in got) and len(calls) == 1
        with pytest.raises(RuntimeError):
            await flights.run("k", read)
        assert len(calls) == 2

    asyncio.run(main())


def test_an_asker_that_gives_up_does_not_cancel_the_read_for_the_others():
    flights = Flights()

    async def read():
        await asyncio.sleep(0.1)
        return 42

    async def main():
        waiting = asyncio.ensure_future(flights.run("k", read))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(flights.run("k", read), 0.01)
        assert await waiting == 42

    asyncio.run(main())


def test_a_flight_of_another_loop_is_not_joined():
    """A flight still pending on another loop (a test's closed asyncio.run) is never awaited from this one."""
    flights, calls = Flights(), []

    async def read():
        calls.append(1)
        return len(calls)

    other = asyncio.new_event_loop()
    try:
        stranded = other.create_future()              # never done: its loop does not run
        flights._pending["k"] = stranded
        assert asyncio.run(asyncio.wait_for(flights.run("k", read), 2)) == 1
        assert flights._pending.get("k") is not stranded
    finally:
        other.close()
    assert asyncio.run(flights.run("k", read)) == 2   # and a finished one is not reused either


def test_key_locks_let_one_thread_per_key_read_and_the_others_find_it_cached():
    locks, cache, reads = KeyLocks(), {}, []

    def get(key):
        if key in cache:                    # the fast path never takes the lock
            return cache[key]
        with locks.hold(key):
            if key in cache:
                return cache[key]
            reads.append(key)
            time.sleep(0.05)
            cache[key] = key.upper()
            return cache[key]

    threads = [threading.Thread(target=get, args=(k,)) for k in ["a"] * 6 + ["b"] * 2]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(reads) == ["a", "b"] and cache == {"a": "A", "b": "B"} and len(locks) == 0
