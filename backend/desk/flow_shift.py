"""Investor flow shifts through the session (#579): when a main position's pace changes, not only its sign.

Input is #359's snapshots (desk.futures_history.intraday): running totals for the day of the foreign,
institution and individual investors, KOSPI / KOSDAQ spot in 100 million KRW and KOSPI200 futures in
contracts. Every rule reads the latest snapshot, so the snapshot loop (and a replay of a past day,
one snapshot at a time) judges each moment once:

- pace (``WINDOW_MIN`` minutes) of the foreign and the institution (the main positions; the individual
  mostly takes the other side): ``recent`` = the last 30 minutes' change, ``prev`` = the 30 before,
  judged from ``PACE_FROM_MIN`` after the market's open - the opening's heavy trading would read
  every next half hour as a shrink (the first hour has its own rule below) - until the closing
  auction (``UNTIL``), which no rule reads.
  - ``buy_grow`` / ``sell_grow`` (매수 · 매도 확대): recent past the threshold on its side and at
    least a threshold faster than prev.
  - ``buy_shrink`` / ``sell_shrink`` (매수 · 매도 축소): prev past ``SHRINK_FROM`` thresholds, recent
    down to ``SHRINK_LEFT`` of it or less. A shrink that is also the other side's growth reads as the growth.
- ``open_reversal`` (장초 대비 반전): the first hour moved at least twice the threshold, and what came
  since runs the other way by at least the threshold and half the first hour.
- ``turn`` (순매수 · 순매도 전향) for the institution and the individual, on desk.flow_intraday.turns'
  dead band and hold (the foreign turns stay #361's desk.flow_alerts), from ``TURN_FROM_MIN`` after the open.

The threshold of a market and investor is the ``QUANTILE`` of the absolute 30-minute changes over the
last trading days (desk.futures_history keeps ``KEEP_INTRADAY_DAYS`` of them) once ``MIN_DAYS`` are
there, never under half the default; before that the default (that quantile on 2026-11-13 · 16 · 17).
Pure functions - the sending, cooldown and daily cap are desk.market_alerts.
"""

from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, time, timedelta

from . import flow_intraday
from .futures_flow import KST

WINDOW_MIN = 30
MARKETS = ("kospi", "kosdaq", "futures")
WHO = ("foreign", "institution", "individual")
PACE_WHO = ("foreign", "institution")
PACE_FROM_MIN = 90
SHRINK_FROM = 1.5
TURN_WHO = ("institution", "individual")          # the foreign turns are desk.flow_alerts (#361)
SHRINK_LEFT = 0.4
QUANTILE = 0.9
MIN_DAYS = 3
STRONG_TIMES = 2.0                                # recent at least this many thresholds: strong
OPEN_HOUR = {"kospi": time(9, 0), "kosdaq": time(9, 0), "futures": time(8, 45)}
# the closing auctions (spot 15:20-15:30, futures 15:35-15:45) and after: a move there is not one to act on
UNTIL = {"kospi": time(15, 20), "kosdaq": time(15, 20), "futures": time(15, 35)}
TURN_FROM_MIN = 30                                # a turn in the first half hour is the opening's noise
TURN_RECENT_MIN = 10                              # a turn found this recently is still news
SLACK_MIN = 3                                     # a window's far end may miss its snapshot by this much
# |30-minute change| at QUANTILE on 2026-11-13 · 16 · 17 (spot 100M KRW, futures contracts)
DEFAULT = {("kospi", "foreign"): 2030.0, ("kospi", "institution"): 980.0, ("kospi", "individual"): 1650.0,
           ("kosdaq", "foreign"): 430.0, ("kosdaq", "institution"): 200.0, ("kosdaq", "individual"): 480.0,
           ("futures", "foreign"): 550.0, ("futures", "institution"): 520.0, ("futures", "individual"): 240.0}

WHO_KO = {"foreign": "외인", "institution": "기관", "individual": "개인"}
MARKET_KO = {"kospi": "코스피 현물", "kosdaq": "코스닥 현물", "futures": "선물"}
RULE_KO = {"buy_grow": "매수 확대", "sell_grow": "매도 확대", "buy_shrink": "매수 축소",
           "sell_shrink": "매도 축소", "open_buy_reversal": "장초 매수 뒤 매도 반전",
           "open_sell_reversal": "장초 매도 뒤 매수 반전", "buy_turn": "순매수 전향", "sell_turn": "순매도 전향"}
# red up, blue down (the Korean market's colors)
BEARISH = {"sell_grow", "buy_shrink", "open_buy_reversal", "sell_turn"}


def _series(points: list[dict], market: str, who: str) -> list[tuple[datetime, float]]:
    return [(at, float(v[who])) for at, v in flow_intraday.series(points, market) if v.get(who) is not None]


def _at_or_before(s: list[tuple[datetime, float]], times: list[datetime], at: datetime) -> float | None:
    i = bisect_right(times, at) - 1
    if i < 0 or at - times[i] > timedelta(minutes=SLACK_MIN):
        return None
    return s[i][1]


def changes(points: list[dict], market: str, who: str, window: int = WINDOW_MIN) -> list[float]:
    """Every ``window``-minute change of the day (one per snapshot that has one) - the history's sample."""
    s = _series(points, market, who)
    times = [t for t, _ in s]
    out = []
    for t, v in s:
        old = _at_or_before(s, times, t - timedelta(minutes=window))
        if old is not None:
            out.append(v - old)
    return out


def thresholds(days: list[list[dict]]) -> dict[tuple[str, str], float]:
    """Per (market, investor): the QUANTILE of |30-minute change| over ``days`` (each a day's
    snapshots), or the default with fewer than MIN_DAYS days that have that market."""
    out = {}
    for key, default in DEFAULT.items():
        sample, n = [], 0
        for points in days:
            got = changes(points, *key)
            if got:
                n += 1
                sample += [abs(x) for x in got]
        if n < MIN_DAYS:
            out[key] = default
            continue
        sample.sort()
        out[key] = max(default / 2, sample[int(QUANTILE * (len(sample) - 1))])
    return out


def _alert(rule: str, market: str, who: str, at: datetime, value: float, prev: float | None,
           severity: str) -> dict:
    return {"kind": "flow_shift", "rule": rule, "market": market, "who": who, "severity": severity,
            "t": at.isoformat(timespec="seconds"), "value": round(value, 2),
            "prev": None if prev is None else round(prev, 2)}


def _opened(at: datetime, market: str, minutes: int) -> bool:
    """``minutes`` past the market's open and before its closing auction."""
    start = datetime.combine(at.date(), OPEN_HOUR[market], KST) + timedelta(minutes=minutes)
    return start <= at < datetime.combine(at.date(), UNTIL[market], KST)


def _pace(s, times, at, v, market, who, band) -> dict | None:
    if who not in PACE_WHO or not _opened(at, market, PACE_FROM_MIN):
        return None
    mid = _at_or_before(s, times, at - timedelta(minutes=WINDOW_MIN))
    far = _at_or_before(s, times, at - timedelta(minutes=2 * WINDOW_MIN))
    if mid is None or far is None:
        return None
    recent, prev = v - mid, mid - far
    strong = "strong" if abs(recent) >= STRONG_TIMES * band else "caution"
    if recent >= band and recent - prev >= band:
        return _alert("buy_grow", market, who, at, recent, prev, strong)
    if recent <= -band and recent - prev <= -band:
        return _alert("sell_grow", market, who, at, recent, prev, strong)
    if prev >= SHRINK_FROM * band and recent <= SHRINK_LEFT * prev:
        return _alert("buy_shrink", market, who, at, recent, prev, "info")
    if prev <= -SHRINK_FROM * band and recent >= SHRINK_LEFT * prev:
        return _alert("sell_shrink", market, who, at, recent, prev, "info")
    return None


def _open_reversal(s, times, at, v, market, who, band) -> dict | None:
    first = datetime.combine(at.date(), OPEN_HOUR[market], KST) + timedelta(hours=1)
    if at <= first or not _opened(at, market, 60):
        return None
    opened = _at_or_before(s, times, first)
    if opened is None or abs(opened) < 2 * band:
        return None
    since = v - opened
    if since * opened < 0 and abs(since) >= max(band, abs(opened) / 2):
        rule = "open_buy_reversal" if opened > 0 else "open_sell_reversal"
        return _alert(rule, market, who, at, since, opened, "caution")
    return None


def judge(points: list[dict], limits: dict[tuple[str, str], float]) -> list[dict]:
    """The shifts at the latest snapshot of each market and investor, and the turns found lately."""
    out = []
    for market in MARKETS:
        for who in WHO:
            s = _series(points, market, who)
            if not s:
                continue
            times = [t for t, _ in s]
            at, v = s[-1]
            band = limits.get((market, who), DEFAULT[(market, who)])
            for rule in (_pace, _open_reversal):
                got = rule(s, times, at, v, market, who, band)
                if got:
                    out.append(got)
            if who in TURN_WHO:
                for turn in flow_intraday.turns(points, market, who):
                    t = datetime.fromisoformat(turn["t"])
                    if at - t <= timedelta(minutes=TURN_RECENT_MIN) and _opened(t, market, TURN_FROM_MIN):
                        out.append(_alert(turn["kind"], market, who, t, turn["value"], None, "caution"))
    return out


def text(alert: dict) -> str:
    """e.g. "🔵 기관 코스피 현물 매도 확대 · 10:42 · 최근 30분 −1,250억 (직전 30분 −310억)"."""
    rule, market = alert["rule"], alert["market"]
    unit = "계약" if market == "futures" else "억"
    dot = "🔵" if rule in BEARISH else "🔴"
    head = f"{dot} {WHO_KO[alert['who']]} {MARKET_KO[market]} {RULE_KO[rule]}"
    hhmm = datetime.fromisoformat(alert["t"]).astimezone(KST).strftime("%H:%M")
    if rule in ("buy_turn", "sell_turn"):
        tail = f"누적 {_num(alert['value'])}{unit}"
    elif rule.startswith("open_"):
        tail = f"장초 1시간 {_num(alert['prev'])}{unit} → 이후 {_num(alert['value'])}{unit}"
    else:
        tail = f"최근 {WINDOW_MIN}분 {_num(alert['value'])}{unit} (직전 {WINDOW_MIN}분 {_num(alert['prev'])}{unit})"
    return f"{head} · {hhmm} · {tail}"


def _num(v: float | None) -> str:
    return "?" if v is None else f"{v:+,.0f}".replace("-", "−")
