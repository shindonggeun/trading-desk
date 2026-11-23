"""#536 step 1 - measure before fixing: how long each API request takes, per route.

A plain ASGI middleware (outermost, so the session check counts too):
- every response gets ``Server-Timing: app;dur=<ms>`` (the time to the response headers);
- a request slower than ``SLOW_MS`` logs one warning: the method, the route *template*
  (``/api/runs/{run_id}`` - never the ids or the query string), the ms, the status and, when the handler set
  ``request.state.cache``, its hit/miss; #858 then the handler's ``request.state.timing_note`` as ``key=value``
  words (``/api/batch/{kind}``: ``kind=levels n=24``, ``/api/estimates``: ``n=12`` - counts, never tickers);
  and at the end ``loop_max=<ms>``, the longest the event loop was away
  while it ran (#859, desk.loop_lag) - a slow request with a long loop wait waited for the loop, not its work;
- per route the newest ``KEEP`` durations stay in memory for ``stats()`` (count · p50 · p95 · max), shown by
  ``GET /api/perf/timings`` so production can be read without parsing logs.

Streams (``text/event-stream``) are left out of the numbers: they stay open on purpose. Nothing here changes
a response's body or status. (``desk.timing`` is something else: an analysis run's stages, #171.)
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from collections import deque

log = logging.getLogger(__name__)

SLOW_MS = 800.0
KEEP = 500                   # durations kept per route (the newest)


def _pct(values: list[float], q: float) -> float:
    """Nearest rank: the smallest value with at least ``q`` of the values at or under it."""
    s = sorted(values)
    return round(s[min(len(s) - 1, max(0, math.ceil(q * len(s)) - 1))], 1)


class Timings:
    """Per-route durations (thread-safe: the middleware adds on the loop, stats() may be read anywhere)."""

    def __init__(self, keep: int = KEEP) -> None:
        self.keep = keep
        self._lock = threading.Lock()
        self._by_route: dict[str, deque] = {}
        self.since = time.time()

    def add(self, route: str, ms: float) -> None:
        with self._lock:
            self._by_route.setdefault(route, deque(maxlen=self.keep)).append(ms)

    def stats(self) -> list[dict]:
        with self._lock:
            rows = [(r, list(d)) for r, d in self._by_route.items() if d]
        out = [{"route": r, "count": len(v), "p50_ms": _pct(v, 0.5), "p95_ms": _pct(v, 0.95), "max_ms": round(max(v), 1)}
               for r, v in rows]
        return sorted(out, key=lambda x: -x["p95_ms"])

    def reset(self) -> None:
        with self._lock:
            self._by_route.clear()
            self.since = time.time()


NOTE_KEYS = ("kind", "n")           # #858 what a handler may add to its slow line - never a ticker
_NOTE_VALUE = re.compile(r"[A-Za-z0-9_-]{1,24}")


def _note(note: object) -> str:
    """#858 the handler's ``request.state.timing_note`` as ``key=value`` words: only NOTE_KEYS (a batch's kind,
    how many tickers), each a short ASCII word or a count; anything else is left out."""
    if not isinstance(note, dict):
        return ""
    words = []
    for k in NOTE_KEYS:
        v = note.get(k)
        if isinstance(v, int | str) and not isinstance(v, bool) and _NOTE_VALUE.fullmatch(str(v)):
            words.append(f" {k}={v}")
    return "".join(words)


def route_of(scope: dict) -> str:
    """The matched route's template (no ids, no query); "unmatched" for an API path no route took."""
    path = getattr(scope.get("route"), "path", None)
    if path:
        return path
    raw = scope.get("path", "")
    return "unmatched" if raw.startswith("/api/") else "static"


class TimingMiddleware:
    def __init__(self, app, timings: Timings, slow_ms: float = SLOW_MS, loop_max=None) -> None:
        self.app, self.timings, self.slow_ms = app, timings, slow_ms
        self.loop_max = loop_max          # #859 desk.loop_lag.LoopLag.max_since, or None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()
        loop_start = time.monotonic()

        async def timed_send(message):
            if message["type"] == "http.response.start":
                ms = (time.perf_counter() - start) * 1000
                headers = list(message.get("headers") or [])
                stream = any(k.lower() == b"content-type" and v.startswith(b"text/event-stream") for k, v in headers)
                message = {**message, "headers": [*headers, (b"server-timing", f"app;dur={ms:.1f}".encode())]}
                if not stream:
                    route = route_of(scope)
                    self.timings.add(route, ms)
                    if ms >= self.slow_ms:
                        state = scope.get("state") or {}
                        cache = state.get("cache")
                        waited = f" loop_max={self.loop_max(loop_start):.0f}ms" if self.loop_max else ""
                        log.warning("slow request %s %s %.0fms status %s%s%s%s", scope.get("method"), route, ms,
                                    message.get("status"), f" cache {cache}" if cache in ("hit", "miss") else "",
                                    _note(state.get("timing_note")), waited)
            await send(message)

        await self.app(scope, receive, timed_send)
