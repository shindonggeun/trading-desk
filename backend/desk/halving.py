"""#514: the bitcoin halving cycles side by side - each past cycle's peak and bottom (dates, prices, days) and
where the cycle now stands against them (days since the halving, from the high, the lowest close since).

Every number is computed here from daily closes (Yahoo ``BTC-USD``, from 2014-09-17); nothing is taken from
posts. Before that there is no price: the 2012 cycle's peak is "no data" (``None``) and its bottom is the lowest
close of the data there is (``partial``). The halving dates are fixed by block height (every 210,000 blocks);
the next one is an estimate from the last cycle's average block time.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

log = logging.getLogger(__name__)

SYMBOL = "BTC-USD"
SOURCE = "Yahoo Finance BTC-USD 일봉 종가 (2014-09-17 부터)"
HALVINGS = (            # (n, block, date UTC, reward after)
    (1, 210_000, date(2012, 11, 28), 25.0),
    (2, 420_000, date(2016, 7, 9), 12.5),
    (3, 630_000, date(2020, 5, 11), 6.25),
    (4, 840_000, date(2024, 4, 20), 3.125),
)
BLOCKS = 210_000
# a cycle's peak is its highest close within this many days of the halving: the bull top, not the rally into
# the next halving (2020: 2021-11, not 2024-03); its bottom the lowest close after that peak (before the next)
PEAK_WINDOW_DAYS = 730
STEP_DAYS = 7           # one point a week in the overlay series
TTL_SEC = 24 * 3600
FAIL_TTL_SEC = 10 * 60
NOTES = (
    "바닥은 지나고 나서야 알 수 있어요. 지금까지의 최저가 바닥이라는 뜻이 아니에요.",
    "비교할 지난 사이클이 3개뿐이라 범위는 참고용이에요.",
)
NOTE_2012 = "2012 사이클은 2014-09-17 이전 가격이 없어 고점은 자료 부족이고, 바닥은 있는 자료 안에서 찾은 값이에요."

Closes = list[tuple[date, float]]          # oldest first, one per day


def _pct(a: float, b: float) -> float:
    return round((a / b - 1) * 100, 1)


def _point(d: date, price: float, start: date) -> dict:
    return {"date": d.isoformat(), "price": round(price, 2), "days_from_halving": (d - start).days}


def next_halving() -> dict:
    """The next halving's date from the last cycle's average block time (an estimate)."""
    (_, _, d3, _), (n4, b4, d4, r4) = HALVINGS[-2], HALVINGS[-1]
    est = d4 + (d4 - d3)                   # 210,000 blocks at the last cycle's pace
    return {"n": n4 + 1, "block": b4 + BLOCKS, "date": est.isoformat(), "reward_after": r4 / 2, "estimated": True}


def cycle(closes: Closes, start: date, end: date | None) -> dict:
    """One cycle (``start`` to the day before ``end``; open when ``end`` is None): its peak (the highest close
    within ``PEAK_WINDOW_DAYS``), its bottom (the lowest close after the peak) and how far each is from the halving."""
    days = [(d, p) for d, p in closes if d >= start and (end is None or d < end)]
    out = {"halving": start.isoformat(), "end": (end - timedelta(days=1)).isoformat() if end else None,
           "data_from": days[0][0].isoformat() if days else None, "partial": not days or days[0][0] > start,
           "peak": None, "bottom": None, "note": None}
    if not days:
        out["note"] = "이 사이클의 가격 자료가 없어요."
        return out
    if out["partial"]:
        # the peak may be before the data: no peak, and the bottom is only the lowest close there is
        low_d, low_p = min(days, key=lambda x: x[1])
        out["bottom"] = {**_point(low_d, low_p, start), "days_from_peak": None, "drawdown_pct": None}
        out["note"] = f"{days[0][0].isoformat()} 이전 가격이 없어 고점은 자료 부족이에요."
        return out
    window = [i for i, (d, _) in enumerate(days) if (d - start).days <= PEAK_WINDOW_DAYS]
    peak_i = max(window, key=lambda i: days[i][1])
    peak_d, peak_p = days[peak_i]
    out["peak"] = _point(peak_d, peak_p, start)
    after = days[peak_i + 1:]           # the lowest close after the peak day (none when the peak is the last)
    if not after:
        return out
    low_d, low_p = min(after, key=lambda x: x[1])
    out["bottom"] = {**_point(low_d, low_p, start), "days_from_peak": (low_d - peak_d).days,
                     "drawdown_pct": _pct(low_p, peak_p)}
    return out


def _range(values: list[tuple[str, float]]) -> dict | None:
    if not values:
        return None
    nums = [v for _, v in values]
    return {"min": min(nums), "max": max(nums), "cycles": [c for c, _ in values]}


def _where(value: float | None, rng: dict | None, low_word: str = "before", high_word: str = "after") -> str | None:
    if value is None or rng is None:
        return None
    return low_word if value < rng["min"] else high_word if value > rng["max"] else "within"


def _series(closes: Closes, start: date, end: date | None, base: date | None, step: int = STEP_DAYS) -> list:
    """[days from ``base`` (the halving or the peak), close / the reference close] every ``step`` days. The
    reference is the close on ``base``, or the window's first close when ``base`` has none (the 2012 cycle:
    prices from 2014-09-17) - returned as the second value; x still counts from ``base`` (658 on, not 0)."""
    days = [(d, p) for d, p in closes if d >= start and (end is None or d < end)]
    if not days:
        return [], None
    by_day = dict(days)
    anchor = base or days[0][0]
    ref_day = anchor if anchor in by_day else days[0][0]
    ref = by_day[ref_day]
    first = days[0][0]                   # the first day with a price is always a point (658 for 2012, not 665)
    pts = [[(d - anchor).days, round(p / ref, 4)] for d, p in days if d == first or (d - anchor).days % step == 0]
    if pts and pts[-1][0] != (days[-1][0] - anchor).days:          # always the latest day too
        pts.append([(days[-1][0] - anchor).days, round(days[-1][1] / ref, 4)])
    return pts, ref_day


def view(closes: Closes, today: date | None = None) -> dict:
    """The whole answer (the #514 contract) from daily closes, oldest first."""
    closes = sorted({d: p for d, p in closes if p and p > 0}.items())   # one close a day: the last row given
    if not closes:
        raise ValueError("no prices")
    last_d, last_p = closes[-1]
    starts = [h[2] for h in HALVINGS]
    past = [cycle(closes, starts[i], starts[i + 1]) for i in range(len(starts) - 1)]
    now_start = starts[-1]
    now_days = [(d, p) for d, p in closes if d >= now_start]
    current = None
    if now_days:
        hi_i = max((i for i, (d, _) in enumerate(now_days) if (d - now_start).days <= PEAK_WINDOW_DAYS),
                   key=lambda i: now_days[i][1])
        hi_d, hi_p = now_days[hi_i]
        after = now_days[hi_i + 1:]
        low = min(after, key=lambda x: x[1]) if after else None
        current = {
            "halving": now_start.isoformat(), "days_since_halving": ((today or last_d) - now_start).days,
            "price": round(last_p, 2), "high": _point(hi_d, hi_p, now_start),
            "days_since_high": ((today or last_d) - hi_d).days, "from_high_pct": _pct(last_p, hi_p),
            "low_after_high": ({**_point(low[0], low[1], now_start), "drawdown_pct": _pct(low[1], hi_p)}
                               if low else None),
            "from_low_pct": _pct(last_p, low[1]) if low else None,
        }
    r_halving = _range([(c["halving"], c["bottom"]["days_from_halving"]) for c in past if c["bottom"]])
    r_peak = _range([(c["halving"], c["bottom"]["days_from_peak"]) for c in past
                     if c["bottom"] and c["bottom"]["days_from_peak"] is not None])
    r_dd = _range([(c["halving"], c["bottom"]["drawdown_pct"]) for c in past
                   if c["bottom"] and c["bottom"]["drawdown_pct"] is not None])
    ranges = {"bottom_days_from_halving": r_halving, "bottom_days_from_peak": r_peak, "bottom_drawdown_pct": r_dd,
              "now": {"days_since_halving": _where(current and current["days_since_halving"], r_halving),
                      "days_since_high": _where(current and current["days_since_high"], r_peak),
                      # drawdowns are negative: above the range's top is shallower, below its bottom deeper
                      "from_high_pct": _where(current and current["from_high_pct"], r_dd, "deeper", "shallower")}}
    by_halving, by_peak = [], []
    for i, s in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else None
        pts, base = _series(closes, s, end, s)
        if pts:
            by_halving.append({"halving": s.isoformat(), "base_date": base.isoformat(), "points": pts})
        peak = past[i]["peak"] if i < len(past) else (current and current["high"])
        if peak:
            pd = date.fromisoformat(peak["date"])
            # from the halving: the days before the peak come out negative (the overlay is centred on it)
            pts, base = _series(closes, s, end, pd)
            by_peak.append({"halving": s.isoformat(), "base_date": base.isoformat(), "points": pts})
    notes = list(NOTES)
    if any(c["partial"] for c in past):
        notes.append(NOTE_2012)
    return {"as_of": last_d.isoformat(), "source": SOURCE,
            "halvings": [{"n": n, "block": b, "date": d.isoformat(), "reward_after": r, "estimated": False}
                         for n, b, d, r in HALVINGS] + [next_halving()],
            "cycles": past, "current": current, "ranges": ranges,
            "series": {"by_halving": by_halving, "by_peak": by_peak}, "notes": notes}


def fake_closes() -> Closes:
    """``DESK_FAKE_RUNS``: a made-up history from 2014-09-17 (four-year waves), no network."""
    import math

    start, out = date(2014, 9, 17), []
    for i in range((datetime.now(UTC).date() - start).days + 1):
        wave = math.sin((i - 300) / 1461 * 2 * math.pi)
        out.append((start + timedelta(days=i), round(400 * math.exp(i / 700) * (1 + 0.6 * wave), 2)))
    return out


def yahoo_closes() -> Closes:
    import yfinance as yf

    frame = yf.Ticker(SYMBOL).history(period="max", interval="1d", auto_adjust=False)
    frame = frame.dropna(subset=["Close"])
    return [(idx.to_pydatetime().astimezone(UTC).date() if idx.tzinfo else idx.date(), float(row["Close"]))
            for idx, row in frame.iterrows()]


class Cache:
    """The answer for a day (a failure is retried after ``FAIL_TTL_SEC``)."""

    def __init__(self, fetch: Callable[[], Closes] = yahoo_closes, clock=time.monotonic,
                 today=lambda: datetime.now(UTC).date()) -> None:
        self.fetch, self.clock, self.today = fetch, clock, today
        self._lock = threading.Lock()
        self._value: dict | None = None
        self._until = 0.0
        self.error: str | None = None

    def get(self, ahead: float = 0.0) -> dict | None:
        """``ahead`` (#536, the prewarm loop): refetch an answer that expires within that many seconds.
        A fresh answer is read without the lock, so a page never waits behind that refetch."""
        if self.clock() < self._until - ahead:
            return self._value
        with self._lock:
            if self.clock() < self._until - ahead:
                return self._value
            try:
                self._value, self.error = view(self.fetch(), self.today()), None
                self._until = self.clock() + TTL_SEC
            except Exception as exc:  # noqa: BLE001 - the page says it could not; kept the last good answer
                log.warning("halving: prices failed (%s)", type(exc).__name__)
                self.error = type(exc).__name__
                self._until = self.clock() + FAIL_TTL_SEC
            return self._value
