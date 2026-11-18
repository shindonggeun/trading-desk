"""A bounded cache with a time to live per entry (#628).

Every module kept its own ``dict[key, (stamp, value)]`` and cleared it whole past some size (#391 and
before). ``TTLCache`` keeps at most ``max_items`` entries - past that the expired ones go first, then the
least recently used - and each entry lives ``ttl`` seconds (the cache's, or its own: #391's rule is that
an empty answer or a failure is not kept, or kept briefly, so a caller passes a short ``ttl`` for those,
or ``0`` not to keep them at all).

- ``get(key, ahead=)``: the value while fresh (``ahead``: treat one expiring within that many seconds as
  gone - the prewarm reads it again before it does), else ``default``.
- ``entry(key)``: ``(age, value)`` of an unexpired entry - for a caller that judges freshness itself
  (one long-lived entry read as fresh for a short while and as a fallback after).
- ``put(key, value, ttl=)``, ``pop``, ``clear``, ``len``, ``values()`` (a snapshot of the unexpired values).

The clock is ``time.monotonic`` unless given; any call takes ``now=`` too, for a caller whose clock is
its own (tests). Thread-safe: one lock around each call.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import Any, Generic, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class TTLCache(Generic[K, V]):
    def __init__(self, max_items: int, ttl: float, clock: Callable[[], float] = time.monotonic) -> None:
        if max_items < 1 or ttl <= 0:
            raise ValueError("max_items and ttl must be positive")
        self.max_items, self.ttl, self._clock = max_items, float(ttl), clock
        self._items: OrderedDict[K, tuple[float, float, V]] = OrderedDict()   # key → (stored, expires, value)
        self._lock = threading.Lock()

    def _now(self, now: float | None) -> float:
        return self._clock() if now is None else now

    def get(self, key: K, default: Any = None, *, ahead: float = 0.0, now: float | None = None) -> V | Any:
        t = self._now(now)
        with self._lock:
            hit = self._items.get(key)
            if hit is None:
                return default
            if t >= hit[1]:
                del self._items[key]
                return default
            self._items.move_to_end(key)
            return hit[2] if t < hit[1] - ahead else default

    def entry(self, key: K, *, now: float | None = None) -> tuple[float, V] | None:
        t = self._now(now)
        with self._lock:
            hit = self._items.get(key)
            if hit is None:
                return None
            if t >= hit[1]:
                del self._items[key]
                return None
            self._items.move_to_end(key)
            return t - hit[0], hit[2]

    def put(self, key: K, value: V, ttl: float | None = None, *, now: float | None = None) -> None:
        """Keep ``value`` for ``ttl`` seconds (the cache's when None); ``ttl <= 0`` keeps nothing and drops
        what was there."""
        t = self._now(now)
        life = self.ttl if ttl is None else float(ttl)
        with self._lock:
            self._items.pop(key, None)
            if life <= 0:
                return
            self._items[key] = (t, t + life, value)
            if len(self._items) > self.max_items:
                for k in [k for k, v in self._items.items() if t >= v[1]]:
                    del self._items[k]
                while len(self._items) > self.max_items:
                    self._items.popitem(last=False)

    def pop(self, key: K, default: Any = None) -> V | Any:
        with self._lock:
            hit = self._items.pop(key, None)
        return default if hit is None else hit[2]

    def values(self, *, now: float | None = None) -> list[V]:
        """The unexpired values, oldest first - a copy, for a look (tests, status), never to change."""
        t = self._now(now)
        with self._lock:
            return [v[2] for v in self._items.values() if t < v[1]]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        return len(self._items)
