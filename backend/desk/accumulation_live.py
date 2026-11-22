"""Prepare chart and analysis accumulation views with the same completed-bar windows."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from . import accumulation, accumulation_bt, channels

log = logging.getLogger(__name__)
WINDOWS = {name: spec["window"] for name, spec in accumulation_bt.SPEC.items()}
DAILY_BARS = WINDOWS["mid"] + 1
WEEKLY_BARS = WINDOWS["long"] + 1


def _finished(bars, tf: str, view: str, now: datetime):
    if not bars:
        return None
    live = ((now - datetime.fromisoformat(bars[-1]["at"])).days < 7 if tf == "1w"
            else channels.is_live(bars, tf, now))
    return (bars[:-1] if live else bars)[-WINDOWS[view]:]


async def read(tfs, results, weeks, now: datetime, price: float | None) -> tuple[dict | None, dict | None]:
    """Read short (4h or 1h), mid (daily), and long (weekly) views from fetched candles."""
    def bars_of(tf):
        res = results[tfs.index(tf)] if tf in tfs else None
        return res["bars"] if res is not None and not isinstance(res, BaseException) else None

    try:
        short_tf, short_bars = None, None
        for tf in ("240m", "60m"):
            if bars := bars_of(tf):
                short_tf, short_bars = tf, _finished(bars, tf, "short", now)
                break
        by_view = {
            "short": short_bars,
            "mid": _finished(bars_of("1d"), "1d", "mid", now),
            "long": _finished(weeks, "1w", "long", now),
        }
        return await asyncio.to_thread(accumulation.views, by_view, {"short": short_tf} if short_tf else None,
                                       None, price)
    except Exception as exc:  # noqa: BLE001 - optional bands must not cost the chart or analysis
        log.warning("accumulation read failed: %s", type(exc).__name__)
        return None, None
