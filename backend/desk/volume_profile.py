"""Volume profile (매물대, issue #106): how much traded at each price over the horizon's bars.

Each bar's volume is spread evenly over the price bins its high-low range touches — a daily bar
says nothing about where inside its range the shares changed hands, and even spreading is the
usual approximation. From the bins:

- POC (point of control): the bin with the most volume — the price the market agreed on most.
- Value area: the bins around the POC that hold 70% of the volume.
- Thick nodes (HVN): local peaks well above the average bin — supply/demand that slows price.
- Thin nodes (LVN): local troughs well below it — price tends to travel through them quickly.

Arithmetic from price history, not a forecast.
"""

from __future__ import annotations

import math

MIN_BINS = 24
MAX_BINS = 48
BIN_ATR = 0.25          # bin width ≈ a quarter ATR, kept within MIN/MAX_BINS over the range
VALUE_AREA = 0.70
THICK = 1.3             # a peak at 1.3× the average bin is a thick node
THIN = 0.5              # a trough under half the average is a thin one
MAX_NODES = 4           # per kind, the most marked first
GAP_THIN = 0.8          # a stretch averaging under 0.8× the average bin is a thin run


def _round(price: float) -> float:
    return float(round(price)) if price >= 1000 else round(price, 2)


def profile(bars: list[dict], atr: float | None = None, interval: str | None = None) -> dict | None:
    """The profile of ``bars`` ({"high","low","volume"}, oldest first), or None when there is no
    volume to profile (an index without volume, too few bars)."""
    # a vendor glitch (high under low, NaN) drops that bar, not the whole profile
    rows = [b for b in bars if (b.get("volume") or 0) > 0 and isinstance(b.get("high"), (int, float))
            and isinstance(b.get("low"), (int, float)) and math.isfinite(b["high"]) and math.isfinite(b["low"])
            and b["high"] >= b["low"]]
    if len(rows) < 10:
        return None
    lo, hi = min(b["low"] for b in rows), max(b["high"] for b in rows)
    if hi <= lo:
        return None
    span = hi - lo
    n = round(span / (BIN_ATR * atr)) if atr else MAX_BINS
    n = max(MIN_BINS, min(MAX_BINS, n))
    width = span / n
    vol = [0.0] * n
    for b in rows:
        first = min(n - 1, int((b["low"] - lo) / width))
        last = min(n - 1, int((b["high"] - lo) / width))
        share = b["volume"] / (last - first + 1)
        for i in range(first, last + 1):
            vol[i] += share
    total = sum(vol)
    poc = max(range(n), key=lambda i: vol[i])

    # value area: grow from the POC toward the busier neighbour until 70% is inside
    a = b_ = poc
    inside = vol[poc]
    while inside < VALUE_AREA * total and (a > 0 or b_ < n - 1):
        below = vol[a - 1] if a > 0 else -1.0
        above = vol[b_ + 1] if b_ < n - 1 else -1.0
        if above >= below:
            b_ += 1
            inside += above
        else:
            a -= 1
            inside += below

    mean = total / n
    mid = [lo + (i + 0.5) * width for i in range(n)]
    thick, thin = [], []
    for i in range(n):
        left = vol[i - 1] if i > 0 else -1.0
        right = vol[i + 1] if i < n - 1 else -1.0
        if vol[i] >= THICK * mean and vol[i] >= left and vol[i] >= right:
            thick.append(i)
        elif 0 < i < n - 1 and vol[i] <= THIN * mean and vol[i] <= left and vol[i] <= right:
            thin.append(i)
    thick.sort(key=lambda i: -vol[i])
    thin.sort(key=lambda i: vol[i])

    def node(i: int, kind: str) -> dict:
        return {"price": _round(mid[i]), "kind": kind, "share": round(vol[i] / total, 4)}

    return {
        "interval": interval,
        "bars": len(rows),
        "low": _round(lo),
        "high": _round(hi),
        "bin_width": _round(width),
        "bins": [{"price": _round(mid[i]), "volume": round(vol[i]), "share": round(vol[i] / total, 4)} for i in range(n)],
        "poc": _round(mid[poc]),
        "value_area": {"low": _round(lo + a * width), "high": _round(lo + (b_ + 1) * width)},
        "nodes": [node(i, "thick") for i in thick[:MAX_NODES]] + [node(i, "thin") for i in thin[:MAX_NODES]],
    }


def density(vp: dict | None, price: float) -> str | None:
    """"thick" / "thin" / None — how busy the profile is at ``price`` against its average bin."""
    if not vp or not vp.get("bins"):
        return None
    bins = vp["bins"]
    width = vp.get("bin_width") or 0
    if price < vp["low"] - width or price > vp["high"] + width:
        return None
    near = min(bins, key=lambda b: abs(b["price"] - price))
    mean = 1 / len(bins)
    if near["share"] >= THICK * mean:
        return "thick"
    if near["share"] <= THIN * mean:
        return "thin"
    return None


def gap_thin(vp: dict | None, a: float | None, b: float | None) -> bool | None:
    """Whether the stretch strictly between ``a`` and ``b`` is thin on average (price tends to
    travel it quickly); None when the profile does not cover it."""
    if not vp or a is None or b is None:
        return None
    lo, hi = min(a, b), max(a, b)
    inside = [x["share"] for x in vp.get("bins") or [] if lo < x["price"] < hi]
    if not inside:
        return None
    return sum(inside) / len(inside) <= GAP_THIN / len(vp["bins"])


def prompt_line(vp: dict | None) -> str | None:
    """One line for the agents: POC, value area and the nodes."""
    if not vp:
        return None
    thick = ", ".join(str(n["price"]) for n in vp["nodes"] if n["kind"] == "thick") or "none"
    thin = ", ".join(str(n["price"]) for n in vp["nodes"] if n["kind"] == "thin") or "none"
    va = vp["value_area"]
    return (f"Volume profile over the last {vp['bars']} {vp.get('interval') or ''} bars: POC {vp['poc']}, "
            f"value area {va['low']}-{va['high']} (70% of volume); thick nodes (heavy supply/demand, price "
            f"tends to stall) {thick}; thin nodes (price tends to move through quickly) {thin}.")
