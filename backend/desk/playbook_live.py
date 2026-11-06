"""Live playbooks (#128): desk.playbook recomputed on the bars and the quote now.

The analysis' checklist is a snapshot: a scalp's "in the zone / rejection bar / reclaim" is stale
within minutes. This recomputes it on demand from the same cached bars the levels use
(desk.sr.frame_bars, desk.prices) and remembers when each side's state last changed. Results are
cached per horizon (the levels' TTL) so several pages on one ticker cost one computation.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

from . import horizons, playbook
from .sr import frame_bars, support_resistance

TIMEOUT_SEC = 20.0
_CACHE: dict[tuple, tuple[float, dict]] = {}
_SINCE: dict[tuple, tuple[str, str]] = {}     # (ticker, horizon, run, side) -> (state, since)


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


async def current(prices, ticker: str, horizon: str | None = None, run_id: str | None = None,
                  run_pb: dict | None = None, clock=time.monotonic) -> dict:
    """Both playbooks now; with ``run_pb`` (an analysis' playbooks) its levels stay."""
    h = horizons.get(horizon)
    key = (ticker, h.id, run_id)
    cached = _CACHE.get(key)
    if cached and clock() - cached[0] < h.sr_ttl_sec:
        return cached[1]
    try:
        quote = await asyncio.wait_for(prices.quote(ticker), TIMEOUT_SEC)
        live = {"price": quote.price, "source": quote.source, "fetched_at": quote.fetched_at}
    except Exception:  # noqa: BLE001 - the last close then
        live = None
    sr = await asyncio.wait_for(
        support_resistance(prices, ticker, price=live["price"] if live else None, horizon=h.id), TIMEOUT_SEC)
    frame = horizons.get(None) if sr.get("fallback") else h
    primary, secondary, _c, _p, _s = await asyncio.wait_for(frame_bars(prices, ticker, None, frame), TIMEOUT_SEC)
    daily = primary if frame.primary == "1d" else secondary
    fresh = await asyncio.to_thread(playbook.build, sr, daily, primary, frame, live=live,
                                    today=today(primary, frame))
    if fresh is None:
        raise ValueError(f"no playbooks for {ticker}: too few bars or no levels")
    out = playbook.track(fresh, run_pb, primary, frame)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    for side in ("long", "short"):
        state = out[side].get("state")
        k = (*key, side)
        if state is not None and (k not in _SINCE or _SINCE[k][0] != state):
            _SINCE[k] = (state, now)
        out[side]["state_since"] = _SINCE[k][1] if state is not None else None
    out.update(ticker=ticker, horizon=h.id, run=run_id, as_of=now, live=live)
    if len(_CACHE) > 256:
        _CACHE.clear()
    if len(_SINCE) > 1024:
        _SINCE.clear()
    _CACHE[key] = (clock(), out)
    return out
