"""Today's spot and futures investor flows as minute bars, with the foreign buy/sell turns (#359).

desk.futures_history stores a snapshot every few minutes of the session: the futures investors'
net contracts and the KOSPI / KOSDAQ spot investors' net value, both running totals for the day.
Here the snapshots are grouped into bars of ``bucket`` minutes from the 08:45 open: each bar ends at
its last snapshot's running total (``foreign`` ...) and carries what the bar added (``foreign_delta``,
from the previous bar's close; the first bar's delta is its own total).

A turn is where the foreign running total crosses zero for good: it has to clear a dead band
(TURN_BAND per market) on the other side and stay there for TURN_HOLD snapshots, so a total
wobbling around zero does not flip back and forth. Turns are judged on the raw snapshots, not the
bars, so every bucket shows the same turns. Bars sit on the 08:45 derivatives grid; spot trades
from 09:00, so its first 60-minute bar covers 45 minutes.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from .futures_flow import KST

BUCKETS = (1, 3, 5, 10, 15, 30, 60)
OPEN = time(8, 45)
TURN_BAND = {"futures": 500.0, "kospi": 300.0, "kosdaq": 300.0}   # contracts / 100 million KRW
TURN_HOLD = 2
CLASSES = ("foreign", "institution", "individual")


def _at(point: dict) -> datetime:
    return datetime.fromisoformat(point["at"]).astimezone(KST)


def _bucket_end(at: datetime, minutes: int) -> datetime:
    open_at = datetime.combine(at.date(), OPEN, KST)
    if at <= open_at:
        return open_at
    step = timedelta(minutes=minutes)
    n = -(-(at - open_at) // step)          # ceil: a snapshot at 09:00 closes the 08:45-09:00 bar
    return open_at + n * step


def series(points: list[dict], market: str) -> list[tuple[datetime, dict]]:
    """(time, {class: running total}) per snapshot for one market, skipping empty snapshots."""
    out = []
    for p in points:
        if market == "futures":
            vals = {c: (p.get("investors") or {}).get(c, {}).get("net_contracts") for c in CLASSES}
            vals["price"] = p.get("price")
        else:
            vals = dict((p.get("spot") or {}).get(market) or {})
        if any(vals.get(c) is not None for c in CLASSES):
            out.append((_at(p), vals))
    return out


def bars(points: list[dict], market: str, minutes: int) -> list[dict]:
    """One bar per ``minutes`` that has a snapshot, oldest first."""
    last: dict[datetime, dict] = {}
    for at, vals in series(points, market):
        last[_bucket_end(at, minutes)] = vals         # points are oldest first: the last one wins
    out, prev = [], {}
    total_key = (lambda c: f"{c}_contracts") if market == "futures" else (lambda c: c)
    for end in sorted(last):
        vals = last[end]
        bar = {"t": end.isoformat(timespec="seconds")}
        for c in CLASSES:
            v = vals.get(c)
            bar[total_key(c)] = v
            bar[f"{c}_delta"] = None if v is None else round(v - prev.get(c, 0.0), 2)
        if market == "futures":
            bar["price"] = vals.get("price")
        out.append(bar)
        prev = {**prev, **{c: v for c, v in vals.items() if v is not None}}   # a gap keeps the last value
    return out


def turns(points: list[dict], market: str, who: str = "foreign") -> list[dict]:
    """The foreign buy/sell turns of one market, oldest first."""
    band, out = TURN_BAND[market], []
    side, pending, run = 0, 0, 0            # side: +1 net buying, -1 net selling, 0 not set yet
    for at, vals in series(points, market):
        v = vals.get(who)
        if v is None:
            continue
        now = 1 if v >= band else -1 if v <= -band else 0
        if now == 0 or now == side:
            pending, run = 0, 0
            continue
        run = run + 1 if now == pending else 1
        pending = now
        if run >= TURN_HOLD:
            if side != 0:                    # the first side of the day is a start, not a turn
                out.append({"t": at.isoformat(timespec="seconds"), "market": market, "who": who,
                            "kind": "buy_turn" if now > 0 else "sell_turn", "value": v})
            side, pending, run = now, 0, 0
    return out


def view(points: list[dict], minutes: int) -> dict:
    """The API body (without the date / support fields): spot and futures bars, and the turns."""
    if minutes not in BUCKETS:
        raise ValueError(f"bucket must be one of {BUCKETS}")
    all_turns = [t for m in ("futures", "kospi", "kosdaq") for t in turns(points, m)]
    return {"bucket_min": minutes,
            "spot": {m: bars(points, m, minutes) for m in ("kospi", "kosdaq")},
            "futures": bars(points, "futures", minutes),
            "turns": sorted(all_turns, key=lambda t: t["t"])}
