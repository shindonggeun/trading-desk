"""The report's candle charts (#682): for each subject a report draws, the bars of four timeframes (daily, 4h,
2h, 1h) and their nearest lines, kept with the report - so the page draws the same picture later, whatever
the prices do after.

The lines of a timeframe are desk.chart_read's (#631: channel and pattern lines, trendlines, unbroken swing
highs / lows, Fibonacci levels; the daily's open gaps are left out - the chart page adds them) - the nearest
three each side and the next three. The 2h and 4h bars are folded from one fetch of 1h bars (by the exchange's
day, as desk.prices does).
A line is drawn as a band ``ZONE_ATR`` of that timeframe's ATR each side. Where lines of two or more
timeframes fall within ``CONFLUENCE_ATR`` of the daily ATR of each other, they are one place several
timeframes point at (``confluence``): "일봉 · 4시간 · 1시간이 모두 가리키는 지지 141~143". Arithmetic on
price history - neither a forecast nor advice.
"""

from __future__ import annotations

import itertools
import logging
import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from . import channels, chart_read

log = logging.getLogger(__name__)

FRAMES = ("1d", "240m", "120m", "60m")
FRAME_KO = {"1d": "일봉", "240m": "4시간", "120m": "2시간", "60m": "1시간"}
KEEP = 120                 # bars a timeframe keeps in the stored snapshot (the small chart's window)
HOURLY_FETCH = 1200        # 1h bars fetched once: 500 for 1h, 2h and 4h folded from them (~185 trading days)
ZONE_ATR = 0.15            # a line drawn as a band this many of its timeframe's ATR each side
CONFLUENCE_ATR = 0.35      # lines of different timeframes this close (daily ATR) are one place
CONFLUENCE_MAX = 4         # the nearest places kept
MAX_SYMBOLS = 3            # charts a report keeps (the subject, its index, one more)
NOTE = "여러 시간봉의 선을 같은 차트에 모은 거예요 — 예측도, 매수 · 매도 추천도 아니에요."

BarsFetch = Callable[[str, str, int], list[dict]]


def _atr(bars: list[dict], n: int = 14) -> float | None:
    if len(bars) < 2:
        return None
    trs = [max(b["high"] - b["low"], abs(b["high"] - a["close"]), abs(b["low"] - a["close"]))
           for a, b in itertools.pairwise(bars)]
    tail = trs[-n:]
    return sum(tail) / len(tail) if tail else None


def _r(x: float) -> float:
    return channels._price(x)


def zones_of(frame: dict, atr: float) -> list[dict]:
    """A timeframe's lines as bands: nearest first a side (``near``: one of the three the read names)."""
    lv = frame.get("levels") or {}
    out = []
    for side, kind in (("above", "resistance"), ("below", "support")):
        for near, rows in ((True, lv.get(side) or []), (False, (lv.get("far") or {}).get(side) or [])):
            for row in rows:
                w = ZONE_ATR * atr
                out.append({"price": row["price"], "low": _r(row["price"] - w), "high": _r(row["price"] + w),
                            "kind": kind, "name": row["name"], "distance_pct": row["distance_pct"], "near": near})
    return out


def confluence(levels: list[tuple[str, dict]], close: float, tol: float) -> list[dict]:
    """Places two or more timeframes point at: the lines sorted by price, a line within ``tol`` of the group's
    first joining it; a group of one timeframe is no place. Nearest the close first, CONFLUENCE_MAX of them."""
    groups: list[list[tuple[str, dict]]] = []
    for tf, z in sorted(levels, key=lambda x: x[1]["price"]):
        if groups and z["price"] - groups[-1][0][1]["price"] <= tol:
            groups[-1].append((tf, z))
        else:
            groups.append([(tf, z)])
    # bands of neighbouring groups that still overlap are one place (two labels over the same prices read as two)
    merged: list[list[tuple[str, dict]]] = []
    for g in groups:
        if (merged and min(z["low"] for _, z in g) <= max(z["high"] for _, z in merged[-1])
                and max(z["high"] for _, z in g) - min(z["low"] for _, z in merged[-1]) <= 3 * tol):   # never a long chain
            merged[-1] += g
        else:
            merged.append(g)
    out = []
    for g in merged:
        tfs = [tf for tf in FRAMES if any(t == tf for t, _ in g)]
        if len(tfs) < 2:
            continue
        low, high = min(z["low"] for _, z in g), max(z["high"] for _, z in g)
        mid = (low + high) / 2
        kind = "support" if mid <= close else "resistance"
        names = list(dict.fromkeys(z["name"] for _, z in g))[:3]
        label = (f"{' · '.join(FRAME_KO[t] for t in tfs)}이 모두 가리키는 {'지지' if kind == 'support' else '저항'} "
                 f"{_fmt(low)}~{_fmt(high)}")
        out.append({"low": _r(low), "high": _r(high), "kind": kind, "frames": tfs, "count": len(tfs),
                    "names": names, "label": label, "distance_pct": round((mid / close - 1) * 100, 2)})
    out.sort(key=lambda c: abs(c["distance_pct"]))
    return out[:CONFLUENCE_MAX]


def _fmt(p: float) -> str:
    return f"{p:,.0f}" if p >= 1000 else f"{p:,.2f}"


def fold(bars: list[dict], size: int, day_of: Callable[[str], str]) -> list[dict]:
    """1h bars folded ``size`` at a time within an exchange day (a 4h bar never spans two sessions)."""
    out: list[dict] = []
    count, last_day = 0, None
    for b in bars:
        day = day_of(b["at"])
        if not out or day != last_day or count == size:
            out.append(dict(b))
            count, last_day = 1, day
            continue
        o = out[-1]
        o["high"], o["low"] = max(o["high"], b["high"]), min(o["low"], b["low"])
        o["close"], o["volume"] = b["close"], (o.get("volume") or 0) + (b.get("volume") or 0)
        count += 1
    return out


def snapshot(symbol: str, bars_fetch: BarsFetch, now: datetime | None = None) -> dict | None:
    """The four timeframes' bars (the last KEEP) and bands, and the places they share; None without bars."""
    from .reports import _day, _zone_of

    zone = _zone_of(symbol)
    frames, levels, daily_atr, close = {}, [], None, None
    hourly: list[dict] | None = None

    def bars_of(tf: str) -> list[dict]:
        nonlocal hourly
        if tf == "1d":
            return bars_fetch(symbol, "1d", channels.BARS["1d"])
        if hourly is None:
            hourly = []                      # a failed fetch is not asked again for 2h / 4h
            hourly = [b for b in bars_fetch(symbol, "60m", HOURLY_FETCH) if b.get("close")]
        if tf == "60m":
            return hourly[-channels.BARS["60m"]:]
        return fold(hourly, channels.MINUTES[tf] // 60, lambda at: _day(at, zone))[-channels.BARS[tf]:]

    for tf in FRAMES:
        try:
            bars = [b for b in bars_of(tf) if b.get("close")]
        except Exception as exc:  # noqa: BLE001 - one timeframe missing leaves the others
            log.info("report chart %s %s: no bars (%s)", symbol, tf, type(exc).__name__)
            continue
        if len(bars) < 30:
            continue
        live = channels.is_live(bars, tf, now)
        try:
            f = chart_read.frame(bars, tf, live)
        except Exception as exc:  # noqa: BLE001 - the bars still draw, without lines
            log.info("report chart %s %s: no lines (%s)", symbol, tf, type(exc).__name__)
            f = {"levels": {}, "price": bars[-1]["close"], "trend": {"state": "sideways"}}
        atr = _atr(bars[:-1] if live and len(bars) > 2 else bars) or 0.0
        zones = zones_of(f, atr) if atr > 0 else []
        if tf == "1d":
            daily_atr = atr
        if close is None:
            close = f.get("price") or bars[-1]["close"]
        frames[tf] = {
            "bars": [[b["at"], _r(b["open"]), _r(b["high"]), _r(b["low"]), _r(b["close"])] for b in bars[-KEEP:]],
            "zones": zones, "price": f.get("price"), "trend": (f.get("trend") or {}).get("state"),
            "summary": f.get("summary"), "live_bar": live,
        }
        levels += [(tf, z) for z in zones]
    if not frames or not close:
        return None
    tol = CONFLUENCE_ATR * daily_atr if daily_atr else close * 0.005
    return {"symbol": symbol, "close": _r(close), "as_of": frames[next(iter(frames))]["bars"][-1][0],
            "frames": frames, "confluence": confluence(levels, close, tol), "note": NOTE}


def symbols_of(report: dict) -> list[str]:
    """The symbols the report's sections draw (a levels or zone chart naming one), in order, MAX_SYMBOLS."""
    out: list[str] = []
    for s in report.get("sections") or []:
        c = s.get("chart") if isinstance(s, dict) else None
        if isinstance(c, dict) and c.get("kind") in ("levels", "zone") and c.get("symbol") and c["symbol"] not in out:
            out.append(c["symbol"])
    return out[:MAX_SYMBOLS]


# ---------------------------------------------------------------- bars

def yahoo_bars(symbol: str, tf: str, count: int) -> list[dict]:
    from desk import prices

    from .reports import _bars

    return _bars(prices.yahoo_candles(symbol, tf, count))


def fake_bars(symbol: str, tf: str, count: int) -> list[dict]:
    """``DESK_FAKE_RUNS``: the made-up daily history (desk.reports.fake_fetch), each day cut into hours."""
    from .reports import fake_fetch

    daily, _ = fake_fetch(symbol)
    if tf == "1d":
        return daily[-count:]
    per = {"60m": 7, "120m": 4, "240m": 2}[tf]     # 1h is what snapshot asks; 2h / 4h stay for a direct call
    step = channels.MINUTES[tf]
    seed = sum(map(ord, symbol))
    out = []
    for d in daily[-(count // per + 2):]:
        start = datetime.fromisoformat(d["at"]).astimezone(UTC) + timedelta(hours=14, minutes=30)
        o, span = d["open"], d["high"] - d["low"]
        for k in range(per):
            c = d["open"] + (d["close"] - d["open"]) * (k + 1) / per + span * 0.25 * math.sin(k * 1.7 + seed)
            c = min(max(c, d["low"]), d["high"])
            out.append({"at": (start + timedelta(minutes=step * k)).isoformat(timespec="seconds"), "open": o,
                        "high": min(d["high"], max(o, c) * 1.002), "low": max(d["low"], min(o, c) * 0.998),
                        "close": c, "volume": d["volume"] / per})
            o = c
    return out[-count:]
