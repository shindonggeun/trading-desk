"""Today's direction again during the session (#619): at set times until the regular close, "from now to
the close" in five stages, scored per round.

- The rounds sit on the ends of Yahoo's 60-minute index bars, so a past session can be called exactly as
  the live one is: KRX bars start at 09:00 and the last one ends at 15:00 (Yahoo has no 15:00-15:30 bar),
  NYSE bars start at 09:30 and end on the half hour. KR rounds are 10:00 to 15:00 KST, US rounds 10:30 to
  15:30 ET (desk.market_calendar's zones: New York's daylight saving moves them in Korean time, not in
  New York's). A round at or after the session's close is not made (a US half day ends at 13:00); a
  KRX session that opens at 10:00 (the year's first, the CSAT day) has no rounds at all: the morning
  features read the open by 09:30 (desk.day_call.kr_late_open), and the training left those days out.
- Yahoo's KRX bars come about 20 minutes late (desk.live_bars.YAHOO_LAG), the CME futures 10: a live KR
  round waits READY after its time before it reads the bar that ended at it (desk.day_calls.windows).
- The answer is the session's close against the last hourly close by the round (``anchor``), in the
  ATR of the days before scaled to the time left: ``z = (close - anchor) / (ATR * sqrt(left))``, where
  ``left`` is the share of the regular session still to run. A round near the close has little room
  to move, so fixed ATR edges would call almost every late round "약보합" and look easy (#619).
- Features: the day call's own daily ones as of its second round (desk.day_call.features: what the
  morning knew, the open gap included), and the session so far read from the hourly bars finished by the
  round - the move from the open, the last hour, the range and where the price sits in it, the time
  left, whether the open gap is still unfilled (#577) and, for Korea, the Nasdaq futures since the KRX
  open (#568). Nothing after the round's time is read.
- The intraday investor flows (#579) have no history on this side (the server keeps them since #359),
  so they are not in the model.
- A call is "판단 보류" when its long and short sides are within desk.day_call.HOLD_MARGIN, and the whole
  model is "참고용 · 통계적 우위 없음" (``reference_only``) unless its hold-out beat every baseline
  (``verdict_of``); such a model's rounds are not pushed (desk.day_calls.push_of).
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from pathlib import Path

from . import day_call as dc
from . import market_calendar

CALL = "to_close"
CALL_KO = "지금부터 마감까지"
NO_EDGE_KO = "참고용 · 통계적 우위 없음"
EDGE_POINTS = 0.05          # the hold-out direction hit must beat the best baseline by this (and Brier skill > 0)
DATA = dc.DATA
# round -> local time (the end of an hourly index bar); numbered after the day call's 1차 and 2차
ROUNDS = {
    "kr": {3: time(10, 0), 4: time(11, 0), 5: time(12, 0), 6: time(13, 0), 7: time(14, 0), 8: time(15, 0)},
    "us": {3: time(10, 30), 4: time(11, 30), 5: time(12, 30), 6: time(13, 30), 7: time(14, 30), 8: time(15, 30)},
}
HOURLY = {"kr": "^KS11@60m", "us": "^GSPC@60m"}     # the target's hourly bars in a series dict
# how long after a round its last bar is complete on Yahoo (KRX ~20 min late, the NQ futures 10, ^GSPC 0)
READY = {"kr": timedelta(minutes=25), "us": timedelta(minutes=1)}
NQ = "NQ=F@60m"
# the day call's daily features kept (decided before the hold-out was looked at: the trend and stretch of
# the days before, how wild they were, VIX, the open gap, the expiry day)
DAILY = ("t_ret1", "t_ret5", "t_ma20", "t_rv", "t_atrp", "vix_lvl", "vix_chg", "gap", "expiry")
FEATURES = {
    "i_move": ("시가 이후 흐름", "atr"), "i_last": ("최근 1시간", "atr"), "i_range": ("오늘 고저 폭", "atr"),
    "i_pos": ("오늘 범위 안 위치", "pos"), "i_left": ("남은 시간", "left"), "gap_open": ("메우지 않은 시가 갭", "gap"),
    "nq_day": ("장중 나스닥 선물", "atr"),
}


def round_time(market: str, round_: int) -> time:
    return ROUNDS[market][round_]


def round_label(market: str, round_: int) -> str:
    """"3차 10:00": the number after the day call's two, and the local time."""
    return f"{round_}차 {round_time(market, round_):%H:%M}"


def cutoff_of(market: str, day: date, round_: int) -> datetime:
    return datetime.combine(day, round_time(market, round_), dc.MARKETS[market].zone)


def bounds(market: str, day: date) -> tuple[datetime, datetime]:
    """The regular session's open and close (a US half day closes at 13:00, the CSAT day at 16:30)."""
    return market_calendar.session_bounds(dc.MARKETS[market].calendar, day)


def rounds_of(market: str, day: date) -> list[int]:
    """The rounds of ``day``'s session: none on a closed day, none at or after the close."""
    if not market_calendar.is_open(dc.MARKETS[market].calendar, day):
        return []
    if market == "kr" and dc.kr_late_open(day):
        return []
    opens, closes = bounds(market, day)
    return [r for r in sorted(ROUNDS[market]) if opens < cutoff_of(market, day, r) < closes]


def left(market: str, day: date, round_: int) -> float:
    """The share of the regular session still to run at the round."""
    opens, closes = bounds(market, day)
    return (closes - cutoff_of(market, day, round_)) / (closes - opens)


def _bar_end(market: str, bar) -> datetime:
    start = datetime.fromisoformat(bar.at)
    day = start.astimezone(dc.MARKETS[market].zone).date()
    return min(start + timedelta(hours=1), bounds(market, day)[1])


_DAYS: dict[tuple, tuple[Sequence, dict[date, list]]] = {}


def _days(market: str, bars: Sequence) -> dict[date, list]:
    """The hourly bars by local session day, kept per series (the backtest asks thousands of times; the
    entry holds the list itself so its id is not reused while cached)."""
    key = (market, id(bars), len(bars))
    got = _DAYS.get(key)
    if got is None or got[0] is not bars:
        if len(_DAYS) > 8:
            _DAYS.clear()
        zone = dc.MARKETS[market].zone
        by: dict[date, list] = {}
        for b in bars:
            if b.close:
                by.setdefault(datetime.fromisoformat(b.at).astimezone(zone).date(), []).append(b)
        got = _DAYS[key] = (bars, by)
    return got[1]


def session_bars(market: str, day: date, bars: Sequence, cutoff: datetime) -> list:
    """``day``'s hourly index bars finished by ``cutoff`` (oldest first)."""
    opens = bounds(market, day)[0]
    return [b for b in _days(market, bars).get(day, [])
            if datetime.fromisoformat(b.at) >= opens and _bar_end(market, b) <= cutoff]


def features(market: str, round_: int, day: date, series: dict[str, Sequence],
             base: tuple[dict[str, float], dict] | None = None) -> tuple[dict[str, float], dict]:
    """The round's features (only bars finished by its time) and what the call needs besides: the
    anchor (the last hourly close), the ATR and the time left. ``base`` is the day call's round-2
    features of the day when already read (the backtest reads them once a day)."""
    if round_ not in rounds_of(market, day):
        raise dc.NoData(f"no round {round_} on {day}")
    x0, meta = base or dc.features(market, 2, day, series)
    cutoff = cutoff_of(market, day, round_)
    today = session_bars(market, day, series.get(HOURLY[market]) or [], cutoff)
    if not today:
        raise dc.NoData(f"{HOURLY[market]}: no bar of {day} by {cutoff}")
    if datetime.fromisoformat(today[0].at) != bounds(market, day)[0] or not today[0].open:
        raise dc.NoData(f"{HOURLY[market]}: the opening bar of {day} is missing")
    a = meta["atr"]
    prev_close = meta["last_close"]
    anchor = today[-1].close
    opened = today[0].open
    high = max(b.high for b in today)
    low = min(b.low for b in today)
    before = today[-2].close if len(today) > 1 else opened
    gap = opened - prev_close
    unfilled = (gap > 0 and low > prev_close) or (gap < 0 and high < prev_close)
    share = left(market, day, round_)
    x = {k: x0[k] for k in DAILY}
    x.update({
        "i_move": (anchor - opened) / a,
        "i_last": (anchor - before) / a,
        "i_range": (high - low) / a,
        "i_pos": ((anchor - low) / (high - low) - 0.5) if high > low else 0.0,
        "i_left": share,
        "gap_open": (1.0 if gap > 0 else -1.0) if unfilled else 0.0,
    })
    if market == "kr":
        atrp = meta["atr_pct"] / 100
        x["nq_day"] = dc._night(series, NQ, bounds(market, day)[0], cutoff) / atrp
    return x, {"anchor": anchor, "atr": a, "atr_pct": meta["atr_pct"], "left": share, "cutoff": cutoff,
               "open": opened}


def label(market: str, day: date, round_: int, series: dict[str, Sequence], anchor: float,
          now: datetime | None = None) -> dict | None:
    """The round's answer: the close against ``anchor`` in the ATR scaled to the time left (desk.day_call
    .score reads it as any other answer). None while the session is not over at ``now``."""
    ans = dc.label(market, day, series, "oc", now)
    if ans is None or round_ not in rounds_of(market, day) or not anchor:
        return None
    z = (ans["close"] - anchor) / (ans["atr"] * math.sqrt(left(market, day, round_)))
    return {"open": anchor, "close": ans["close"], "atr": ans["atr"], "z": z, "stage": dc.stage_of(z),
            "return_pct": (ans["close"] / anchor - 1) * 100}


def _value_text(name: str, value: float) -> str:
    if name in dc.FEATURES:
        return dc._value_text(name, value)
    kind = FEATURES[name][1]
    if kind == "pos":
        return f"{(value + 0.5) * 100:.0f}%(0 저가 · 100 고가)"
    if kind == "left":
        return f"{value * 100:.0f}%"
    if kind == "gap":
        return "위 갭 남음" if value > 0 else "아래 갭 남음" if value < 0 else "없음 · 메움"
    return f"{value:+.2f} ATR"


def _label_ko(name: str) -> str:
    return (FEATURES.get(name) or dc.FEATURES[name])[0]


def reasons(model: dict, x: dict[str, float], n: int = 5) -> list[dict]:
    """desk.day_call.reasons with this module's feature names."""
    word = {"long": "오르는 쪽", "short": "내리는 쪽", "volatile": "크게 움직이는 쪽", "calm": "조용한 쪽"}
    out = []
    for name, mu, sd, b, g in zip(model["features"], model["mean"], model["std"], model["beta"], model["gamma"],
                                  strict=True):
        v = (x[name] - mu) / sd
        loc, scale = b * v, g * v
        if abs(loc) >= abs(scale):
            effect, weight = ("long" if loc > 0 else "short"), abs(loc)
        else:
            effect, weight = ("volatile" if scale > 0 else "calm"), abs(scale)
        text = _value_text(name, x[name])
        out.append({"key": name, "label": _label_ko(name), "value": text, "effect": effect,
                    "weight": round(weight, 3), "text": f"{_label_ko(name)} {text} → {word[effect]}"})
    return sorted(out, key=lambda r: -r["weight"])[:n]


def load_model(market: str, root: Path = DATA) -> dict | None:
    """The committed round model of ``market`` (rounds_<market>.json), None when missing or unreadable."""
    try:
        return json.loads((root / f"rounds_{market}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_report(market: str, root: Path = DATA) -> dict | None:
    try:
        return json.loads((root / f"rounds_report_{market}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def call(model_file: dict, market: str, round_: int, day: date, series: dict[str, Sequence]) -> dict:
    """One round's call, shaped as desk.day_call.call; ``features`` also keeps the anchor and the time
    left the scoring reads back."""
    model = model_file["model"]
    x, meta = features(market, round_, day, series)
    probs = dc.percent(dc.raw_probs(model, x))
    stage = dc.top_stage(probs)
    flat, strong = model["edges"]
    room = meta["atr_pct"] * math.sqrt(meta["left"])
    return {"market": market, "date": day.isoformat(), "call": CALL, "call_label": CALL_KO,
            "measured_from": "round", "round": round_, "round_label": round_label(market, round_),
            "cutoff_at": meta["cutoff"].isoformat(), "stage": stage, "label": dc.LABEL_KO[stage],
            "direction": dc.direction(probs), "verdict": dc.verdict(probs, dc.HOLD_MARGIN),
            "reference_only": model_file.get("verdict") != "edge",
            "reference_label": None if model_file.get("verdict") == "edge" else NO_EDGE_KO,
            "probs": {s: probs[s] for s in dc.DISPLAY_ORDER},
            "edges_pct": {"flat": round(flat * room, 2), "strong": round(strong * room, 2)},
            "reasons": reasons(model, x), "model_version": model_file["version"], "fallback": False,
            "features": {**x, "anchor": meta["anchor"], "left": meta["left"]}}


def verdict_of(holdout: dict) -> str:
    """"edge" when the hold-out direction hit beats always long, always short and following the session
    so far by EDGE_POINTS and the Brier skill is above zero; "no_edge" otherwise (or too few rows)."""
    b = holdout.get("baselines") or {}
    rates = [(b.get(k) or {}).get("direction_hit_rate") for k in ("always_long", "always_short", "follow_trend")]
    best = max((r for r in rates if r is not None), default=None)
    hit = holdout.get("direction_hit_rate")
    if hit is None or best is None or (holdout.get("brier_skill") or 0) <= 0:
        return "no_edge"
    return "edge" if round(hit - best, 3) >= EDGE_POINTS else "no_edge"     # the rates are rounded to 3 places


STAGE_INDEX = {s: i for i, s in enumerate(dc.STAGES)}


def jump(before: str, after: str) -> int:
    """How many stages apart two calls are (strong_short 0 ... strong_long 4)."""
    return abs(STAGE_INDEX[after] - STAGE_INDEX[before])
