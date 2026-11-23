"""#858 one read per key at a time.

The dashboard's first look asks for the same ticker from several places at once (the quote board, the
estimated bars, the bottom zone, the playbooks): each one found the cache empty and read the source itself.

- ``Flights`` - for coroutines: ``await flights.run(key, factory)`` starts ``factory()`` once per key; whoever
  asks for that key while it runs awaits the same result (or the same exception). A caller that gives up (a
  timeout, a cancelled request) does not cancel the read for the others. A flight belongs to the event loop
  that started it: a caller on another loop (tests run one ``asyncio.run`` after another) starts its own.
- ``KeyLocks`` - for code in worker threads (``asyncio.to_thread``): ``with locks.hold(key):`` lets one thread
  at a time through per key, so a second asker waits for the first one's read and then finds it in the cache.
  Check the cache before taking the lock - a fresh value is never kept waiting behind a refresh.

desk.prices' bars (#536) keep their own copy of the coroutine pattern.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable, Hashable, Iterator
from contextlib import contextmanager
from typing import Any


class Flights:
    def __init__(self) -> None:
        self._pending: dict[Hashable, asyncio.Future] = {}

    def __len__(self) -> int:
        return len(self._pending)

    async def run(self, key: Hashable, factory: Callable[[], Awaitable[Any]]) -> Any:
        loop = asyncio.get_running_loop()
        task = self._pending.get(key)
        if task is None or task.done() or task.get_loop() is not loop:
            task = asyncio.ensure_future(factory())
            self._pending[key] = task

            def done(t: asyncio.Future, key: Hashable = key) -> None:
                if self._pending.get(key) is t:
                    self._pending.pop(key, None)
                if not t.cancelled():
                    t.exception()                  # retrieved: nobody may be left waiting for it

            task.add_done_callback(done)
        return await asyncio.shield(task)


class KeyLocks:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._held: dict[Hashable, list] = {}     # key -> [its lock, how many hold or wait for it]

    def __len__(self) -> int:
        with self._lock:
            return len(self._held)

    @contextmanager
    def hold(self, key: Hashable) -> Iterator[None]:
        with self._lock:
            entry = self._held.setdefault(key, [threading.Lock(), 0])
            entry[1] += 1
        try:
            with entry[0]:
                yield
        finally:
            with self._lock:
                entry[1] -= 1
                if entry[1] == 0 and self._held.get(key) is entry:
                    del self._held[key]


__all__ = ["Flights", "KeyLocks"]
