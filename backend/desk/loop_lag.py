"""#859 how late the event loop runs, and what held it.

Every request, stream and background job of this process shares one event loop: a synchronous call on it
(a file write, a TLS context, a big computation) - or worker threads holding the GIL - makes every request
wait (``/api/health`` 1.6 s in production). Two pieces watch it:

- a task sleeps ``INTERVAL`` and records how late it woke up (the lag), the newest ``KEEP`` samples;
- a thread checks that task's heartbeat; when the loop has not come back for ``STALL_MS`` it reads the
  loop thread's stack once and names the innermost ``desk`` frame (``macro_news.py:441 fetch_theme_rss``)
  - the place that held the loop, or where the loop waited for the GIL (a plain socket read: worker
  threads' CPU work). When the loop is back the stall is kept (the newest ``KEEP_STALLS``) with its exact
  length, and logged as a warning - once per place per ``LOG_EVERY_SEC``, with the count since.

``stats()`` goes to ``/api/status`` (p50 · p95 · max of the last ``WINDOW_SEC``, the kept stalls by place);
``max_since(t)`` gives the slow-request log the longest wait while that request ran.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import sys
import threading
import time
from collections import Counter, deque

log = logging.getLogger(__name__)

INTERVAL = 0.1               # the heartbeat task's sleep (s)
KEEP = 3000                  # lag samples kept (5 min at INTERVAL, plus the time the loop was late)
WINDOW_SEC = 300.0           # the lag figures of stats() cover this many seconds
STALL_MS = 250.0             # the loop away this long is a stall: its place is read and logged
POLL_SEC = 0.05              # the watcher thread's check
KEEP_STALLS = 200
LOG_EVERY_SEC = 60.0         # one warning per place in this long (GIL contention can stall every cycle)
_PACKAGE = os.path.dirname(os.path.abspath(__file__))
# the real sleep, held at import: a test that swaps ``asyncio.sleep`` to drive a stream's rounds counts its
# own sleeps, not this heartbeat's
_sleep = asyncio.sleep


def _pct(values: list[float], q: float) -> float:
    s = sorted(values)
    return round(s[min(len(s) - 1, max(0, math.ceil(q * len(s)) - 1))], 1)


def site_of(frame) -> str:
    """The innermost frame of this package (``module.py:line function``) on ``frame``'s stack, else the
    innermost frame itself - where the loop thread stood."""
    first = None
    f = frame
    while f is not None:
        code = f.f_code
        here = f"{os.path.basename(code.co_filename)}:{f.f_lineno} {code.co_name}"
        first = first or here
        if os.path.dirname(os.path.abspath(code.co_filename)).startswith(_PACKAGE):
            return here
        f = f.f_back
    return first or "?"


class LoopLag:
    def __init__(self, interval: float = INTERVAL, stall_ms: float = STALL_MS, poll: float = POLL_SEC,
                 clock=time.monotonic) -> None:
        self.interval, self.stall_ms, self.poll, self.clock = interval, stall_ms, poll, clock
        self._lock = threading.Lock()
        self._samples: deque[tuple[float, float]] = deque(maxlen=KEEP)   # (monotonic when woken, lag ms)
        self._stalls: deque[dict] = deque(maxlen=KEEP_STALLS)
        self._beat: float | None = None
        self._loop_thread: int | None = None
        self._current: dict | None = None      # the stall under way (watcher thread only)
        self._logged: dict[str, tuple[float, int]] = {}   # place -> (last warning, stalls since)
        self._stop = threading.Event()

    # --- the loop side ---------------------------------------------------------------------

    async def run(self) -> None:
        """The heartbeat; also starts the watcher thread. Runs until cancelled."""
        self._loop_thread = threading.get_ident()
        self._stop.clear()
        watcher = threading.Thread(target=self._watch, name="loop-lag", daemon=True)
        watcher.start()
        try:
            while True:
                start = self._beat = self.clock()
                await _sleep(self.interval)
                now = self._beat = self.clock()
                with self._lock:
                    self._samples.append((now, max(0.0, (now - start - self.interval) * 1000)))
        finally:
            self._stop.set()

    # --- the watcher thread ----------------------------------------------------------------

    def _watch(self) -> None:
        while not self._stop.wait(self.poll):
            self.check()

    def check(self) -> None:
        """One look at the heartbeat (the watcher thread calls it every ``poll``; tests call it directly)."""
        beat, tid = self._beat, self._loop_thread
        if beat is None or tid is None:
            return
        now = self.clock()
        cur = self._current
        if cur is not None and cur["beat"] != beat:              # the loop came back: that stall is over
            self._current = None
            # its exact length: from the beat before it to the beat after it, less the heartbeat's own sleep
            self._close(cur, max(cur["ms"], (beat - cur["beat"] - self.interval) * 1000), now)
            cur = None
        away = (now - beat - self.interval) * 1000
        if away >= self.stall_ms:
            if cur is None:
                frame = sys._current_frames().get(tid)
                cur = self._current = {"beat": beat, "site": site_of(frame) if frame is not None else "?",
                                       "at": time.time(), "ms": away}
            cur["ms"] = max(cur["ms"], away)

    def _close(self, cur: dict, ms: float, now: float) -> None:
        stall = {"site": cur["site"], "ms": round(ms), "at": cur["at"]}
        with self._lock:
            self._stalls.append(stall)
        last, since = self._logged.get(stall["site"], (-math.inf, 0))
        if now - last >= LOG_EVERY_SEC:
            more = f" (+{since} more there in the last {now - last:.0f}s)" if since else ""
            log.warning("event loop blocked %dms at %s%s", stall["ms"], stall["site"], more)
            self._logged[stall["site"]] = (now, 0)
        else:
            self._logged[stall["site"]] = (last, since + 1)

    # --- readers ---------------------------------------------------------------------------

    def max_since(self, start: float) -> float:
        """The longest loop wait (ms) seen since ``start`` (this clock): the samples taken after it, and
        how long the loop has been away right now."""
        with self._lock:
            samples = list(self._samples)
        lags = [lag for at, lag in samples if at >= start]
        beat = self._beat
        now_away = (self.clock() - beat - self.interval) * 1000 if beat is not None else 0.0
        return round(max([0.0, now_away, *lags]), 1)

    def stats(self) -> dict:
        """The lag of the last ``WINDOW_SEC`` and the kept stalls (the newest ``KEEP_STALLS``) by place."""
        with self._lock:
            samples, stalls = list(self._samples), list(self._stalls)
        cutoff = self.clock() - WINDOW_SEC
        lags = [lag for at, lag in samples if at >= cutoff]
        recent = [s for s in stalls if s["at"] >= time.time() - WINDOW_SEC]
        by_site = Counter(s["site"] for s in stalls)
        worst: dict[str, int] = {}
        for s in stalls:
            worst[s["site"]] = max(worst.get(s["site"], 0), s["ms"])
        return {
            "window_sec": WINDOW_SEC, "samples": len(lags),
            "p50_ms": _pct(lags, 0.5) if lags else None, "p95_ms": _pct(lags, 0.95) if lags else None,
            "max_ms": round(max(lags), 1) if lags else None,
            "stall_ms": self.stall_ms, "stalls_recent": len(recent), "stalls_kept": len(stalls),
            "sites": [{"site": site, "count": n, "max_ms": worst[site]} for site, n in by_site.most_common(8)],
        }
