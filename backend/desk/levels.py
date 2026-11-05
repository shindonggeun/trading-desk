"""Make sure a plan carries entry, stop and target.

The model is asked for all three (desk.profile) but may still leave one out. A
missing level is filled from recent prices: entry = the live quote when the analysis
is for today (desk.prices: Toss, else Yahoo), otherwise the last close up to the
analysis date; stop = entry ∓ 1.5 ATR(14), target = entry ± 3 ATR(14) — a 2:1 trade sized to
the instrument's own volatility. Each level records where it came from, so the page
can tell the model's call from the arithmetic.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

from . import horizons
from .plan import reward_risk

STOP_ATR = 1.5
TARGET_ATR = 3.0
ATR_DAYS = 14
LEVELS = ("entry_price", "stop_loss", "price_target")
# A live quote this far from the last daily close is more likely a unit mismatch (a split
# the daily bars have not caught, another currency) than a move; levels then use the close.
LIVE_MAX_GAP = 0.25

PriceFn = Callable[[str, str], tuple[float, float]]


# Days of Yahoo history fetched per bar interval: enough for ATR14, inside its intraday limits.
_LOOKBACK_DAYS = {"1m": 5, "5m": 7, "60m": 30, "1d": 120}


def last_close_and_atr(ticker: str, trade_date: str, interval: str = "1d") -> tuple[float, float]:
    """(last close, Wilder ATR14) from Yahoo ``interval`` bars up to trade_date."""
    import yfinance as yf

    end = date.fromisoformat(trade_date) + timedelta(days=1)
    bars = yf.Ticker(ticker).history(start=end - timedelta(days=_LOOKBACK_DAYS.get(interval, 120)), end=end,
                                     interval=interval, auto_adjust=False)
    bars = bars.dropna(subset=["High", "Low", "Close"])
    if len(bars) < ATR_DAYS + 1:
        raise ValueError(f"not enough {interval} bars for {ticker} up to {trade_date}")
    high, low, close = bars["High"].tolist(), bars["Low"].tolist(), bars["Close"].tolist()
    ranges = [max(h - l, abs(h - pc), abs(l - pc)) for h, l, pc in zip(high[1:], low[1:], close[:-1])]
    atr = sum(ranges[:ATR_DAYS]) / ATR_DAYS
    for tr in ranges[ATR_DAYS:]:
        atr = (atr * (ATR_DAYS - 1) + tr) / ATR_DAYS
    return float(close[-1]), float(atr)


def _round(price: float) -> float:
    # KRW and other large quotes trade in whole units; dollars in cents.
    return float(round(price)) if price >= 1000 else round(price, 2)


def _close_and_atr(prices: PriceFn, ticker: str, trade_date: str,
                   h: horizons.Horizon) -> tuple[float, float, horizons.Horizon, str | None]:
    """ATR from the horizon's bars; when those are missing, daily bars with swing multiples
    (an intraday multiple of a daily ATR would put stop and target far too wide)."""
    if h.primary == "1d":
        return (*prices(ticker, trade_date), h, None)
    try:
        return (*prices(ticker, trade_date, h.primary), h, None)
    except Exception as exc:  # noqa: BLE001
        swing = horizons.get(None)
        close, atr = prices(ticker, trade_date)
        return close, atr, swing, (f"no {h.primary} bars ({type(exc).__name__}); levels use daily ATR "
                                   f"with swing multiples")


def fill_levels(plan: dict, ticker: str, trade_date: str, prices: PriceFn = last_close_and_atr,
                live: dict | None = None, *, horizon: str | None = None) -> dict:
    """The plan with missing levels filled for a long or short bias. Flat/review plans and
    complete plans come back unchanged apart from the source markers.

    ``live`` ({"price", "source", "fetched_at"}) is the quote at the end of a same-day analysis;
    when given, a computed entry starts from it instead of the last daily close. ``horizon``
    (desk.horizons) picks the bars the ATR comes from and how many ATRs stop and target sit."""
    plan = dict(plan)
    # a level that already says where it came from (desk.underlying: "converted") keeps it
    source = {key: (plan.get("levels_source") or {}).get(key, "model") for key in LEVELS if plan.get(key) is not None}
    direction = {"long": 1, "short": -1}.get(plan.get("bias"))
    missing = [key for key in LEVELS if plan.get(key) is None]
    if live:
        plan["live_price"] = live
    if direction is not None and missing:
        try:
            close, atr, used, fallback = _close_and_atr(prices, ticker, trade_date, horizons.get(horizon))
        except Exception as exc:  # noqa: BLE001 - no data means no computed levels, not a failed run
            plan["levels_error"] = f"{type(exc).__name__}: {exc}"
        else:
            if used.primary != "1d":
                plan["atr_interval"] = used.primary
            if fallback:
                plan["levels_warning"] = fallback
            use_live = bool(live) and abs(live["price"] - close) <= LIVE_MAX_GAP * close
            if live and not use_live:
                plan["levels_warning"] = (f"live price {live['price']} is over {LIVE_MAX_GAP:.0%} "
                                          f"from the last close {_round(close)}; levels use the close")
            base = live["price"] if use_live else close
            plan["reference_price"] = {"price": _round(base),
                                       "source": live["source"] if use_live else "close"}
            entry = plan.get("entry_price") or base
            computed = {"entry_price": base,
                        "stop_loss": entry - direction * used.stop_atr * atr,
                        "price_target": entry + direction * used.target_atr * atr}
            for key in missing:
                plan[key] = _round(computed[key])
                source[key] = "atr"
            plan["atr"] = _round(atr)
            plan["last_close"] = _round(close)
    plan["levels_source"] = source
    plan["reward_risk"] = reward_risk(plan.get("entry_price"), plan.get("stop_loss"), plan.get("price_target"))
    return plan


# Within this many ATR (or this fraction of the price, without an ATR) the entry is "now".
NOW_ATR = 0.5
NOW_PCT = 0.01
# Further than this the entry needs a move the holding period may never bring.
FAR_ATR = 2.0
FAR_PCT = 0.05


def entry_vs_live(plan: dict, atr: float | None = None) -> dict:
    """How the plan's levels sit against the live price: the gap to the entry, what kind of
    order that entry is, and whether the stop or target has already been passed.

    kind: now | limit (long below / short above the price: waits for a pullback or rally)
          | stop (long above / short below: waits for a breakout or breakdown)."""
    live = (plan.get("live_price") or {}).get("price")
    entry = plan.get("entry_price")
    direction = {"long": 1, "short": -1}.get(plan.get("bias"))
    # a quote fill_levels already distrusted (a split, another currency) frames nothing
    if not live or entry is None or direction is None or plan.get("levels_warning"):
        return plan
    # nor one that far from the plan's own levels when fill_levels had nothing to fill
    anchor = plan.get("last_close") or entry
    if abs(live - anchor) > LIVE_MAX_GAP * anchor:
        return plan
    plan = dict(plan)
    atr = atr or plan.get("atr")
    gap = entry - live
    gap_atr = abs(gap) / atr if atr else None
    near = gap_atr <= NOW_ATR if gap_atr is not None else abs(gap) / live <= NOW_PCT
    far = gap_atr > FAR_ATR if gap_atr is not None else abs(gap) / live > FAR_PCT
    kind = "now" if near else ("limit" if gap * direction < 0 else "stop")
    stop, target = plan.get("stop_loss"), plan.get("price_target")
    plan["entry_vs_live"] = {
        "live": live,
        "gap": _round(gap),
        "gap_pct": round(gap / live * 100, 2),
        "gap_atr": round(gap_atr, 2) if gap_atr is not None else None,
        "kind": kind,
        "far": far,
        # live already beyond the stop or the target: the level no longer frames a trade from here
        "stop_passed": stop is not None and (live - stop) * direction <= 0,
        "target_passed": target is not None and (target - live) * direction <= 0,
    }
    return plan
