"""#689 demand-driven collection: which shared values the api's screens read, for the collector to keep fresh.

The api answers screens from Redis when the collector has the value; the collector fetches only what a screen
read lately, so with no screen open neither of them calls Toss or KIS.

- **api** (``want``): every read of a demand-collected value (a quote past the KIS tick and the memory cache,
  the futures quote card) made for a screen marks its (namespace, key) - on every read, not only on a miss:
  while the collector keeps a value fresh the api never misses, and a "want on miss" would stop the collection
  it relies on. Only reads inside an HTTP request mark (``ScreenReads`` sets ``SCREEN`` per request): the api's
  own background reads (prewarm's playbooks every few minutes) must not keep the collector calling with no
  screen open. ``flush`` (the ``demand_wants`` job, every few seconds) writes the keys read in the last
  ``KEEP_SEC`` as one sealed value under one name (``td:want:<HMAC>``, desk.shared_cache): the name carries no
  ticker (its length, and the ``quote`` keys' count, still tell roughly how many - docs/security.md §5.2).
  Nothing read - nothing written, and the value runs out in ``WANT_TTL``.
- **collector** (``serve``, the ``demand`` job): reads that value and calls a handler per namespace for each key
  due (``EVERY``), two at a time, each within ``HANDLER_SEC``, a pass starting none after ``PASS_SEC``. A handler fetches from the source without
  reading the shared tier (it would read its own value back) and writes the value for the api. The collector
  keeps no wanted set of its own - a restart picks up where Redis is.
- Off unless the process is the api with Redis on (or the collector, for ``serve``): one process (``all``) or no
  Redis answers as before, from its own fetches.

Only market data goes through here: tickers (quotes, #999 off-hours estimates) and futures keys, no user,
session or holdings figures
(docs/security.md §5.2).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from . import shared_cache

log = logging.getLogger(__name__)

WANT_NS = "want"
WANT_KEY = "v1"
WANT_TTL = 20.0            # the wanted set runs out this soon after the api stops writing it
KEEP_SEC = 30.0            # a key read within this long is still wanted (a 15 s screen poll, twice)
FLUSH_EVERY = 10.0         # the api rewrites an unchanged wanted set this often (inside WANT_TTL)
MAX_KEYS = 80              # as desk.broker.kis_stream's subscription cap - past it, a key is not collected
MAX_KEY_CHARS = 40
# a key's collection period per namespace. #999 "estimate" (desk.estimate.collect): 9 s on the 5 s serve
# loop lands every ~10 s (10 would wait for the third pass, ~15 s)
EVERY = {"quote": 10.0, "futures_quote": 15.0, "estimate": 9.0}
NS_MAX = {"estimate": MAX_KEYS // 2}   # #999 a stream's 50 off-hours tickers leave room for the quotes
PARALLEL = 2               # handlers running at once in a pass
PASS_SEC = 10.0            # a pass stops starting handlers after this long
HANDLER_SEC = 20.0         # one fetch past this is a failure (a slow KIS page run must not hold the next pass)
BACKOFF_MAX_SEC = 60.0     # a key that failed waits twice as long each time, up to this

Handler = Callable[[str], Awaitable[None]]

# True inside an HTTP request (ScreenReads): only a screen's reads are wanted
SCREEN: ContextVar[bool] = ContextVar("desk_demand_screen", default=False)

_lock = threading.Lock()
_role = ""
_seen: dict[tuple[str, str], float] = {}           # api: (namespace, key) -> monotonic time it was last read
_sent: tuple[frozenset, float] = (frozenset(), 0.0)  # api: what the last flush wrote, and when
_due: dict[tuple[str, str], float] = {}            # collector: key -> monotonic time it is next due
_fails: dict[tuple[str, str], int] = {}
_stats = {"flushes": 0, "dropped": 0, "served": 0, "failed": 0}


def configure(role: str) -> None:
    """``role``: desk.roles' role of this process (api / collector / analyst / all)."""
    global _role, _sent
    with _lock:
        _role = role
        _sent = (frozenset(), 0.0)
        _seen.clear()
        _due.clear()
        _fails.clear()
        for k in _stats:
            _stats[k] = 0


def enabled() -> bool:
    """The api marks and accepts demand-collected values (older than its own fetches would be)."""
    return _role == "api" and shared_cache.enabled()


def serving() -> bool:
    return _role == "collector" and shared_cache.enabled()


def want(namespace: str, key: str) -> None:
    """Mark ``key`` of ``namespace`` as read now (no I/O - the ``demand_wants`` job writes the set)."""
    if not enabled() or not SCREEN.get() or namespace not in EVERY or not isinstance(key, str)             or not 0 < len(key) <= MAX_KEY_CHARS:
        return
    with _lock:
        if (namespace, key) not in _seen and (len(_seen) >= MAX_KEYS or (
                namespace in NS_MAX and sum(1 for ns, _ in _seen if ns == namespace) >= NS_MAX[namespace])):
            _stats["dropped"] += 1
            return
        _seen[(namespace, key)] = time.monotonic()


def _current(now: float) -> dict[str, list[str]]:
    with _lock:
        for k in [k for k, at in _seen.items() if now - at > KEEP_SEC]:
            del _seen[k]
        out: dict[str, list[str]] = {}
        for ns, key in sorted(_seen):
            out.setdefault(ns, []).append(key)
        return out


async def flush() -> None:
    """api: write the keys read lately - when the set changed, or FLUSH_EVERY after the last write."""
    global _sent
    if not enabled():
        return
    now = time.monotonic()
    wanted = _current(now)
    if not wanted:
        return                                     # nothing read: let the last set run out
    keys = frozenset((ns, k) for ns, ks in wanted.items() for k in ks)
    if keys == _sent[0] and now - _sent[1] < FLUSH_EVERY:
        return
    await shared_cache.aput(WANT_NS, WANT_KEY, wanted, WANT_TTL)
    if not shared_cache._up():
        return                                     # not written (Redis down): write it again once it is back
    _sent = (keys, now)
    _stats["flushes"] += 1


def _valid(value: object) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, list[str]] = {}
    total = 0
    for ns, keys in value.items():
        if ns not in EVERY or not isinstance(keys, list):
            return {}
        for k in keys:
            if not isinstance(k, str) or not 0 < len(k) <= MAX_KEY_CHARS:
                return {}
        total += len(keys)
        out[ns] = list(dict.fromkeys(keys))
    return out if total <= MAX_KEYS else {}


async def read() -> dict[str, list[str]]:
    """collector: the wanted keys by namespace ({} when none, or the value is not one the api wrote)."""
    hit = await shared_cache.aget(WANT_NS, WANT_KEY)
    return _valid(hit[0]) if hit else {}


async def serve(handlers: dict[str, Handler]) -> int:
    """collector: one pass - every wanted key due calls its namespace's handler. Returns the keys served."""
    if not serving():
        return 0
    wanted = await read()
    now = time.monotonic()
    with _lock:
        live = {(ns, k) for ns, ks in wanted.items() for k in ks}
        for k in [k for k in _due if k not in live]:       # no longer wanted: forget its timing
            _due.pop(k, None)
            _fails.pop(k, None)
        due = sorted((k for k in live if k[0] in handlers and _due.get(k, 0.0) <= now), key=lambda k: _due.get(k, 0.0))
    if not due:
        return 0
    started = time.monotonic()
    gate = asyncio.Semaphore(PARALLEL)
    served = 0

    async def one(ns: str, key: str) -> None:
        nonlocal served
        async with gate:
            if time.monotonic() - started > PASS_SEC:
                return                                     # the next pass takes it (still due)
            try:
                await asyncio.wait_for(handlers[ns](key), HANDLER_SEC)
            except Exception as exc:  # noqa: BLE001 - one key failing leaves the others; the api falls back
                with _lock:
                    n = _fails[(ns, key)] = _fails.get((ns, key), 0) + 1
                    _due[(ns, key)] = time.monotonic() + min(BACKOFF_MAX_SEC, EVERY[ns] * 2 ** n)
                    _stats["failed"] += 1
                log.info("demand: %s fetch failed: %s", ns, type(exc).__name__)
                return
            with _lock:
                _fails.pop((ns, key), None)
                # #999 the period runs from the pass's start, not this fetch's end: a key served late in a long
                # pass is due with the others on the pass EVERY later, not one serve loop after that
                _due[(ns, key)] = started + EVERY[ns]
                _stats["served"] += 1
            served += 1

    await asyncio.gather(*(one(ns, key) for ns, key in due))
    return served


def stats() -> dict:
    """For /api/status: counts only, no keys."""
    with _lock:
        return {"role": _role, "on": enabled() or serving(), "wanted": len(_seen), "collecting": len(_due), **_stats}


class ScreenReads:
    """ASGI middleware: an HTTP request's reads (and the tasks it starts) mark what they read; background jobs'
    do not. Each request runs in its own task, so the flag ends with it."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            SCREEN.set(True)
        await self.app(scope, receive, send)
