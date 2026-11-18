"""Nasdaq 100 futures (NQ=F) selling alarms (#579), read on 5-minute bars around the clock.

The bars are Yahoo's (desk.prices.candles "NQ=F" "5m", about a week of them); only finished bars
are judged (a bar's start plus five minutes at or before now), so a bar is judged once. Rules on the
last finished bar:

- ``nq_drop`` (급락 속도): its 5-minute return at or under the ``LOW_QUANTILE`` of the 5-minute
  returns before it, with its volume at least ``VOLUME_MIN_TIMES`` the usual - caution, strong from
  ``VOLUME_TIMES`` (selling with weight). The usual is the median volume of the same five minutes
  (New York clock) on the days before: the cash open's bar always trades many times the pre-market's.
- ``nq_drop15``: the same on the 15-minute return (three bars), when the 5-minute one did not fire.
- ``nq_from_high`` (장중 고점 대비): the close ``FROM_HIGH_PCT`` or more under the session's high
  (the CME day from 18:00 ET); once a session (desk.market_alerts).

Returns across a gap (the 17:00-18:00 ET break, a weekend, a missing bar) are left out of the samples.
CME hours: Sunday 18:00 ET to Friday 17:00 ET without the daily 17:00-18:00 break (holidays are
not modelled - Yahoo then has no new bar to judge).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from statistics import median

from .estimate import US
from .futures_flow import KST
from .session_progress import FUTURES_CLOSES, FUTURES_OPENS

TICKER = "NQ=F"
BARS = 1500                      # Yahoo 5-minute bars a pass: about five CME days (desk.prices asks ~15 days)
BAR = timedelta(minutes=5)
SETTLE_SEC = 20                  # after a bar's end, for Yahoo to have it
LOW_QUANTILE = 0.02
VOLUME_TIMES = 3.0
VOLUME_MIN_TIMES = 1.5
FROM_HIGH_PCT = 1.5
MIN_SAMPLE = 200                 # fewer 5-minute returns than this: no quantile worth reading


def cme_open(now: datetime) -> bool:
    local = now.astimezone(US.zone)
    day, clock = local.weekday(), local.time()
    if day == 5 or (day == 4 and clock >= FUTURES_CLOSES) or (day == 6 and clock < FUTURES_OPENS):
        return False
    return not (FUTURES_CLOSES <= clock < FUTURES_OPENS)


def seconds_to_next(now: datetime) -> float:
    """Until SETTLE_SEC past the next 5-minute mark."""
    step = BAR.total_seconds()
    return step - (now.timestamp() % step) + SETTLE_SEC


def _at(bar: dict) -> datetime:
    return datetime.fromisoformat(bar["at"]).astimezone(UTC)


def _quantile(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[int(q * (len(xs) - 1))]


def _returns(bars: list[dict], step: int) -> list[float]:
    out = []
    for i in range(step, len(bars)):
        a, b = bars[i - step], bars[i]
        if _at(b) - _at(a) == step * BAR and a.get("close") and b.get("close"):
            out.append(b["close"] / a["close"] - 1)
    return out


def _volume_times(done: list[dict]) -> float | None:
    """The last bar's volume over the median of the same New York five minutes on the days before."""
    last = done[-1]
    clock = _at(last).astimezone(US.zone).time()
    same = [b.get("volume") or 0 for b in done[:-1] if _at(b).astimezone(US.zone).time() == clock]
    same = [v for v in same if v > 0]
    if not same or not last.get("volume"):
        return None
    return last["volume"] / median(same)


def _session_start(at: datetime) -> datetime:
    local = at.astimezone(US.zone)
    start = datetime.combine(local.date(), FUTURES_OPENS, US.zone)
    return start if local >= start else start - timedelta(days=1)


def session_of(at: datetime):
    """The New York date the CME session holding ``at`` opened on (18:00 ET); an alert's time is its
    bar's end, so a minute back is the bar's own session."""
    return _session_start(at - timedelta(minutes=1)).date()


def _alert(rule: str, at: datetime, value: float, limit: float | None, severity: str, **extra) -> dict:
    return {"kind": "nq_alert", "rule": rule, "market": "nq", "who": "all", "severity": severity,
            "t": at.astimezone(KST).isoformat(timespec="seconds"), "value": round(value * 100, 3),
            "prev": None if limit is None else round(limit * 100, 3), **extra}


def judge(bars: list[dict], now: datetime | None = None) -> list[dict]:
    """The alarms on the last finished bar (its end is the alert's time), oldest rule first."""
    now = now or datetime.now(UTC)
    done = [b for b in bars if b.get("close") and _at(b) + BAR <= now]
    if len(done) < MIN_SAMPLE:
        return []
    last, end = done[-1], _at(done[-1]) + BAR
    out = []
    r5, r15 = _returns(done, 1), _returns(done, 3)
    fired = False
    if len(r5) > MIN_SAMPLE and _at(last) - _at(done[-2]) == BAR:
        now5, low5 = r5[-1], _quantile(r5[:-1], LOW_QUANTILE)
        if now5 <= low5 and now5 < 0:
            times = _volume_times(done)
            if times is not None and times >= VOLUME_MIN_TIMES:
                out.append(_alert("nq_drop", end, now5, low5, "strong" if times >= VOLUME_TIMES else "caution",
                                  volume_times=round(times, 1)))
                fired = True
    if not fired and len(r15) > MIN_SAMPLE and _at(last) - _at(done[-4]) == 3 * BAR:
        now15, low15 = r15[-1], _quantile(r15[:-1], LOW_QUANTILE)
        if now15 <= low15 and now15 < 0:
            out.append(_alert("nq_drop15", end, now15, low15, "caution"))
    start = _session_start(_at(last))
    session = [b for b in done if _at(b) >= start]
    high = max((b.get("high") or b["close"]) for b in session)
    off = last["close"] / high - 1
    if off * 100 <= -FROM_HIGH_PCT:
        out.append(_alert("nq_from_high", end, off, -FROM_HIGH_PCT / 100, "caution", high=high))
    return out


def text(alert: dict) -> str:
    """e.g. "🔵 나스닥 선물 급락 · 23:35 · 5분 −0.62% (하위 2% −0.31%) · 거래량 평소의 3.4배"."""
    hhmm = datetime.fromisoformat(alert["t"]).astimezone(KST).strftime("%H:%M")
    pct = (lambda v: f"{v:+.2f}%".replace("-", "−"))
    rule = alert["rule"]
    if rule == "nq_from_high":
        return f"🔵 나스닥 선물 장중 고점 대비 {pct(alert['value'])} · {hhmm}"
    span = "5분" if rule == "nq_drop" else "15분"
    head = "🔵 나스닥 선물 급락" + (" (매도세 강함)" if alert["severity"] == "strong" else "")
    parts = [head, hhmm, f"{span} {pct(alert['value'])} (하위 {LOW_QUANTILE:.0%} {pct(alert['prev'])})"]
    if alert.get("volume_times") is not None:
        parts.append(f"거래량 같은 시각 평소의 {alert['volume_times']:.1f}배")
    return " · ".join(parts)
