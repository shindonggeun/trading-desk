"""Recent days against each other (#152): what the last sessions did compared with yesterday, the
day before, last week and the last month - the desk's arithmetic, so the agents interpret numbers
instead of reading them off a chart.

``compare(days)`` takes daily bars oldest first (the last one may be today's forming session) and
returns changes over 1 / 2 / 3 / 5 sessions and 1 week / 2 weeks / 1 month, where the price sits in
the month's range, ATR(5) against ATR(20) (volatility expanding or calming), volume against its
20-day average, the run of up or down closes, the last open's gap, and whether the last bar made a
higher low / lower high than the one before. ``prompt_note`` renders it for the analysis.
"""

from __future__ import annotations

from itertools import pairwise

LOOKBACKS = {"d1": 1, "d2": 2, "d3": 3, "d5": 5, "w2": 10, "m1": 21}      # d5 is the week


def _tr(bars: list[dict]) -> list[float]:
    return [max(b["high"] - b["low"], abs(b["high"] - p["close"]), abs(b["low"] - p["close"]))
            for p, b in pairwise(bars)]


def _pct(now: float, then: float | None) -> float | None:
    return round((now / then - 1) * 100, 2) if then else None


def compare(days: list[dict], partial: bool = False) -> dict | None:
    """``partial``: the last bar is today's session still trading - its volume is left out (a
    morning against full days means nothing) and the note says so."""
    if len(days) < 6:
        return None
    last, prev = days[-1], days[-2]
    close = last["close"]
    closes = [b["close"] for b in days]
    changes = {k: _pct(close, closes[-1 - n]) if len(closes) > n else None for k, n in LOOKBACKS.items()}
    month = days[-21:]
    hi, lo = max(b["high"] for b in month), min(b["low"] for b in month)
    tr = _tr(days[-21:])
    atr5 = sum(tr[-5:]) / 5 if len(tr) >= 5 else None
    atr20 = sum(tr[-20:]) / 20 if len(tr) >= 20 else None
    vols = [b.get("volume") or 0 for b in days]
    avg20 = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else None
    if partial or not last.get("volume"):
        avg20 = None                                   # no volume to compare (or a partial day's)
    streak, sign = 0, 0
    for a, b in zip(reversed(closes[:-1]), reversed(closes[1:]), strict=False):
        step = (b > a) - (b < a)
        if step == 0 or (sign and step != sign):
            break
        sign, streak = step, streak + 1
    return {
        "as_of": last["at"], "partial": partial,
        "close": close,
        "changes_pct": changes,
        "sessions": [{"at": b["at"][:10], "open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"],
                      "change_pct": _pct(b["close"], p["close"])} for p, b in pairwise(days[-6:])],
        "month_high": hi, "month_low": lo,
        "month_position_pct": round((close - lo) / (hi - lo) * 100, 1) if hi > lo else None,
        "atr5_vs_atr20": round(atr5 / atr20, 2) if atr5 and atr20 else None,
        "volume_x20": round(vols[-1] / avg20, 2) if avg20 else None,
        "streak": streak * sign,                   # +3 = three higher closes in a row, -2 = two lower
        "gap_pct": _pct(last["open"], prev["close"]),
        "higher_low": last["low"] > prev["low"],
        "lower_high": last["high"] < prev["high"],
    }


def _f(v: float | None, unit: str = "%") -> str:
    return "n/a" if v is None else f"{v:+.1f}{unit}"


def _p(v: float | None) -> str:
    """A price without scientific notation (0.000012, not 1.2e-05)."""
    return "n/a" if v is None else f"{v:,.8f}".rstrip("0").rstrip(".")


def prompt_note(r: dict | None) -> str | None:
    """The comparison for the analysis (numbers from the desk; the agents interpret, not recompute).
    A value the bars cannot give is left out, not printed as None."""
    if not r:
        return None
    c = r["changes_pct"]
    which = "today's session so far (partial)" if r.get("partial") else "the last completed session"
    first = (f"Recent days compared (desk arithmetic on daily bars; the last bar is {which}): close {_p(r['close'])}; "
             f"vs 1 session {_f(c['d1'])}, 2 {_f(c['d2'])}, 3 {_f(c['d3'])}, 1 week (5) {_f(c['d5'])}; "
             f"2 weeks {_f(c['w2'])}, 1 month {_f(c['m1'])}.")
    bits = [f"month range {_p(r['month_low'])}-{_p(r['month_high'])}"
            + (f", the price at {r['month_position_pct']}% of it" if r.get("month_position_pct") is not None else "")]
    ratio = r.get("atr5_vs_atr20")
    if ratio is not None:
        state = "volatility expanding" if ratio > 1.2 else "calmer" if ratio < 0.8 else "normal"
        bits.append(f"ATR(5)/ATR(20) {ratio} ({state})")
    if r.get("volume_x20") is not None:
        bits.append(f"volume {r['volume_x20']}x its 20-day average")
    streak = r.get("streak") or 0
    bits.append(f"{abs(streak)} {'higher' if streak > 0 else 'lower'} closes in a row" if streak else "flat last close")
    if r.get("gap_pct") is not None:
        bits.append(f"last gap {_f(r['gap_pct'])}")
    bits.append("last bar " + ("made a higher low" if r["higher_low"] else "did not hold the prior low")
                + (" and a lower high" if r["lower_high"] else ""))
    sessions = "; ".join(f"{x['at']} O{_p(x['open'])} H{_p(x['high'])} L{_p(x['low'])} C{_p(x['close'])} "
                         f"({_f(x['change_pct'])})" for x in r["sessions"])
    body = "; ".join(bits)
    lines = [first, body[:1].upper() + body[1:] + ".", f"Sessions: {sessions}.",
             ("Weigh the last days and weeks first: a level the price made yesterday or this month outranks one "
              "from a range months ago.")]
    return "\n".join(lines)[:1400]
