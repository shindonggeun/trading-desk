"""Entry triggers on the lower bars (#149): the zone says where, a turn on the smaller bars says when.

A playbook's zone is a price band on the horizon's own bars (daily for swing, 5-minute for intraday).
Buying the moment the price touches it catches every falling knife that keeps falling. Here the
next smaller bars (``LOWER``) are walked from the touch: once a turn shows - the bar closes above
the previous bar's high, the session VWAP is taken back, a volume spike's low holds, or a higher
low forms (mirrored for a short) - the trigger is a stop order just past that signal bar, and the
invalidation the turn's extreme less 0.25 of the lower bars' ATR, or the plan's stop when that is
nearer. ``scan`` is one pass over bars, so the live page (desk.playbook_live) and the backtest
(desk.trigger_bt) run the same rule.

States: ``armed`` (the zone was touched; waiting for a signal, or for the price to reach the
trigger), ``triggered`` (the trigger traded) and ``invalid`` (the plan's stop traded first, or -
live - a filled trigger's invalidation broke since). A signal whose turn extreme is broken before
it triggers is dropped and the scan looks for the next one. One bar that reaches both sides is read
against the trade (the break first) unless it opens through the trigger. Everything is arithmetic
on bars.
"""

from __future__ import annotations

from itertools import pairwise

LOWER = {"1d": "60m", "60m": "5m", "5m": "1m"}          # the horizon's bars -> the bars the trigger is read on
LOOKBACK = {"60m": 21, "5m": 78, "1m": 90}              # live: lower bars scanned (3 sessions / 1 session / 90 min)
BUFFER_ATR = 0.1          # the trigger sits this much of the lower ATR past the signal bar
INVALID_ATR = 0.25        # the invalidation sits this much past the turn's extreme
CHASE_ATR = 1.0           # a trigger further than this (plan ATR) past the zone is a chase: not taken
SPIKE = 2.0               # volume this many times the 20-bar average makes an absorption bar
SIGNALS = ("break", "vwap_reclaim", "absorption", "higher_low")
# Which side should wait for the trigger rather than take the zone's limit ladder. The basket
# comparison (desk.trigger_bt, swing plans on 60m bars 2024-11~, 6 tickers, 10bp a side): the
# trigger made +0.026R a fill against the touch's -0.054R (held-out 30%: +0.067R vs -0.003R),
# MAE 0.56 vs 0.69 ATR, shorts -0.13R -> +0.02R, longs -0.01R -> +0.03R, at 42% filled vs 74%.
# The ladder stays as the plan's levels; the page says to wait for the trigger.
PREFERRED = {"long": True, "short": True}


def atr(bars: list[dict], n: int = 14) -> float | None:
    if len(bars) < 2:
        return None
    trs = [max(b["high"] - b["low"], abs(b["high"] - p["close"]), abs(b["low"] - p["close"]))
           for p, b in pairwise(bars)]
    trs = trs[-n:]
    return sum(trs) / len(trs) if trs else None


def _vwaps(bars: list[dict]) -> list[float | None]:
    """Session VWAP at each bar (a session = the bar's UTC date; Korean and US sessions do not cross it)."""
    out, day, pv, vol = [], None, 0.0, 0.0
    for b in bars:
        if b["at"][:10] != day:
            day, pv, vol = b["at"][:10], 0.0, 0.0
        v = b.get("volume") or 0
        pv += (b["high"] + b["low"] + b["close"]) / 3 * v
        vol += v
        out.append(pv / vol if vol else None)
    return out


def _signal(bars: list[dict], i: int, up: bool, vwaps: list[float | None], turn_at: int) -> str | None:
    """The signal bar ``i`` closes, read on bars[:i+1] only. ``turn_at`` is the turn extreme's index."""
    b, p = bars[i], bars[i - 1] if i else None
    if p is None:
        return None
    sgn = 1 if up else -1
    if sgn * (b["close"] - (p["high"] if up else p["low"])) > 0 and sgn * (b["close"] - b["open"]) > 0:
        return "break"
    v, pv = vwaps[i], vwaps[i - 1]
    same_session = p["at"][:10] == b["at"][:10]          # a session's first bar has no VWAP to take back
    if same_session and v is not None and pv is not None and sgn * (p["close"] - pv) < 0 < sgn * (b["close"] - v):
        return "vwap_reclaim"
    if i - turn_at >= 2:
        t = bars[turn_at]
        vols = [x.get("volume") or 0 for x in bars[max(0, turn_at - 20):turn_at]]
        avg = sum(vols) / len(vols) if vols else 0
        if avg and (t.get("volume") or 0) >= SPIKE * avg:
            return "absorption"            # the spike made the extreme and two bars since have not broken it
        mid = bars[i - 1]
        # a second, shallower turn: the bar before made a low above the turn's and this one closes above its high
        if (mid["low"] > t["low"] if up else mid["high"] < t["high"]) and \
                (mid["low"] < bars[i - 2]["low"] if up else mid["high"] > bars[i - 2]["high"]) and \
                sgn * (b["close"] - (mid["high"] if up else mid["low"])) > 0:
            return "higher_low"
    return None


def scan(bars: list[dict], side: dict, up: bool, plan_atr: float, lower_atr: float | None = None,
         touched: bool = False) -> dict | None:
    """Walk ``bars`` (oldest first) against one playbook side. None for a side with no zone, no
    bars or no ATR; else {"state": None (not touched yet) | armed | triggered | invalid, "price",
    "invalidation", "signal", "signal_at", "touched_at", "filled_at", "fill", "rr_t1"}. ``invalid``
    = the plan's stop traded before a trigger. ``touched``: the zone was reached before these bars
    (the live page's window starts after it), so the first bar is already armed."""
    zone = side.get("zone") if side else None
    if not zone or side.get("setup") in (None, "none") or not bars or not plan_atr:
        return None
    stop = side.get("stop")
    vwaps = _vwaps(bars)
    sgn = 1 if up else -1
    edge = zone["high"] if up else zone["low"]
    cap = edge + sgn * CHASE_ATR * plan_atr
    out = {"state": None, "price": None, "invalidation": None, "signal": None, "signal_at": None,
           "touched_at": None, "filled_at": None, "fill": None, "rr_t1": None, "lower_atr": None}
    turn = None                          # index of the extreme since the touch
    for i, b in enumerate(bars):
        far = b["low"] if up else b["high"]
        near = b["high"] if up else b["low"]
        if out["state"] is None:
            if not touched and sgn * (far - edge) > 0:
                continue
            out.update(state="armed", touched_at=b["at"])
            turn = i
        pending = out["price"] is not None and i > _index(bars, out["signal_at"])
        # a bar opening through a pending trigger fills at the open, whatever it does after
        if pending and sgn * (b["open"] - out["price"]) >= 0:
            out.update(state="triggered", filled_at=b["at"], fill=_round(b["open"]))
            return _with_rr(out, side, up)
        # otherwise a bar reaching both sides is read against the trade: the break first
        if stop is not None and sgn * (far - stop) <= 0:
            out.update(state="invalid", price=None, signal=None, signal_at=None, invalidation=None)
            return out
        if pending and sgn * (far - out["invalidation"]) <= 0:          # the turn failed before it triggered
            out.update(price=None, invalidation=None, signal=None, signal_at=None)
            pending = False
        if pending and sgn * (near - out["price"]) >= 0:
            out.update(state="triggered", filled_at=b["at"], fill=out["price"])
            return _with_rr(out, side, up)
        if sgn * (far - (bars[turn]["low"] if up else bars[turn]["high"])) < 0:
            turn = i
        if out["price"] is not None:
            continue
        sig = _signal(bars, i, up, vwaps, turn)
        if not sig:
            continue
        # the lower ATR as of the signal bar: bars after it are not known yet (the backtest walks the future)
        la = lower_atr or atr(bars[max(0, i - 14):i + 1]) or plan_atr * 0.25
        trigger = (b["high"] + BUFFER_ATR * la) if up else (b["low"] - BUFFER_ATR * la)
        if sgn * (trigger - cap) > 0:
            continue                                            # the turn already ran away from the zone
        extreme = bars[turn]["low"] if up else bars[turn]["high"]
        inval = extreme - sgn * INVALID_ATR * la
        if stop is not None:
            inval = max(inval, stop) if up else min(inval, stop)   # the nearer of the two
        out.update(price=_round(trigger), invalidation=_round(inval), signal=sig, signal_at=b["at"],
                   lower_atr=round(la, 6))
    return _with_rr(out, side, up)


def _with_rr(out: dict, side: dict, up: bool) -> dict:
    if out["price"] is not None:
        out["rr_t1"] = _rr(side, out["fill"] or out["price"], out["invalidation"], up)
    return out


def _index(bars: list[dict], at: str | None) -> int:
    return next((i for i, b in enumerate(bars) if b["at"] == at), -1)


def _rr(side: dict, entry: float, inval: float, up: bool) -> float | None:
    t1 = next((t for t in side.get("targets") or [] if t.get("role") == "t1"), None)
    risk = abs(entry - inval)
    if not t1 or t1.get("price") is None or not risk:
        return None
    return round((t1["price"] - entry) / risk if up else (entry - t1["price"]) / risk, 2)


def _round(price: float | None) -> float | None:
    if price is None:
        return None
    return round(price, 2) if abs(price) >= 1 else round(price, 4)


_KEYS = ("state", "price", "invalidation", "signal", "signal_at", "touched_at", "filled_at", "rr_t1")


def live(side: dict, lower: list[dict], up: bool, plan_atr: float, price: float | None,
         touched: bool = False) -> dict | None:
    """The trigger for the page now, or None before the zone is touched. The forming last bar is
    not a signal bar (its close is not one yet), only the price now: a pending trigger the quote
    has reached reads ``triggered``; a filled trigger whose invalidation has broken since (a bar
    after the fill, or the quote) reads ``invalid`` - that trade is over."""
    finished = lower[:-1] if len(lower) > 1 else lower
    res = scan(finished, side, up, plan_atr, touched=touched)
    if res is None or res["state"] is None:
        return None
    pref = PREFERRED["long" if up else "short"]
    if res["state"] == "invalid":
        return {**{k: None for k in _KEYS}, "state": "invalid", "touched_at": res["touched_at"], "preferred": pref}
    sgn = 1 if up else -1
    if res["state"] == "armed" and res["price"] is not None and price is not None \
            and sgn * (price - res["price"]) >= 0:
        res["state"] = "triggered"
    if res["state"] == "triggered":
        # filled by the quote: only the forming bar is after it; filled on a finished bar: the bars since
        after = lower[-1:] if res["filled_at"] is None else [b for b in lower if b["at"] > res["filled_at"]]
        worst = [b["low"] if up else b["high"] for b in after] + ([price] if price is not None else [])
        if any(sgn * (w - res["invalidation"]) <= 0 for w in worst):
            res["state"] = "invalid"
    return {**{k: res[k] for k in _KEYS}, "preferred": pref}
