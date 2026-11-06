"""Live playbooks (#128): desk.playbook recomputed on the bars and the quote now.

The analysis' checklist is a snapshot: a scalp's "in the zone / rejection bar / reclaim" is stale
within minutes. This recomputes it on demand from the same cached bars the levels use
(desk.sr.frame_bars, desk.prices) and remembers when each side's state last changed. Results are
cached per horizon (the levels' TTL) so several pages on one ticker cost one computation.

``state_since`` is when this process first saw the side in its state (at most a TTL late; reset
by a restart): an observation time, not an exchange timestamp. Tracking an analysis, ``invalid``
sticks - a stop once crossed does not come back as a fresh entry. The time stop is not tracked
here (the scoring in desk.outcome judges it on the bars).
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from datetime import UTC, datetime

from . import horizons, playbook, trigger
from .sr import frame_bars, support_resistance

TIMEOUT_SEC = 25.0           # the whole computation, not each step
FAIL_TTL_SEC = 10.0          # a failure is remembered briefly so a poll does not pile up retries
_CACHE: OrderedDict[tuple, tuple[float, dict | Exception]] = OrderedDict()
_SINCE: OrderedDict[tuple, tuple[str, str]] = OrderedDict()   # (ticker, horizon, run, side) -> (state, since)
_LOCKS: dict[tuple, asyncio.Lock] = {}


def today(primary: list[dict], h: horizons.Horizon) -> dict | None:
    """The forming session from intraday bars, only when the window reaches back past its start."""
    if h.primary == "1d" or not primary:
        return None
    day = primary[-1]["at"][:10]
    first = next(i for i, b in enumerate(primary) if b["at"][:10] == day)
    if first == 0:
        return None
    session = primary[first:]
    return {"at": session[0]["at"], "open": session[0]["open"], "close": session[-1]["close"],
            "high": max(b["high"] for b in session), "low": min(b["low"] for b in session),
            "volume": sum(b.get("volume") or 0 for b in session)}


def _trim(d: OrderedDict, limit: int) -> None:
    while len(d) > limit:
        d.popitem(last=False)          # the oldest first


async def _compute(prices, ticker: str, h: horizons.Horizon, run_pb: dict | None) -> tuple[dict, dict | None]:
    try:
        quote = await prices.quote(ticker)
        live = {"price": quote.price, "source": quote.source, "fetched_at": quote.fetched_at}
    except Exception:  # noqa: BLE001 - the last close then, and the state is marked stale
        live = None
    sr = await support_resistance(prices, ticker, price=live["price"] if live else None, horizon=h.id)
    frame = horizons.get(None) if sr.get("fallback") else h
    primary, secondary, _c, _p, _s = await frame_bars(prices, ticker, None, frame)
    daily = primary if frame.primary == "1d" else secondary
    fresh = await asyncio.to_thread(playbook.build, sr, daily, primary, frame, live=live,
                                    today=today(primary, frame))
    if fresh is None:
        raise ValueError(f"no playbooks for {ticker}: too few bars or no levels")
    out = playbook.track(fresh, run_pb, primary, frame)
    await _triggers(prices, ticker, out, frame, live)
    return out, live


async def _triggers(prices, ticker: str, out: dict, frame: horizons.Horizon, live: dict | None) -> None:
    """The lower-bar entry trigger (#149) on each side near or in its zone; ``trigger`` None
    otherwise, or when the lower bars cannot be had (the page keeps the zone state)."""
    lower = trigger.LOWER.get(frame.primary)
    wanted = [s for s in ("long", "short")
              if (out.get(s) or {}).get("state") in ("near", "in_zone", "confirmed")]
    for s in ("long", "short"):
        if out.get(s) is not None:
            out[s]["trigger"] = None
    if not lower or not wanted:
        return
    try:
        bars = (await prices.candles(ticker, lower, trigger.LOOKBACK[lower]))["bars"]
    except Exception:  # noqa: BLE001 - the trigger is an extra; the zone state still stands
        return
    atr = out["frame"].get("atr") or 0.0
    price = live["price"] if live else None
    for s in wanted:
        out[s]["trigger"] = trigger.live(out[s], bars, s == "long", atr, price)
    out["frame"] = {**out["frame"], "trigger_interval": lower}


async def current(prices, ticker: str, horizon: str | None = None, run_id: str | None = None,
                  run_pb: dict | None = None, clock=time.monotonic) -> dict:
    """Both playbooks now; with ``run_pb`` (an analysis' playbooks) its levels stay."""
    h = horizons.get(horizon)
    key = (ticker, h.id, run_id)
    if len(_LOCKS) > 512:              # forget locks of keys no longer cached
        for stale in [k for k in _LOCKS if k not in _CACHE and not _LOCKS[k].locked()]:
            del _LOCKS[stale]
    lock = _LOCKS.setdefault(key, asyncio.Lock())
    async with lock:                   # two pages on one ticker wait for one computation
        cached = _CACHE.get(key)
        if cached:
            at, value = cached
            if isinstance(value, Exception) and clock() - at < FAIL_TTL_SEC:
                raise value
            if not isinstance(value, Exception) and clock() - at < h.sr_ttl_sec:
                return value
        try:
            out, live = await asyncio.wait_for(_compute(prices, ticker, h, run_pb), TIMEOUT_SEC)
        except Exception as exc:
            _CACHE[key] = (clock(), exc)
            _trim(_CACHE, 256)
            raise
        now = datetime.now(UTC).isoformat(timespec="seconds")
        for side in ("long", "short"):
            k = (*key, side)
            state = out[side].get("state")
            previous = _SINCE.get(k)
            if run_id and previous and previous[0] == "invalid":
                state = out[side]["state"] = "invalid"          # a crossed stop stays crossed
            if state is not None and live is not None and (previous is None or previous[0] != state):
                _SINCE[k] = (state, now)               # a fallback price never records a transition
                _SINCE.move_to_end(k)
            out[side]["state_since"] = _SINCE[k][1] if state is not None and k in _SINCE else None
            out[side]["stale"] = live is None
        out.update(ticker=ticker, horizon=h.id, run=run_id, as_of=now, live=live,
                   levels_from="run" if run_pb else "fresh")
        _trim(_SINCE, 1024)
        _CACHE[key] = (clock(), out)
        _CACHE.move_to_end(key)
        _trim(_CACHE, 256)
        return out
