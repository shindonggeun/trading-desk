"""Do the intraday investor flows and the futures basis lead the index? (#645) - measured, not assumed.

Price-made indicators (RSI, MACD, volume) are lagging by construction; the investors' running net
buying (KIS' intraday provisional figures) and the basis may move with the price or a little ahead.
This module measures it on the snapshots desk.futures_history keeps every minute of the session
(``futures_snapshots``: the front month's price, basis and investors' net contracts, and the KOSPI
spot investors' net in 100 million KRW - #359), 60 days of them on the server.

For each day, a grid every BUCKET minutes over the regular session (09:00 to 15:20 KST, the closing
auction left out) takes each value **as of** the grid time - the last snapshot at or before it, as old
as STALE_SEC at most - so nothing is read before it was stored. At grid point k:

- ``signal`` = the indicator's change over the bucket just ended (x[k] - x[k-1]); the basis likewise;
- ``fwd`` = the target's log return over the next bucket (k -> k+1): **lead**;
- ``now`` = the target's return over the same bucket (k-1 -> k): **sync** (moves together);
- ``prior`` = the target's return over the bucket before (k-2 -> k-1): **lag** (flow following price).

Targets: the front month (``price``) and KOSPI200 (``price - market_basis``). The baseline is price
momentum (the target's own last bucket return) - an indicator worth watching for direction has to call
the next bucket's direction better than it. Correlations are taken per day and averaged; their spread
across days gives the standard error (a day's minutes are not independent, its days roughly are).

Verdict per indicator, target and bucket (MIN_DAYS days at least, else **표본 부족**): **선행** when the
signal's correlation with the next bucket's return **net of the price's own last two moves** (the residual
of fwd on now and prior) is at least T_MIN standard errors from 0 - a flow that only moves with the price
would otherwise look leading whenever returns mean-revert; else **동행** or **후행**, the stronger of the
same-bucket and the bucket-before correlations past T_MIN (with autocorrelated returns each leaks into the
other); else **관계 없음**. The basis against KOSPI200 (read as price - basis) is **정의상 연동**, not judged.
Hit rates (the lead correlation's way, and momentum's better way) are reported on the same buckets, not
used in the verdict. Arithmetic on stored history, not a forecast.

``cadence`` measures how often each stored value actually changes between snapshots (the source's real
update interval, at the snapshots' one-minute resolution) - the delay the screens state.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from statistics import median

import numpy as np

from . import market_calendar

KST = market_calendar.KST
SESSION = (time(9, 0), time(15, 20))     # regular trading, the closing auction (15:20-15:30) left out
BUCKETS = (5, 15)                        # minutes
STALE_SEC = 150                          # a grid value older than this is missing (a gap in the snapshots)
MIN_DAYS = 20                            # a day-mean t on fewer days calls too much by chance
T_MIN = 3.0                              # |t| for a verdict: 7 indicators x 2 targets x 2 buckets x 3 tests (~84) at 2.0 would call several by chance

# indicator -> the snapshot column it reads (desk.db.futures_snapshots); "basis" is market_basis
INDICATORS = {
    "foreign_futures": "foreign_contracts",
    "institution_futures": "institution_contracts",
    "individual_futures": "individual_contracts",
    "foreign_spot": "kospi_foreign_100m",
    "institution_spot": "kospi_institution_100m",
    "individual_spot": "kospi_individual_100m",
    "basis": "market_basis",
}
INDICATOR_KO = {
    "foreign_futures": "외국인 선물 순매수", "institution_futures": "기관 선물 순매수",
    "individual_futures": "개인 선물 순매수", "foreign_spot": "외국인 코스피 현물 순매수",
    "institution_spot": "기관 코스피 현물 순매수", "individual_spot": "개인 코스피 현물 순매수",
    "basis": "선물 베이시스", "momentum": "가격 모멘텀(기준선)",
}
TARGETS = ("futures", "kospi200")
# KOSPI200 is read as price - basis, so the basis is inside its own target: a link by definition, not a finding
TIED = {("basis", "kospi200")}
TARGET_KO = {"futures": "코스피200 선물", "kospi200": "코스피200 지수"}


def _at(row: dict) -> datetime:
    at = row["at"]
    if isinstance(at, str):
        at = datetime.fromisoformat(at)
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def _target(row: dict, target: str) -> float | None:
    price = row.get("price")
    if not price:
        return None
    if target == "futures":
        return float(price)
    basis = row.get("market_basis")
    return float(price) - float(basis) if basis is not None else None


def grid(rows: Sequence[dict], day: date, bucket: int) -> list[tuple[datetime, dict | None]]:
    """The day's grid times every ``bucket`` minutes over SESSION with the snapshot as of each (None
    when the last one before it is older than STALE_SEC). ``rows`` oldest first."""
    times = [_at(r) for r in rows]
    start = datetime.combine(day, market_calendar.hours("kr", day)[0], KST)       # 10:00 on a late open
    end = datetime.combine(day, SESSION[1], KST)
    out, i, t = [], -1, start
    while t <= end:
        while i + 1 < len(rows) and times[i + 1] <= t:
            i += 1
        ok = i >= 0 and (t - times[i]).total_seconds() <= STALE_SEC
        out.append((t, rows[i] if ok else None))
        t += timedelta(minutes=bucket)
    return out


def _log_ret(a: float | None, b: float | None) -> float | None:
    return math.log(b / a) if a and b and a > 0 and b > 0 else None


def samples(rows: Sequence[dict], day: date, bucket: int, indicator: str, target: str) -> list[tuple]:
    """(signal, fwd, now, prior, momentum) per grid point that has all of them."""
    g = grid(rows, day, bucket)
    col = INDICATORS.get(indicator)
    out = []
    for k in range(2, len(g) - 1):
        rows_k = [g[j][1] for j in (k - 2, k - 1, k, k + 1)]
        if any(r is None for r in rows_k):
            continue
        p = [_target(r, target) for r in rows_k]
        fwd, now, prior = _log_ret(p[2], p[3]), _log_ret(p[1], p[2]), _log_ret(p[0], p[1])
        if None in (fwd, now, prior):
            continue
        if indicator == "momentum":
            signal = now
        else:
            a, b = rows_k[1].get(col), rows_k[2].get(col)
            if a is None or b is None:
                continue
            signal = float(b) - float(a)
        out.append((signal, fwd, now, prior, now))
    return out


def _corr(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    if n < 5:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / (sx * sy)


def _hits(pairs: Iterable[tuple[float, float]]) -> tuple[int, int]:
    n = hit = 0
    for s, f in pairs:
        if s == 0 or f == 0:
            continue
        n += 1
        hit += (s > 0) == (f > 0)
    return hit, n


def _mean_se(vals: list[float]) -> tuple[float | None, float | None]:
    if not vals:
        return None, None
    m = sum(vals) / len(vals)
    if len(vals) < 2:
        return m, None
    sd = math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1))
    return m, sd / math.sqrt(len(vals))


def _t(m: float | None, se: float | None) -> float | None:
    # an indicator against itself (momentum's sync) is 1 every day: its spread is float noise, not a t
    return None if m is None or not se or se < 1e-9 * max(1.0, abs(m)) else m / se


def _partial(sig: Sequence[float], fwd: Sequence[float], moves: Sequence[Sequence[float]]) -> float | None:
    """The correlation of ``sig`` with what of ``fwd`` the price's own recent moves do not explain (fwd's
    least-squares residual on ``moves``: the last bucket's and the one before's return): information about
    the next bucket beyond the price itself."""
    n = len(fwd)
    if n < 5:
        return None
    x = np.column_stack([np.ones(n), *[np.asarray(m, dtype=float) for m in moves]])
    y = np.asarray(fwd, dtype=float)
    coef, *_ = np.linalg.lstsq(x, y, rcond=None)
    return _corr(sig, list(y - x @ coef))


def measure(days: dict[date, Sequence[dict]], indicator: str, target: str, bucket: int) -> dict:
    """One indicator against one target at one bucket, over ``days`` (date -> that day's snapshots)."""
    per_day = {"lead": [], "sync": [], "lag": [], "lead_net": []}
    hits = [0, 0]
    base = [0, 0]
    n = 0
    for day, rows in sorted(days.items()):
        got = samples(rows, day, bucket, indicator, target)
        if not got:
            continue
        n += len(got)
        sig = [s[0] for s in got]
        for key, idx in (("lead", 1), ("sync", 2), ("lag", 3)):
            c = _corr(sig, [s[idx] for s in got])
            if c is not None:
                per_day[key].append(c)
        c = _partial(sig, [s[1] for s in got], ([s[2] for s in got], [s[3] for s in got]))
        if c is not None:
            per_day["lead_net"].append(c)
        # both hit rates on the same buckets: the indicator moved, and so did the target next
        same = [s for s in got if s[0] != 0 and s[1] != 0 and s[4] != 0]
        h, m = _hits((s[0], s[1]) for s in same)
        hits[0] += h
        hits[1] += m
        h, m = _hits((s[4], s[1]) for s in same)
        base[0] += h
        base[1] += m
    out = {"indicator": indicator, "target": target, "bucket_min": bucket, "samples": n,
           "days": max(len(v) for v in per_day.values())}
    for key, vals in per_day.items():
        m, se = _mean_se(vals)
        out[key] = {"corr": None if m is None else round(m, 3), "se": None if se is None else round(se, 3),
                    "t": None if _t(m, se) is None else round(_t(m, se), 2)}
    # the direction call reads the sign the lead correlation has (a contrarian signal calls the other
    # way); momentum's baseline likewise takes its better way (continuation or reversal)
    lead = out["lead"]["corr"]
    raw = hits[0] / hits[1] if hits[1] else None
    out["hit_rate"] = None if raw is None else round(raw if (lead or 0) >= 0 else 1 - raw, 3)
    out["hit_side"] = "same" if (lead or 0) >= 0 else "opposite"
    out["hit_n"] = hits[1]
    out["momentum_hit_rate"] = round(max(base[0], base[1] - base[0]) / base[1], 3) if base[1] else None
    out["verdict"] = verdict(out)
    return out


def verdict(r: dict) -> str:
    if r["indicator"] == "momentum":
        return "기준선"
    if (r["indicator"], r["target"]) in TIED:
        return "정의상 연동"
    if r["days"] < MIN_DAYS:
        return "표본 부족"

    def strong(key):
        t = r[key]["t"]
        return t is not None and abs(t) >= T_MIN

    # leading means telling the next bucket beyond what the price's own last move tells: a flow that only
    # moves with the price would otherwise look "leading" whenever returns mean-revert (#645 review)
    if strong("lead_net"):
        return "선행"
    # with autocorrelated returns a lagging flow also correlates with the same bucket (and the other way
    # round): the stronger of the two says which it is
    both = [(abs(r[k]["t"]), name) for k, name in (("sync", "동행"), ("lag", "후행")) if strong(k)]
    return max(both)[1] if both else "관계 없음"


def by_day(rows: Iterable[dict]) -> dict[date, list[dict]]:
    out: dict[date, list[dict]] = {}
    for r in rows:
        out.setdefault(date.fromisoformat(r["trade_date"]), []).append(r)
    for v in out.values():
        v.sort(key=_at)
    return out


def table(days: dict[date, Sequence[dict]], buckets: Sequence[int] = BUCKETS) -> list[dict]:
    return [measure(days, ind, tgt, b) for b in buckets for tgt in TARGETS for ind in (*INDICATORS, "momentum")]


def cadence(days: dict[date, Sequence[dict]]) -> dict[str, dict]:
    """How often each stored value changes inside the regular session: the median seconds between two
    changes (the source's update interval at the snapshots' resolution) and the snapshots' own spacing."""
    out = {}
    spacing: list[float] = []
    for col in ("price", *INDICATORS.values()):
        gaps: list[float] = []
        for day, rows in days.items():
            start = datetime.combine(day, SESSION[0], KST)
            end = datetime.combine(day, SESSION[1], KST)
            inside = [r for r in rows if start <= _at(r) <= end and r.get(col) is not None]
            last_change = None
            for a, b in pairwise(inside):
                if col == "price":
                    spacing.append((_at(b) - _at(a)).total_seconds())
                if b[col] != a[col]:
                    if last_change is not None:
                        gaps.append((_at(b) - last_change).total_seconds())
                    last_change = _at(b)
        out[col] = {"changes": len(gaps), "median_update_sec": round(median(gaps)) if gaps else None}
    out["_snapshot_spacing_sec"] = {"median": round(median(spacing)) if spacing else None}
    return out


MIN_ROWS_A_DAY = 30      # a day thinned to its last snapshot (older than KEEP_INTRADAY_DAYS) is no day here


def report(rows: Iterable[dict]) -> dict:
    days = {d: r for d, r in by_day(rows).items() if len(r) >= MIN_ROWS_A_DAY}
    return {"days": len(days), "from": min(days).isoformat() if days else None,
            "to": max(days).isoformat() if days else None, "session": [t.isoformat("minutes") for t in SESSION],
            "t_min": T_MIN, "min_days": MIN_DAYS, "cadence": cadence(days), "rows": table(days)}
