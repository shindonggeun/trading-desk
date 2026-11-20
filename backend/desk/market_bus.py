"""#690 Market data pushed to the browser: quotes, flows, futures and alerts as they are seen, for
``GET /api/stream/market`` (docs/market-stream.md is the contract).

One process (``DESK_ROLE=all``, or no Redis): ``publish`` hands each event straight to this process's open
streams. Split (#688) with the shared Redis (#689): the process that saw it also publishes it, sealed, on a
pub/sub channel (``desk.shared_cache.channel``), and the api process subscribes and hands it on. Kafka is not
used: these are latest values and alerts - nothing to replay; a stream that missed something reconnects to
the latest values (``snapshot``) and the REST alert timeline.

- ``publish`` is safe from any thread (the alert dispatchers run in worker threads): it hops onto the loop.
- One ticker's quote goes out at most once a second (the newest wins); KIS ticks and polled quotes share it.
- A stream's queue is bounded; one that falls behind is told ``reset`` and closed, never slows the others.
- ``degraded()`` names the channels a page should poll again: the subscription is down (``bus_down``), or a
  channel expected to move (a session is open) has been quiet for ``STALE_SEC`` (``stale``).
- Nothing here blocks: a Redis publish runs in the background with the cache's short timeout; a failure is
  counted and logged once, and this process's own streams are served regardless.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import time
import uuid
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime

from . import shared_cache

log = logging.getLogger(__name__)

VERSION = 1
KINDS = ("quote", "flow", "futures", "alert")
DEFAULT_CHANNELS = ("flow", "futures", "alert")
REMOTE_KINDS = ("flow", "futures", "alert")   # what an api process only learns over Redis when split
QUOTE_GAP_SEC = 1.0          # one ticker's quote at most this often
QUOTE_STALE_SEC = 30.0       # keep polling when a requested ticker has stopped moving
STALE_SEC = 180.0            # a channel expected to move that has been quiet this long is degraded
HEARTBEAT_SEC = 15.0
LISTEN_POLL_SEC = 1.0        # one wait for a message on the subscription (then a ping when quiet)
QUEUE_MAX = 500              # events waiting for one stream; past this it is reset
RECONNECT_MAX_SEC = 30.0
LAG_KEEP = 500


class Subscription:
    """One open stream: what it asked for and its queue (filled on the loop thread)."""

    def __init__(self, channels: tuple[str, ...], tickers: frozenset[str], owner: bool) -> None:
        self.channels, self.tickers, self.owner = frozenset(channels), tickers, owner
        self.queue: asyncio.Queue = asyncio.Queue(QUEUE_MAX)
        self.overflowed = False

    def wants(self, event: dict) -> bool:
        kind = event["kind"]
        if kind not in self.channels:
            return False
        if kind == "quote":
            return event["key"] in self.tickers
        if kind == "alert" and event["key"] in OWNER_ONLY:
            return self.owner
        return True

    def offer(self, event: dict) -> None:
        if self.overflowed or not self.wants(event):
            return
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.overflowed = True


OWNER_ONLY = frozenset({"holding_alert"})   # desk.market_alerts.OWNER_ONLY: a holding's alert is the owner's


class Bus:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.on = False
        self.mode = "local"            # local | redis
        self.subscribe_remote = False
        self._remote_required = False
        self._clock = clock
        self._loop: asyncio.AbstractEventLoop | None = None
        self._origin = uuid.uuid4().hex[:12]
        self._subs: set[Subscription] = set()
        self._latest: dict[tuple[str, str], dict] = {}
        self._last_at: dict[str, float] = {}
        self._started = clock()
        self._expect: dict[str, Callable[[], bool]] = {}
        self._bus_down = False
        self._quote_sent: dict[str, float] = {}
        self._quote_pending: dict[str, tuple[dict, bool]] = {}
        self._tasks: set[asyncio.Task] = set()
        self._listener = None
        self._counts = {k: 0 for k in KINDS} | {"published_remote": 0, "received_remote": 0, "dropped": 0,
                                                "publish_errors": 0, "reconnects": 0, "overflows": 0}
        self._lag: dict[str, deque] = {k: deque(maxlen=LAG_KEEP) for k in KINDS}
        self._warned = False

    # -- setup -----------------------------------------------------------------------------------------
    def configure(self, settings, role: str) -> None:
        """At start, on the event loop. Off when ``DESK_MARKET_STREAM=off``; Redis only when the backend is
        split (``role`` other than all) and the shared cache is on."""
        self.on = bool(getattr(settings, "market_stream", True))
        self._loop = asyncio.get_running_loop()
        self._started = self._clock()
        remote = self.on and role != "all" and shared_cache.enabled()
        self.mode = "redis" if remote else "local"
        self.subscribe_remote = remote and role == "api"
        self._remote_required = role == "api"
        self._bus_down = self._remote_required
        if self.subscribe_remote:
            self._listener = shared_cache.listener(settings)
            self._spawn(self._listen())
        if self.on:
            log.info("market stream on (%s%s)", self.mode, ", subscribed" if self.subscribe_remote else "")

    def expect(self, kind: str, live: Callable[[], bool]) -> None:
        """While ``live()`` (its session is open) ``kind`` should move at least every STALE_SEC."""
        self._expect[kind] = live

    async def aclose(self) -> None:
        for sub in list(self._subs):
            sub.overflowed = True      # wakes nothing by itself: the stream sees it on its next heartbeat
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if self._listener is not None:
            with contextlib.suppress(Exception):
                await self._listener.aclose()
            self._listener = None
        self.on = False

    # -- publish ---------------------------------------------------------------------------------------
    def publish(self, kind: str, key: str, payload: dict, source: str, source_at: str | None = None) -> None:
        """Hand one event out (from any thread). A no-op while off."""
        if not self.on or self._loop is None or kind not in KINDS:
            return
        now = datetime.now(UTC)
        event = {"v": VERSION, "kind": kind, "key": str(key),
                 "event_id": uuid.uuid4().hex,
                 "source": source, "source_at": source_at,
                 "observed_at": now.isoformat(timespec="milliseconds"), "payload": payload}
        if _on_loop(self._loop):
            self._admit(event)
        else:
            with contextlib.suppress(RuntimeError):     # the loop is closing
                self._loop.call_soon_threadsafe(self._admit, event)

    def _admit(self, event: dict, relay: bool = True) -> None:
        if event["kind"] != "quote":
            self._emit(event, relay)
            return
        key, now = event["key"], self._clock()
        latest = self._latest.get(("quote", key))
        if latest is not None and event["observed_at"] < latest["observed_at"]:
            return
        pending = self._quote_pending.get(key)
        if pending is not None:
            if event["observed_at"] >= pending[0]["observed_at"]:
                self._quote_pending[key] = event, relay
            return
        wait = QUOTE_GAP_SEC - (now - self._quote_sent.get(key, -math.inf))
        if wait <= 0:
            self._quote_sent[key] = now
            self._emit(event, relay)
            return
        self._quote_pending[key] = event, relay
        self._loop.call_later(wait, self._flush_quote, key)

    def _flush_quote(self, key: str) -> None:
        pending = self._quote_pending.pop(key, None)
        if pending is not None and self.on:
            self._quote_sent[key] = self._clock()
            self._emit(*pending)

    def _emit(self, event: dict, relay: bool) -> None:
        self._deliver(event)
        if relay and self.mode == "redis":
            self._spawn(self._publish_remote(event))

    def _deliver(self, event: dict) -> None:
        kind = event["kind"]
        self._counts[kind] += 1
        self._last_at[kind] = self._clock()
        if kind != "alert":
            self._latest[(kind, event["key"])] = event
            if len(self._latest) > 2000:                  # never unbounded: drop the oldest quote
                oldest = next((k for k in self._latest if k[0] == "quote"), None)
                if oldest is not None:
                    del self._latest[oldest]
        with contextlib.suppress(ValueError, TypeError):
            lag = time.time() - datetime.fromisoformat(event["observed_at"]).timestamp()
            self._lag[kind].append(max(0.0, lag))
        for sub in list(self._subs):
            before = sub.overflowed
            sub.offer(event)
            if sub.overflowed and not before:
                self._counts["overflows"] += 1

    # -- Redis -----------------------------------------------------------------------------------------
    async def _publish_remote(self, event: dict) -> None:
        client = shared_cache.publisher()
        if client is None:
            return
        try:
            message = shared_cache.seal({**event, "origin": self._origin})
            await asyncio.wait_for(client.publish(shared_cache.channel("market"), message),
                                   shared_cache.TIMEOUT_SEC * 4)
            self._counts["published_remote"] += 1
        except Exception as exc:  # noqa: BLE001 - this process's own streams are served regardless
            self._counts["publish_errors"] += 1
            if not self._warned:
                self._warned = True
                log.warning("market stream publish failed (%s) - local streams only until it works", type(exc).__name__)

    async def _listen(self) -> None:
        """The api's subscription, kept up for the life of the process (reconnect with backoff)."""
        pause = 1.0
        while True:
            pubsub = None
            try:
                pubsub = self._listener.pubsub(ignore_subscribe_messages=True)
                await pubsub.subscribe(shared_cache.channel("market"))
                if self._bus_down:
                    log.info("market stream subscription back")
                self._bus_down, pause = False, 1.0
                pinged = self._clock()
                while True:
                    message = await pubsub.get_message(timeout=LISTEN_POLL_SEC)
                    if message is not None:
                        self._receive(message.get("data"))
                    elif self._clock() - pinged >= HEARTBEAT_SEC:
                        pinged = self._clock()
                        await asyncio.wait_for(pubsub.ping(), 5)   # a dead socket fails here, not silently
                    else:
                        await asyncio.sleep(0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                if not self._bus_down:
                    log.warning("market stream subscription lost (%s) - pages poll until it is back", type(exc).__name__)
                self._bus_down = True
                self._counts["reconnects"] += 1
            finally:
                if pubsub is not None:
                    with contextlib.suppress(Exception):
                        await pubsub.aclose()
            await asyncio.sleep(pause)
            pause = min(pause * 2, RECONNECT_MAX_SEC)

    def _receive(self, raw) -> None:
        event = shared_cache.unseal(raw) if isinstance(raw, str) else None
        if (event is None or event.get("v") != VERSION or event.get("kind") not in KINDS
                or not isinstance(event.get("key"), str) or not isinstance(event.get("payload"), dict)
                or not isinstance(event.get("observed_at"), str)):
            self._counts["dropped"] += 1
            return
        if event.pop("origin", None) == self._origin:      # our own, already delivered
            return
        self._counts["received_remote"] += 1
        self._admit(event, relay=False)

    # -- streams ---------------------------------------------------------------------------------------
    def subscribe(self, channels: tuple[str, ...], tickers: frozenset[str], owner: bool) -> Subscription:
        sub = Subscription(channels, tickers, owner)
        self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        self._subs.discard(sub)

    def snapshot(self, sub: Subscription) -> list[dict]:
        """The latest event of every key the stream asked for (alerts are not replayed: REST timeline)."""
        return [e for (kind, _key), e in list(self._latest.items()) if kind != "alert" and sub.wants(e)]

    def degraded(self, sub: Subscription | None = None) -> tuple[list[str], str | None]:
        """The channels a page should poll again, and why (``bus_down`` before ``stale``)."""
        out, reason = [], None
        if self._remote_required and self._bus_down:
            out, reason = list(REMOTE_KINDS), "bus_down"
        now = self._clock()
        if sub is not None and "quote" in sub.channels and any(
                now - self._quote_sent.get(ticker, -math.inf) > QUOTE_STALE_SEC for ticker in sub.tickers):
            out.append("quote")
            reason = reason or "stale"
        for kind, live in self._expect.items():
            if kind in out:
                continue
            try:
                expected = live()
            except Exception:  # noqa: BLE001 - a clock helper failing is "not expected"
                expected = False
            if expected and now - self._last_at.get(kind, self._started) > STALE_SEC:
                out.append(kind)
                reason = reason or "stale"
        return sorted(out), reason

    def stats(self) -> dict:
        """For /api/status: counts, delivery lag (observed -> handed to the streams) p50 · p95 in seconds."""
        lag = {}
        for kind, values in self._lag.items():
            if values:
                ordered = sorted(values)
                lag[kind] = {"n": len(ordered), "p50": round(_pct(ordered, 0.5), 3), "p95": round(_pct(ordered, 0.95), 3)}
        degraded, reason = self.degraded() if self.on else ([], None)
        return {"on": self.on, "mode": self.mode, "subscribed": self.subscribe_remote and not self._bus_down,
                "streams": len(self._subs), "degraded": degraded, "reason": reason, "lag_sec": lag, **self._counts}

    def _spawn(self, coro) -> None:
        task = self._loop.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


def _on_loop(loop: asyncio.AbstractEventLoop) -> bool:
    try:
        return asyncio.get_running_loop() is loop
    except RuntimeError:
        return False


def _pct(ordered: list[float], q: float) -> float:
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


BUS = Bus()


def publish(kind: str, key: str, payload: dict, source: str, source_at: str | None = None) -> None:
    """The process's bus (desk.market_bus.BUS); a no-op until the app configures it."""
    BUS.publish(kind, key, payload, source, source_at)
