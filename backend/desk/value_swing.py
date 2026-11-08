"""The oversold swing buy (#167, ``value_swing``): a long a falling market still earns.

A dip catch (desk.playbook) bets on a bounce of days. This setup looks for the rarer price where
several independent kinds of evidence agree that the fall has gone far enough - and buys it over
days, sells it over weeks (2-8). It is the one sanctioned exception to desk.direction's "fewer
longs in a downtrend" (#166): only when the evidence is there.

Evidence comes in five bundles, each computed by the desk (the model only interprets):

- oversold   daily RSI, weekly RSI, a bullish RSI divergence, a close back inside the lower
             Bollinger band, the distance below the 20-day average, the down streak
- depth      the 52-week drawdown against the ticker's own drawdown history (a percentile - a 3x
             ETF is measured against its own deep falls), the distance below the 200-day average
- base       a weekly support or a past crash low near the price, a capitulation volume day and
             the volume drying up after it, a new low taken back
- fear       desk.risk_regime's selling score (#146) in selling / exhausting, or VIX >= 30
- flows      flows / news that the bad news is priced in - not computed yet (None, never met)

A bundle with no data is None (unknown), not False: a missing index or a short history still
lets the rest be counted. The setup needs the oversold and depth bundles and three bundles in
all; a **conviction** trade needs four, and a positive expected R on enough of the desk's own
record (desk.calibration) - until the record exists conviction stays off.

Pure functions: daily and weekly bars in, dicts out.
"""

from __future__ import annotations

import logging
import math
from statistics import mean

from .indicators import rsi

log = logging.getLogger(__name__)

ENABLED = True               # set by the basket backtest (#167 done-when): off unless it pays after costs
# Swing basket 2019-, costs in, train / held-out (cut 2024-05-17), value_swing trades only:
#   3 bundles -0.140R (297) / -0.239R (94); fear required -0.010R (169) / +0.218R (31);
#   4 bundles +0.038R (141) / +0.391R (26, T1 69%) - chosen on the training half; RSI 25 or a 95th
#   percentile depth did not help. The held-out sample is small: watch the live record (desk.calibration).

# thresholds (the backtest sweeps them, #144)
RSI_DAILY = 30.0
RSI_WEEKLY = 35.0
MA20_ATR = -2.5              # the close this far under the 20-day average, in daily ATR
STREAK = 4
BB_N, BB_K = 20, 2.0
DEPTH_PCTL = 0.85            # the 52-week drawdown deeper than this share of the ticker's history
MA200_GAP = -0.10            # the close this far under the 200-day average
NEAR_ATR = 1.5               # a weekly support / old crash low this close to the recent low
CLIMAX_X = 2.0               # a capitulation day: volume this many times its 20-day average
VIX_FEAR = 30.0
MIN_GROUPS = 4               # bundles for the setup, the REQUIRED ones among them (swept, see below)
REQUIRED = ("oversold", "depth")
CONVICTION_GROUPS = 4        # bundles for a conviction trade
CONVICTION_MIN_N = 30        # desk.calibration record needed before a conviction trade

# the plan
SHARES = (0.3, 0.3, 0.4)     # two limits over days, the last on a confirmed reclaim
STEP_ATR = 1.0               # the second limit this far under the first
STOP_ATR = 0.5               # under the structure low
TIME_STOP_BARS = 25          # sessions after the last fill to reach T1 (about five weeks)
ACCOUNT_RISK_PCT = {"normal": 0.5, "conviction": 1.0}   # the most one trade may lose, % of the account
MAX_POSITION_PCT = 30.0      # never more of the account in one name, whatever the stop allows
# The plan's distances (second limit, stop, reclaim) use the ATR capped the way desk.playbook caps it
# (#151 _calm_atr: a crash day swells the 14-day ATR - SOXL 2026-11-08 had 13.6 on a 46 price, which
# put the stop 43% under the average). The evidence keeps the raw ATR its thresholds were swept with.
# Swing basket 2019-, costs in, gated (train / held-out cut 2024-05-17), the oversold swing buy alone:
#   raw ATR, no floor     192 settled  +0.249R  train +0.180R  held-out +0.573R (34)  worst -1.51R
#   capped ATR            192          +0.315R        +0.201R           +0.845R (34)        -1.07R
#   capped, T1 >= 1R      182          +0.393R        +0.256R           +1.060R (31)        -1.07R
# and the whole book's held-out -0.105R -> -0.100R.
CALM_ATR = True
T1_MIN_RR = 1.0              # a first target closer than this reward/risk from the average is left out
# How often each target was reached before the stop once filled (desk.backtest value_swing_report:
# swing basket from 2019, costs in, within the 40-session look-ahead). Shown on the plan's targets.
# 2026-11-08, capped ATR and T1 >= 1R: 182 filled, expectancy +0.393R (held-out +1.060R on 31), worst
# -1.07R, held a median 17 sessions; each crash beat the dip catch (2020-03 +0.60 vs -0.09, 2022 +0.16
# vs -0.23, 2025-04 +1.44 vs -0.41). T1 is further now (at least 1R), so it is reached less often.
TARGET_REACH = {"t1": 0.473, "t2": 0.203, "t3": 0.033}
TARGET_REACH_N = 182

GROUPS = ("oversold", "depth", "base", "fear", "flows")


def _check(cid: str, met: bool | None, value) -> dict:
    return {"id": cid, "met": met, "value": value}


def _group(checks: list[dict], need: int) -> dict:
    known = [c for c in checks if c["met"] is not None]
    met = sum(1 for c in known if c["met"])
    return {"met": (met >= need) if known else None, "hits": met, "known": len(known), "checks": checks}


def _atr(bars: list[dict], n: int = 14) -> float | None:
    if len(bars) < n + 1:
        return None
    tr = [max(b["high"] - b["low"], abs(b["high"] - p["close"]), abs(b["low"] - p["close"]))
          for p, b in zip(bars[-n - 1:-1], bars[-n:], strict=True)]
    return sum(tr) / n


def _sma(values: list[float], n: int) -> float | None:
    return sum(values[-n:]) / n if len(values) >= n else None


def _divergence(closes: list[float], rsis: list[float | None], look: int = 20) -> bool | None:
    """A lower low in price over the last ``look`` bars with a higher RSI at it."""
    if len(closes) < 2 * look or rsis[-1] is None:
        return None
    a = min(range(len(closes) - 2 * look, len(closes) - look), key=lambda i: closes[i])
    b = min(range(len(closes) - look, len(closes)), key=lambda i: closes[i])
    if rsis[a] is None or rsis[b] is None:
        return None
    return closes[b] < closes[a] and rsis[b] > rsis[a]


def _bb_reentry(closes: list[float], look: int = 5) -> bool | None:
    """A close under the lower band in the last ``look`` bars, and the last close back inside."""
    if len(closes) < BB_N + look:
        return None

    def lower(i: int) -> float:
        w = closes[i - BB_N + 1:i + 1]
        m = mean(w)
        sd = (sum((x - m) ** 2 for x in w) / BB_N) ** 0.5
        return m - BB_K * sd
    last = len(closes) - 1
    below = any(closes[i] < lower(i) for i in range(last - look, last))
    return below and closes[last] >= lower(last)


def _drawdown_pctl(weekly: list[dict]) -> tuple[float | None, float | None]:
    """The 52-week drawdown now, and the share of past weeks whose 52-week drawdown was shallower."""
    if len(weekly) < 60:
        return None, None
    dds = []
    for i in range(52, len(weekly)):
        top = max(b["high"] for b in weekly[i - 52:i + 1])
        dds.append(weekly[i]["close"] / top - 1 if top else 0.0)
    now = dds[-1]
    past = dds[:-1]
    return round(now, 4), round(sum(1 for d in past if d > now) / len(past), 3)


def evidence(daily: list[dict], weekly: list[dict] | None, sr: dict | None = None,
             risk: dict | None = None, vix: float | None = None) -> dict | None:
    """The five bundles at the last daily bar. None without 60 daily bars and an ATR."""
    if len(daily) < 60:
        return None
    atr = _atr(daily)
    if not atr:
        return None
    weekly = weekly or []
    closes = [b["close"] for b in daily]
    close = closes[-1]
    rs = rsi(closes)
    rsi_d = rs[-1]
    rsi_w = rsi([b["close"] for b in weekly])[-1] if len(weekly) > 15 else None
    sma20, sma50, sma200 = _sma(closes, 20), _sma(closes, 50), _sma(closes, 200)
    streak = 0
    for a, b in zip(reversed(closes[:-1]), reversed(closes[1:]), strict=False):
        if b >= a:
            break
        streak += 1
    oversold = _group([
        _check("rsi_daily", rsi_d <= RSI_DAILY if rsi_d is not None else None, rsi_d and round(rsi_d, 1)),
        _check("rsi_weekly", rsi_w <= RSI_WEEKLY if rsi_w is not None else None, rsi_w and round(rsi_w, 1)),
        _check("rsi_divergence", _divergence(closes, rs), None),
        _check("bb_reentry", _bb_reentry(closes), None),
        _check("below_ma20", (close - sma20) / atr <= MA20_ATR if sma20 else None,
               round((close - sma20) / atr, 2) if sma20 else None),
        _check("down_streak", streak >= STREAK, streak),
    ], need=2)

    dd52, pctl = _drawdown_pctl(weekly)
    gap200 = close / sma200 - 1 if sma200 else None
    depth = _group([
        _check("drawdown_pctl", pctl >= DEPTH_PCTL if pctl is not None else None,
               {"drawdown_52w": dd52, "pctl": pctl} if pctl is not None else None),
        _check("below_ma200", gap200 <= MA200_GAP if gap200 is not None else None,
               round(gap200, 4) if gap200 is not None else None),
    ], need=1)

    low20 = min(b["low"] for b in daily[-20:])
    supports = [lv for lv in (sr or {}).get("levels") or []
                if lv.get("kind") == "support" and lv.get("timeframe") in ("1w", "1M")]
    near_support = (any(abs(lv["price"] - low20) <= NEAR_ATR * atr for lv in supports) if sr else None)
    # an old crash low: a weekly swing low (2 weeks each side) more than 6 months back near today's low
    old = weekly[:-26]
    old_lows = [old[i]["low"] for i in range(2, len(old) - 2)
                if old[i]["low"] == min(b["low"] for b in old[i - 2:i + 3])]
    near_old_low = any(abs(x - low20) <= NEAR_ATR * atr for x in old_lows) if len(old) > 10 else None
    vols = [b.get("volume") or 0 for b in daily]
    climax_dry = None
    if sum(vols[-30:]):
        avg = [(sum(vols[i - 20:i]) / 20) for i in range(len(vols) - 10, len(vols))]
        days = [i for i, a in zip(range(len(vols) - 10, len(vols)), avg, strict=True) if a and vols[i] >= CLIMAX_X * a]
        climax_dry = bool(days) and days[-1] < len(vols) - 2 and mean(vols[-3:]) < 0.8 * vols[days[-1]]
    low60 = min(b["low"] for b in daily[-60:])
    took_back = min(b["low"] for b in daily[-5:]) <= low60 and close >= low60 + 0.5 * atr
    base = _group([
        _check("weekly_support", near_support, None),
        _check("old_crash_low", near_old_low, None),
        _check("capitulation_then_dry", climax_dry, None),
        _check("new_low_taken_back", took_back, None),
    ], need=2)

    phase = (risk or {}).get("phase")
    fear = _group([
        _check("selling_score", phase in ("selling", "exhausting") if phase else None, risk and risk.get("score")),
        _check("vix", vix >= VIX_FEAR if vix is not None else None, vix),
    ], need=1)
    flows = _group([_check("flows_news", None, None)], need=1)       # not computed yet (#160 / #115)

    groups = {"oversold": oversold, "depth": depth, "base": base, "fear": fear, "flows": flows}
    count = sum(1 for g in groups.values() if g["met"])
    from .playbook import _calm_atr

    return {"groups": groups, "count": count, "atr": atr, "close": close, "low20": low20,
            "plan_atr": _calm_atr(daily, atr, close) if CALM_ATR else atr,
            "sma20": sma20, "sma50": sma50, "sma200": sma200,
            "high52": max(b["high"] for b in weekly[-52:]) if weekly else max(b["high"] for b in daily),
            "setup": bool(all(groups[g]["met"] for g in REQUIRED) and count >= MIN_GROUPS)}


def conviction(ev: dict, edge: dict | None) -> dict:
    """0-100: 20 per bundle met, a part for a bundle half there. A conviction trade needs
    CONVICTION_GROUPS bundles and the desk's record positive on enough trades."""
    score = 0.0
    checklist = []
    for name in GROUPS:
        g = ev["groups"][name]
        part = 1.0 if g["met"] else (g["hits"] / max(1, g["known"]) * 0.5 if g["known"] else 0.0)
        score += 20 * part
        checklist.append({"id": name, "met": g["met"]})
    record = bool(edge and not edge.get("thin") and (edge.get("n") or 0) >= CONVICTION_MIN_N
                  and (edge.get("exp_r") or 0) > 0)
    checklist.append({"id": "record", "met": record if edge else None})
    active = ev["count"] >= CONVICTION_GROUPS and record
    return {"score": round(score), "active": active, "checklist": checklist,
            "release": ["주봉 구조 저점(손절) 이탈", "새 악재로 신저가 갱신 뒤 되찾기 실패"]}


def position_pct(entry: float, stop: float, account_risk_pct: float) -> float:
    """The share of the account to put in so that the stop loses ``account_risk_pct`` of it."""
    loss = (entry - stop) / entry if entry else 0
    if loss <= 0:
        return 0.0
    return math.floor(min(MAX_POSITION_PCT, account_risk_pct / loss) * 100) / 100   # down: never over budget


def _with_reach(target: dict) -> dict:
    """The target with its backtest reach rate (``hit_rate``, 0-1) and sample size, when known."""
    rate = TARGET_REACH.get(target.get("role"))
    return {**target, "hit_rate": rate, "hit_n": TARGET_REACH_N} if rate is not None else target


def plan(ev: dict, edge: dict | None = None) -> dict | None:
    """The long side for desk.playbook, or None when the evidence is not there."""
    if not ev or not ev["setup"]:
        return None
    from .playbook import _round, _target

    atr, close = ev.get("plan_atr") or ev["atr"], ev["close"]
    e1 = close
    e2 = close - STEP_ATR * atr
    e3 = max(close, ev["low20"] + atr)                 # a stop entry: the reclaim, after a limit fill
    entries = [{"step": 1, "price": _round(e1), "share": SHARES[0], "kind": "limit"},
               {"step": 2, "price": _round(e2), "share": SHARES[1], "kind": "limit"},
               {"step": 3, "price": _round(e3), "share": SHARES[2], "kind": "stop", "after": ["reclaim"]}]
    avg = sum(e["price"] * e["share"] for e in entries)
    stop = _round(min(ev["low20"], e2) - STOP_ATR * atr)
    fall_top, fall_low = ev["high52"], ev["low20"]
    span = max(0.0, fall_top - fall_low)
    targets = []
    for price, basis in ((ev["sma20"], "20일선"),
                         (fall_low + 0.382 * span, "하락폭 38.2% 되돌림"),
                         (max(ev["sma50"] or 0, fall_low + 0.5 * span), "50일선 · 하락폭 50% 되돌림")):
        # the first target must pay T1_MIN_RR of the risk; the next ones only lie beyond it. The roles
        # go by position: a 20-day line too close to pay leaves the retracement as T1 (half off there).
        floor = targets[-1]["price"] + 0.3 * atr if targets else max(avg + 0.3 * atr, avg + T1_MIN_RR * (avg - stop))
        if price and price > floor:
            role = f"t{len(targets) + 1}"
            extra = {"t1": {"action": "take_half", "then_stop": _round(avg)},
                     "t2": {"action": "take_rest"},                 # desk.outcome scores the trade to T2
                     "t3": {"action": "trail", "trail_atr": 2.0}}[role]   # shown; T2 onward on a 2-ATR trail
            targets.append(_with_reach(_target(role, price, basis, "desk", avg, stop, True, **extra)))
    if not targets:
        return None
    conv = conviction(ev, edge)
    kind = "conviction" if conv["active"] else "normal"
    budget = ACCOUNT_RISK_PCT[kind]
    return {"setup": "value_swing", "reason": None, "size_hint": "normal" if conv["active"] else "half",
            "flat_by_close": False,
            "zone": {"low": _round(stop + STOP_ATR * atr), "high": _round(e1), "mid": _round((e1 + e2) / 2),
                     "score": ev["count"], "members": []},
            "entries": entries, "avg_entry": _round(avg), "stop": stop, "risk": _round(avg - stop),
            "targets": targets,
            "time_stop": {"bars": TIME_STOP_BARS, "interval": "1d", "required": True,
                          "rule": f"마지막 체결 뒤 {TIME_STOP_BARS}거래일 안에 T1 을 못 가면 정리"},
            "conditions": [{"id": f"vs_{name}", "met": ev["groups"][name]["met"], "how": "computed", "value": None}
                           for name in GROUPS],
            "score": conv["score"], "conviction": conv,
            # from the numbers the plan shows: the page can check the budget with them
            "risk_budget": {"kind": kind, "account_pct": budget,
                            "position_pct": position_pct(_round(avg), stop, budget)},
            "evidence": {k: {"met": g["met"], "checks": g["checks"]} for k, g in ev["groups"].items()},
            "source": {k: "desk" for k in ("zone", "entries", "stop", "t1", "t2", "time_stop")}, "model": None}


def apply(pb: dict | None, daily: list[dict], weekly: list[dict] | None, horizon: str, sr: dict | None = None,
          risk: dict | None = None, vix: float | None = None, calibrate: bool = True) -> dict | None:
    """The playbooks with the long slot taken by an oversold swing buy when the evidence is there
    (swing frame only - its secondary bars are the weekly ones). The long it replaces is named in
    ``replaced``. ``calibrate=False`` (the backtest) gives conviction no record: never active.
    Idempotent on the same input; ENABLED off leaves the playbooks as they are. A failure here is
    logged and leaves them as built - an extra setup must never cost the run its playbooks."""
    if not ENABLED or not pb or horizon != "swing":
        return pb
    try:
        return _apply(pb, daily, weekly, horizon, sr, risk, vix, calibrate)
    except Exception:  # odd bars (a flat series, a gap in the weeks)
        log.exception("value swing failed; the playbooks stay as built")
        return pb


def _apply(pb: dict, daily: list[dict], weekly: list[dict] | None, horizon: str, sr: dict | None,
           risk: dict | None, vix: float | None, calibrate: bool) -> dict:
    ev = evidence(daily, weekly, sr, risk, vix)
    if not ev or not ev["setup"]:
        return pb
    edge = None
    if calibrate:
        from . import calibration

        edge = calibration.TABLE.look(horizon, "long", "value_swing", (pb.get("regime") or {}).get("id"),
                                      conviction(ev, None)["score"])
    side = plan(ev, edge)
    if not side:
        return pb
    prev = pb.get("long") or {}
    side["direction"] = prev.get("direction")
    side["replaced"] = prev.get("setup") if prev.get("setup") not in (None, "none", "value_swing") else None
    if edge:
        side.update(p_t1=edge["p_t1"], exp_r=edge["exp_r"], n=edge["n"], calibration=edge)
    return {**pb, "long": side}


def prompt_line(side: dict | None) -> str | None:
    """'과매도 스윙' line for the deciding agents."""
    if not side or side.get("setup") != "value_swing":
        return None
    met = [k for k, g in side["evidence"].items() if g["met"]]
    conv = side["conviction"]
    reach = ", ".join(f"{t['role'].upper()} {t['hit_rate']:.0%}" for t in side.get("targets") or []
                      if t.get("hit_rate") is not None)
    return (f"Oversold swing buy (2-8 weeks): evidence {', '.join(met)}; conviction {conv['score']}"
            + (" - CONVICTION trade" if conv["active"] else "")
            + f"; risk {side['risk_budget']['account_pct']}% of the account at the stop"
            + (f"; backtest reach before the stop {reach}" if reach else "") + ".")
