"""#689 A shared tier (Redis) behind some in-process caches - one value for the api, collector and analyst
processes (#688) instead of one fetch per process.

Redis is optional. With no ``DESK_REDIS_SENTINELS`` (or ``DESK_REDIS_URL``) nothing here talks to a server
and every cache stays the in-memory one it was. When Redis does not answer (timeout, refused, failover in
progress) the tier is skipped for ``DOWN_SEC`` and the caller goes on with its memory cache and its own
fetch - a cache must never make a page fail or wait.

- Keys are ``td:<namespace>:<key>``: the homelab Redis is shared with other projects. Every key is written
  with a TTL (the server has no ``maxmemory`` policy, so a key without one would stay forever).
- Values are JSON in an envelope ``{"v": value, "at": unix time written}``. A value whose JSON round trip is
  not equal to it (a tuple, a datetime, a dataclass) is not shared - the caller keeps it in memory only.
- ``get``/``put`` are for code in worker threads (``asyncio.to_thread``); called on the event loop's thread
  they skip Redis (a blocking socket call there would stall every request). ``aget``/``aput`` are for
  coroutines.
- What is never put here: broker tokens (#688 keeps them in the DB), session data, anything per user.
  The password comes from Vault (``kv/trading-desk/backend/prod/env`` ``DESK_REDIS_PASSWORD``) and is never logged.

``TieredCache`` wraps a ``desk.ttl_cache.TTLCache`` with the same calls, reading memory first, then Redis.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import Callable, Hashable
from contextlib import suppress
from typing import Any

from desk.ttl_cache import TTLCache

log = logging.getLogger(__name__)

PREFIX = "td:"
TIMEOUT_SEC = 0.25          # a LAN round trip is ~1 ms; past this the tier is treated as down
DOWN_SEC = 30.0             # how long to skip Redis after a failure
MAX_VALUE_BYTES = 512_000   # larger values stay in memory only

_lock = threading.Lock()
_sync = None                # redis.Redis (or a test double)
_async = None               # redis.asyncio.Redis (or a test double)
_down_until = 0.0
_stats = {"hits": 0, "misses": 0, "writes": 0, "skipped": 0, "errors": 0}
_unshareable: set[str] = set()


def configure(settings) -> None:
    """Build the clients from settings (``redis_sentinels`` · ``redis_master`` · ``redis_password`` ·
    ``redis_url``). Nothing configured = off. The import is lazy: without Redis settings the package is
    never loaded."""
    sentinels = tuple(getattr(settings, "redis_sentinels", ()) or ())
    url = getattr(settings, "redis_url", "") or ""
    if not sentinels and not url:
        use(None, None)
        return
    import redis
    import redis.asyncio
    from redis.asyncio.retry import Retry as AsyncRetry
    from redis.asyncio.sentinel import Sentinel as AsyncSentinel
    from redis.backoff import NoBackoff
    from redis.retry import Retry
    from redis.sentinel import Sentinel

    # no retries: a miss here costs one timeout, then the tier is skipped (the caller has its own fetch)
    side = {"socket_timeout": TIMEOUT_SEC, "socket_connect_timeout": TIMEOUT_SEC}
    opts = {**side, "decode_responses": True, "health_check_interval": 30,
            "password": getattr(settings, "redis_password", "") or None}
    sync_opts = {**opts, "retry": Retry(NoBackoff(), 0)}
    async_opts = {**opts, "retry": AsyncRetry(NoBackoff(), 0)}
    if sentinels:
        hosts = [_host_port(s) for s in sentinels]
        master = getattr(settings, "redis_master", "") or "master-redis"
        sync = Sentinel(hosts, sentinel_kwargs={**side, "retry": Retry(NoBackoff(), 0)}).master_for(master, **sync_opts)
        aio = AsyncSentinel(hosts, sentinel_kwargs={**side, "retry": AsyncRetry(NoBackoff(), 0)}).master_for(
            master, **async_opts)
        where = f"sentinel {master} via {len(hosts)} hosts"
    else:
        sync = redis.Redis.from_url(url, **sync_opts)
        aio = redis.asyncio.Redis.from_url(url, **async_opts)
        where = "url"
    use(sync, aio)
    log.info("shared cache on (%s)", where)


def _host_port(text: str) -> tuple[str, int]:
    host, _, port = text.strip().rpartition(":")
    if not host:
        return text.strip(), 26379
    return host, int(port)


def use(sync_client, async_client) -> None:
    """Set the clients directly (tests pass fakeredis; ``None, None`` turns the tier off)."""
    global _sync, _async, _down_until
    with _lock:
        _sync, _async, _down_until = sync_client, async_client, 0.0
        for k in _stats:
            _stats[k] = 0
        _unshareable.clear()


async def aclose() -> None:
    global _sync, _async
    aio, sync = _async, _sync
    _sync = _async = None
    # closing a dead connection is not worth an error: a debug line at most
    with suppress(Exception):
        if aio is not None:
            await aio.aclose()
    with suppress(Exception):
        if sync is not None:
            sync.close()


def enabled() -> bool:
    return _sync is not None or _async is not None


def stats() -> dict:
    """For /api/status: on/off, up/down, and hit counts in this process (no keys, no values)."""
    with _lock:
        down = max(0.0, _down_until - time.monotonic())
        return {"on": enabled(), "down_sec": round(down), **_stats}


def _up() -> bool:
    return time.monotonic() >= _down_until


def _failed(exc: BaseException) -> None:
    global _down_until
    with _lock:
        _stats["errors"] += 1
        first = time.monotonic() >= _down_until
        _down_until = time.monotonic() + DOWN_SEC
    if first:   # one line per outage, the exception type only (a message can carry a host or a reply)
        log.warning("shared cache unreachable (%s) - memory only for %ds", type(exc).__name__, int(DOWN_SEC))


def _count(name: str) -> None:
    with _lock:
        _stats[name] += 1


def _key(namespace: str, key: Hashable) -> str:
    parts = key if isinstance(key, tuple) else (key,)
    return PREFIX + namespace + ":" + ":".join("" if p is None else str(p) for p in parts)


def _encode(namespace: str, value: Any) -> str | None:
    try:
        text = json.dumps({"v": value, "at": time.time()}, separators=(",", ":"), allow_nan=False)
        same = json.loads(text)["v"] == value
    except (TypeError, ValueError):
        same, text = False, ""
    if not same or len(text) > MAX_VALUE_BYTES:
        if namespace not in _unshareable:   # once per namespace: it is a code fact, not an event
            _unshareable.add(namespace)
            log.info("shared cache: %s value does not round-trip through JSON - kept in memory only", namespace)
        _count("skipped")
        return None
    return text


def _decode(raw: str | None, left_ms: int | None) -> tuple[Any, float, float] | None:
    if raw is None:
        return None
    try:
        env = json.loads(raw)
        value, at = env["v"], float(env["at"])
    except (ValueError, KeyError, TypeError):
        return None
    left = (left_ms or 0) / 1000.0
    if left <= 0:
        return None
    return value, max(0.0, time.time() - at), left


def _on_loop_thread() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def get(namespace: str, key: Hashable) -> tuple[Any, float, float] | None:
    """``(value, age_sec, ttl_left_sec)`` or None. Skips Redis on the event loop's thread."""
    if _sync is None or not _up() or _on_loop_thread():
        return None
    try:
        pipe = _sync.pipeline(transaction=False)
        pipe.get(_key(namespace, key))
        pipe.pttl(_key(namespace, key))
        raw, left = pipe.execute()
    except Exception as exc:  # noqa: BLE001 - any client error means "no shared value"
        _failed(exc)
        return None
    hit = _decode(raw, left)
    _count("hits" if hit else "misses")
    return hit


def put(namespace: str, key: Hashable, value: Any, ttl: float) -> None:
    if _sync is None or ttl <= 0 or not _up() or _on_loop_thread():
        return
    text = _encode(namespace, value)
    if text is None:
        return
    try:
        _sync.set(_key(namespace, key), text, px=max(1, int(ttl * 1000)))
    except Exception as exc:  # noqa: BLE001
        _failed(exc)
        return
    _count("writes")


async def aget(namespace: str, key: Hashable) -> tuple[Any, float, float] | None:
    if _async is None or not _up():
        return None
    try:
        pipe = _async.pipeline(transaction=False)
        pipe.get(_key(namespace, key))
        pipe.pttl(_key(namespace, key))
        raw, left = await pipe.execute()
    except Exception as exc:  # noqa: BLE001
        _failed(exc)
        return None
    hit = _decode(raw, left)
    _count("hits" if hit else "misses")
    return hit


async def aput(namespace: str, key: Hashable, value: Any, ttl: float) -> None:
    if _async is None or ttl <= 0 or not _up():
        return
    text = _encode(namespace, value)
    if text is None:
        return
    try:
        await _async.set(_key(namespace, key), text, px=max(1, int(ttl * 1000)))
    except Exception as exc:  # noqa: BLE001
        _failed(exc)
        return
    _count("writes")


class TieredCache:
    """A ``TTLCache`` (memory, this process) in front of the shared tier, with the same calls.

    A memory miss asks Redis; a Redis hit is kept in memory for what is left of its TTL (and never longer
    than the memory cache's own TTL - ``local_max``), so most reads never leave the process. ``put`` writes
    both. ``ttl <= 0`` (the "do not keep" rule for failures) writes neither and drops the memory entry.
    Async callers use ``aget``/``aput``; the sync calls skip Redis on the event loop thread."""

    def __init__(self, namespace: str, local: TTLCache, local_max: float | None = None,
                 wall: Callable[[], float] = time.time) -> None:
        self.namespace, self.local = namespace, local
        self.local_max = local.ttl if local_max is None else local_max
        self._wall = wall

    # -- memory-only calls kept as they were
    def entry(self, key, *, now: float | None = None):
        return self.local.entry(key, now=now)

    def pop(self, key, default: Any = None):
        return self.local.pop(key, default)

    def values(self, *, now: float | None = None):
        return self.local.values(now=now)

    def clear(self) -> None:
        self.local.clear()

    def __len__(self) -> int:
        return len(self.local)

    @property
    def ttl(self) -> float:
        return self.local.ttl

    # -- two tiers
    def _keep(self, key, hit, now: float | None) -> Any:
        value, _age, left = hit
        self.local.put(key, value, min(left, self.local_max), now=now)
        return value

    def get(self, key, default: Any = None, *, ahead: float = 0.0, now: float | None = None):
        mine = self.local.get(key, _MISS, ahead=ahead, now=now)
        if mine is not _MISS:
            return mine
        hit = get(self.namespace, key)
        if hit is None or hit[2] <= ahead:
            return default
        return self._keep(key, hit, now)

    def put(self, key, value, ttl: float | None = None, *, now: float | None = None) -> None:
        life = self.local.ttl if ttl is None else float(ttl)
        self.local.put(key, value, ttl, now=now)
        put(self.namespace, key, value, life)

    async def aget(self, key, default: Any = None, *, ahead: float = 0.0, now: float | None = None):
        mine = self.local.get(key, _MISS, ahead=ahead, now=now)
        if mine is not _MISS:
            return mine
        hit = await aget(self.namespace, key)
        if hit is None or hit[2] <= ahead:
            return default
        return self._keep(key, hit, now)

    async def aput(self, key, value, ttl: float | None = None, *, now: float | None = None) -> None:
        life = self.local.ttl if ttl is None else float(ttl)
        self.local.put(key, value, ttl, now=now)
        await aput(self.namespace, key, value, life)


_MISS = object()
