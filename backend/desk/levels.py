"""Make sure a plan carries entry, stop and target.

The model is asked for all three (desk.profile) but may still leave one out. A
missing level is filled from recent prices: entry = last close up to the analysis
date, stop = entry ∓ 1.5 ATR(14), target = entry ± 3 ATR(14) — a 2:1 trade sized to
the instrument's own volatility. Each level records where it came from, so the page
can tell the model's call from the arithmetic.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

from .plan import reward_risk

STOP_ATR = 1.5
TARGET_ATR = 3.0
ATR_DAYS = 14
LEVELS = ("entry_price", "stop_loss", "price_target")

PriceFn = Callable[[str, str], tuple[float, float]]


def last_close_and_atr(ticker: str, trade_date: str) -> tuple[float, float]:
    """(last close, Wilder ATR14) from Yahoo daily bars up to trade_date."""
    import yfinance as yf

    end = date.fromisoformat(trade_date) + timedelta(days=1)
    bars = yf.Ticker(ticker).history(start=end - timedelta(days=120), end=end, interval="1d",
                                     auto_adjust=False)
    if len(bars) < ATR_DAYS + 1:
        raise ValueError(f"not enough daily bars for {ticker} up to {trade_date}")
    high, low, close = bars["High"].tolist(), bars["Low"].tolist(), bars["Close"].tolist()
    ranges = [max(h - l, abs(h - pc), abs(l - pc)) for h, l, pc in zip(high[1:], low[1:], close[:-1])]
    atr = sum(ranges[:ATR_DAYS]) / ATR_DAYS
    for tr in ranges[ATR_DAYS:]:
        atr = (atr * (ATR_DAYS - 1) + tr) / ATR_DAYS
    return float(close[-1]), float(atr)


def _round(price: float) -> float:
    # KRW and other large quotes trade in whole units; dollars in cents.
    return float(round(price)) if price >= 1000 else round(price, 2)


def fill_levels(plan: dict, ticker: str, trade_date: str, prices: PriceFn = last_close_and_atr) -> dict:
    """The plan with missing levels filled for a long or short bias. Flat/review plans and
    complete plans come back unchanged apart from the source markers."""
    plan = dict(plan)
    source = {key: "model" for key in LEVELS if plan.get(key) is not None}
    direction = {"long": 1, "short": -1}.get(plan.get("bias"))
    missing = [key for key in LEVELS if plan.get(key) is None]
    if direction is not None and missing:
        try:
            close, atr = prices(ticker, trade_date)
        except Exception as exc:  # noqa: BLE001 - no data means no computed levels, not a failed run
            plan["levels_error"] = f"{type(exc).__name__}: {exc}"
        else:
            entry = plan.get("entry_price") or close
            computed = {"entry_price": close,
                        "stop_loss": entry - direction * STOP_ATR * atr,
                        "price_target": entry + direction * TARGET_ATR * atr}
            for key in missing:
                plan[key] = _round(computed[key])
                source[key] = "atr"
            plan["atr"] = _round(atr)
            plan["last_close"] = _round(close)
    plan["levels_source"] = source
    plan["reward_risk"] = reward_risk(plan.get("entry_price"), plan.get("stop_loss"), plan.get("price_target"))
    return plan
