"""One run, every horizon (#696): the deterministic plan pieces of scalp · intraday · short swing · swing,
what kind of instrument the ticker is, and which horizon the market now suits.

The model still runs once, for the run's horizon. Beside it, with no model:

- per horizon (desk.horizons.HORIZONS) the **map** - the entry now on that horizon's live playbooks
  (desk.entry_now: side, entry zone, stop, targets, past rate), its nearest support and resistance
  (desk.sr on its own bars), its chart read (desk.chart_read on CHART_TF) - and, for the run's horizon,
  the **model**'s own plan (entry, stop, target, opinion). ``source`` says which leads: "model" where the
  model spoke, else "map" ("모델 의견 없음").
- ``instrument_class``: leverage · inverse (desk.related.LEVERAGED) · bond · defensive (lists here) ·
  general. The risk of a plan is the instrument's before it is anyone's taste: this replaces choosing a
  profile (#696).
- ``horizon_fit``: the regime now (desk.regime_live, #694: trend up / down, range, turning - the daily read
  first) and so which horizons fit: a range back and forth between its edges suits scalp and intraday, a
  trend short swing and swing.

Only for a run of today (the bars now); an older run gets ``horizons`` None and says why. Everything is
bounded by TIMEOUT_SEC per piece; a piece that fails is an ``error`` on its horizon, never the run's.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import UTC, datetime

from . import accumulation_live, channels, chart_read, direction_stats, entry_now, horizons, regime_live
from .plan import HORIZON_LABELS
from .sr import support_resistance
from .underlying import leverage_of

log = logging.getLogger(__name__)

TIMEOUT_SEC = 20.0
CHART_TF = {"scalp": "5m", "intraday": "15m", "short_swing": "60m", "swing": "1d"}
LEVELS_EACH = 2
ORDER = ("scalp", "intraday", "short_swing", "swing")
ACCUMULATION_VIEW = {"scalp": "short", "intraday": "short", "short_swing": "mid", "swing": "long"}

# safe havens: Treasuries and short bills, investment-grade bond funds (KR: 국고채 · 단기채권 ETF)
BOND = frozenset({"TLT", "IEF", "SHY", "BND", "AGG", "GOVT", "EDV", "VGLT", "VGIT", "TLH", "IEI", "SGOV", "BIL",
                  "SCHO", "SCHR", "TIP", "LQD", "MUB", "148070.KS", "114260.KS", "153130.KS"})
# defensive: staples, utilities, health care, telecom - demand that holds in a downturn
DEFENSIVE = frozenset({"XLU", "XLP", "XLV", "VPU", "VDC", "KO", "PEP", "PG", "JNJ", "WMT", "COST", "MO", "PM", "CL",
                       "KMB", "GIS", "DUK", "SO", "NEE", "AEP", "VZ", "T", "MRK", "PFE", "ABBV", "MCD",
                       "017670.KS", "030200.KS", "033780.KS", "015760.KS", "097950.KS"})
CLASS_KO = {"leverage": ("레버리지", "공격"), "inverse": ("인버스", "공격 · 하락 베팅"), "bond": ("채권 · 국채", "안전자산 대피"),
            "defensive": ("경기방어주", "방어"), "general": ("일반", "보통")}
REGIME_KO = {"trend_up": "상승 추세", "trend_down": "하락 추세", "range": "박스권", "turning": "추세 전환 중",
             "unknown": "판정 불가"}
FIT = {   # regime -> horizons that suit it best, then the ones that are fine; the rest are a poor fit
    "range": (("scalp", "intraday"), ("short_swing",)),
    "trend_up": (("short_swing", "swing"), ("intraday",)),
    "trend_down": (("short_swing", "swing"), ("intraday",)),
    "turning": (("intraday", "short_swing"), ("scalp",)),
}
HIGH_VOL_PCT = 5.0         # a daily ATR this wide (% of the price): a swing's stop is wide


def instrument_class(ticker: str) -> dict:
    """What the ticker is, for the plan's risk: leverage · inverse · bond · defensive · general."""
    t = ticker.upper()
    lev = leverage_of(t)
    near = " (근사 기초자산 — 자기 지수를 따른다)" if lev and lev[2] else ""
    if lev and lev[1] < 0:
        cid, basis = "inverse", f"{lev[0]} 의 하루 {lev[1]:g}배(인버스){near}"
    elif lev and abs(lev[1]) > 1:
        cid, basis = "leverage", f"{lev[0]} 의 하루 {lev[1]:g}배{near}"
    elif t in BOND:
        cid, basis = "bond", "국채 · 채권 ETF 목록"
    elif t in DEFENSIVE:
        cid, basis = "defensive", "경기방어 업종 · 종목 목록"
    else:
        cid, basis = "general", "레버리지 · 인버스 · 채권 · 경기방어 목록에 없음"
    label, risk = CLASS_KO[cid]
    return {"id": cid, "label": label, "risk": risk, "basis": basis,
            "multiple": lev[1] if lev else None, "underlying": lev[0] if lev else None}


def horizon_fit(view: dict, cls: dict, atr_pct: float | None) -> dict:
    """Which horizons the regime now suits (desk.regime_live.view: its regime, the daily read first) and one
    sentence."""
    frames = view.get("frames") or {}
    regime = view.get("regime") or "unknown"
    lead = next((f for f in (frames.get("1d"), frames.get("60m")) if f and f.get("regime") == regime), {})
    best, ok = FIT.get(regime, ((), ()))
    fit = {h: "good" if h in best else "ok" if h in ok else "poor" if best else "unknown" for h in ORDER}
    names = " · ".join(horizons.HORIZONS[h].label for h in best)
    if regime == "range" and lead.get("box"):
        b = lead["box"]
        text = (f"지금은 박스권(폭 {b['width_pct']}%, 상단 {b['touches_high']}번 · 하단 {b['touches_low']}번 닿음) — "
                f"상 · 하단을 오가는 장이라 {names} 쪽이 맞아요. 스윙은 박스를 벗어나야 맞아요.")
    elif regime in ("trend_up", "trend_down"):
        text = f"지금은 {REGIME_KO[regime]}(ADX {lead.get('adx')}) — 추세를 타는 {names} 쪽이 맞아요."
    elif regime == "turning":
        text = f"추세가 바뀌는 중이에요 — 짧게 확인하며 가는 {names} 쪽이 맞아요."
    else:   # regime_live's unknown: no clear trend nor box (or no bars) - said, no horizon picked
        text = f"장세가 뚜렷하지 않아요({view.get('summary') or '판단 보류'}) — 어느 기간이 맞는지 말하지 않아요."
    notes = []
    if cls["id"] in ("leverage", "inverse"):
        notes.append("레버리지 · 인버스는 오래 들수록 변동성 끌림(복리 손실)이 쌓여 짧은 기간이 유리해요")
    elif cls["id"] in ("bond", "defensive"):
        notes.append(f"{'채권 · 국채 ETF 는' if cls['id'] == 'bond' else '경기방어주는'} 움직임이 느려 짧은 기간은 "
                     "손익비가 작아요")
    if atr_pct is not None and atr_pct >= HIGH_VOL_PCT:
        notes.append(f"일봉 ATR {atr_pct:.1f}% — 변동성이 커서 스윙은 손절 폭이 넓어요")
    return {"regime": regime, "regime_ko": REGIME_KO.get(regime, regime), "best": list(best), "fit": fit,
            "sentence": " ".join([text, *[n + "." for n in notes]]), "basis": lead.get("basis") or [],
            "summary": view.get("summary"),
            "frames": {tf: {k: f.get(k) for k in ("regime", "adx", "box", "as_of")} for tf, f in frames.items()},
            "atr_pct": round(atr_pct, 2) if atr_pct is not None else None,
            "record": None}      # per-horizon past R (#693) when it is measured


# ---------------------------------------------------------------- per horizon

def _levels(sr: dict | None) -> dict:
    """The nearest LEVELS_EACH supports under and resistances over the price of ``sr``."""
    if not sr:
        return {"support": [], "resistance": []}
    ref = sr.get("reference_price")
    rows = [{"price": lv["price"], "strength": lv.get("strength"), "basis": lv.get("basis")}
            for lv in sr.get("levels") or [] if lv.get("price") is not None]
    if ref is None:
        return {"support": [], "resistance": []}
    below = sorted((r for r in rows if r["price"] < ref), key=lambda r: -r["price"])[:LEVELS_EACH]
    above = sorted((r for r in rows if r["price"] >= ref), key=lambda r: r["price"])[:LEVELS_EACH]
    return {"support": below, "resistance": above}


def _map(got: dict | None) -> dict | None:
    """desk.entry_now's pick as the horizon's map: the side, how and where to get in, stop, targets."""
    if not got:
        return None
    best = got.get("best")
    out = {"stand_aside": (got.get("stand_aside") or {}).get("reason"), "regime": got.get("regime"),
           "price_now": got.get("price_now"), "atr": got.get("atr")}
    if best:
        out.update({k: best.get(k) for k in ("side", "side_ko", "setup", "kind", "kind_ko", "zone", "first",
                                             "avg_entry", "stop", "stop_pct", "targets", "from_now_pct",
                                             "expected_r", "ev_r", "p_t1", "n", "thin", "why")})
    return out


def _chart(frame: dict | None) -> dict | None:
    if not frame:
        return None
    lv = frame.get("levels") or {}
    return {"timeframe": frame.get("timeframe"), "summary": frame.get("summary"), "trend": frame.get("trend"),
            "above": (lv.get("above") or [])[:LEVELS_EACH], "below": (lv.get("below") or [])[:LEVELS_EACH],
            "patterns": [p.get("kind") or p.get("name") for p in frame.get("patterns") or []][:2]}


_CLOSE_CHECKS = {"reclaim", "rejection_wick", "rejection_bar", "higher_low", "lower_high", "volume_on_break"}


def _scenarios(got: dict | None, h: horizons.Horizon, *, scripted: bool = False) -> dict:
    got = got or {}
    regime = got.get("regime")
    fallback = got.get("scenario_timeframe", h.primary) != h.primary
    candidates = {c["side"]: c for c in (got.get("best"), got.get("other")) if c and not fallback}
    details = {} if fallback else got.get("scenario_details") or {}
    rows = {}
    for side in entry_now.SIDE_KO:
        candidate = candidates.get(side) or {}
        detail = details.get(side) or {}
        setup = candidate.get("setup")
        state = detail.get("state")
        checks = (detail.get("conditions") or []) if candidate else []
        entries = candidate.get("entries") or []
        ready = (state == "confirmed" and candidate.get("kind") == "now" and not got.get("stale")
                 and (candidate.get("ev_r") or 0) > 0
                 and all(c["id"] != "event_clear" or c["met"] for c in checks))
        reason = detail.get("reason")
        if not candidate and not reason:
            reason = ("이 기간의 봉이 부족해 진입을 검토할 수 없어요" if fallback else
                      "손절 기준을 지나 진입이 무효예요" if state == "invalid" else
                      "이 방향으로 진입을 검토할 가격 근거가 부족해요")
        trigger = detail.get("trigger") if candidate else None
        rows[side] = {
            "status": ("consider_entry" if ready else "wait_for_conditions") if candidate else "no_setup",
            "reason": reason, "regime": regime, "setup": setup,
            "direction": candidate.get("direction"), "basis": detail.get("basis") or [],
            "entry_type": candidate.get("kind"),
            "entry_price": candidate.get("first"), "entry_zone": candidate.get("zone"),
            "stop_loss": candidate.get("stop"), "targets": candidate.get("targets") or [],
            "confirmation": {
                "timeframe": h.primary,
                "intrabar": [c for c in checks if c["id"] not in _CLOSE_CHECKS],
                "bar_close": [c for c in checks if c["id"] in _CLOSE_CHECKS],
                "trigger_price": (trigger or {}).get("price") or
                next((e["price"] for e in entries if e["kind"] == "stop"), None),
                "trigger_text": (trigger or {}).get("text"),
                "lower_trigger": detail.get("lower_trigger") if candidate else None,
            },
            "history": direction_stats.lookup(entry_now.REPORT_DIR, h.id, side, None if scripted else setup, regime),
        }
    return rows


async def _bounded(coro, what: str, errors: dict):
    try:
        return await asyncio.wait_for(coro, TIMEOUT_SEC)
    except Exception as exc:  # noqa: BLE001 - incl. timeout: this piece is missing, the rest stands
        errors[what] = type(exc).__name__
        log.info("all horizons: %s failed: %s", what, type(exc).__name__)
        return None


async def _one(prices, ticker: str, h: horizons.Horizon, sr_main: dict | None, main: str, risk=None) -> dict:
    errors: dict[str, str] = {}
    tf = CHART_TF[h.id]

    async def chart():
        got = await prices.candles(ticker, tf, channels.BARS[tf])
        bars = got["bars"]
        return await asyncio.to_thread(chart_read.frame, bars, tf, channels.is_live(bars, tf))

    sr_task = (asyncio.sleep(0, sr_main) if h.id == main and sr_main
               else support_resistance(prices, ticker, None, horizon=h.id))
    sr, en, ch = await asyncio.gather(_bounded(sr_task, "levels", errors),
                                      # the market's score as the entry page reads it: the live playbooks'
                                      # cache is shared (desk.playbook_live), an ungated build must not land there
                                      _bounded(entry_now.entry_now(prices, ticker, h.id, risk=risk,
                                                                   scenario_details=True), "entry", errors),
                                      _bounded(chart(), "chart", errors))
    return {"horizon": h.id, "label": h.label, "holding": h.holding, "flat_by_close": h.flat_by_close,
            "source": "map", "model": None, "map": _map(en), "scenarios": _scenarios(en, h), "levels": _levels(sr),
            "atr": (sr or {}).get("atr"), "chart": _chart(ch), "errors": errors or None}


MODEL_KEYS = ("origin", "ticker", "opinion", "rating", "bias", "entry_price", "entry_type", "stop_loss",
              "price_target", "reward_risk", "long_trigger", "short_trigger", "valid_until", "why")


def _model(plan: dict, ticker: str) -> dict:
    """The model's own plan on the run's horizon (``origin`` "plan"; ``why`` from its horizon line)."""
    line = (plan.get("horizon_lines") or {}).get(plan.get("horizon") or "") or {}
    return {**{k: plan.get(k) for k in MODEL_KEYS}, "origin": "plan", "ticker": ticker, "why": line.get("why")}


def _line(line: dict, ticker: str) -> dict:
    """The model's line for another horizon (desk.plan.split_horizon_lines, ``origin`` "line"): rating and
    levels, no triggers. ``ticker``: the instrument its prices are on - a leveraged run's lines are the
    underlying's, the one the agents analysed."""
    return {**{k: line.get(k) for k in MODEL_KEYS}, "origin": "line", "ticker": line.get("ticker") or ticker}


def finish(got: dict, plan: dict, main: str, ticker: str) -> dict:
    """``got`` (``maps``) with the model on each horizon it spoke for: the plan on ``main``, the PM's line
    (``plan["horizon_lines"]``) on the others. ``source`` "model" there, else "map"."""
    rows = got.get("horizons")
    if not rows:
        return got
    lines = plan.get("horizon_lines") or {}
    for hid, row in rows.items():
        line = lines.get(hid) or {}
        spoke = bool(line.get("rating") or line.get("entry_price") is not None)
        row["model"], row["underlying_line"] = None, None
        if hid == main:
            row["model"] = _model({**plan, "horizon": main}, ticker)
        elif spoke and (line.get("ticker") or ticker) == ticker:
            row["model"] = _line(line, ticker)
        elif spoke:
            # a leveraged run's line is on the underlying: its prices are not the product's (the board's)
            row["underlying_line"] = _line(line, ticker)
        row["source"] = "model" if row["model"] else "map"
    return got


def fake(ticker: str, plan: dict, main: str) -> dict:
    """A scripted run's four horizons (DESK_FAKE_RUNS runs offline): the same shape, numbers set around the
    plan's entry so the page can be built without quota or market data - marked ``fake``."""
    cls = instrument_class(ticker)
    price = plan.get("entry_price") or 100.0
    up = plan.get("bias") != "short"
    sign = 1 if up else -1
    out = {}
    for i, hid in enumerate(ORDER):
        h = horizons.HORIZONS[hid]
        atr = price * 0.004 * (i + 1) ** 1.5
        first = round(price - sign * 0.5 * atr, 2)
        stop = round(first - sign * h.stop_atr * atr, 2)
        targets = [{"role": r, "price": round(first + sign * m * atr, 2), "rr": round(m / h.stop_atr, 2)}
                   for r, m in (("t1", h.target_atr), ("t2", 1.6 * h.target_atr))]
        out[hid] = {
            "horizon": hid, "label": h.label, "holding": h.holding, "flat_by_close": h.flat_by_close,
            "source": "map", "model": None, "atr": round(atr, 4), "errors": None, "accumulation": None,
            "map": {"regime": "range", "price_now": price, "atr": round(atr, 4),
                    "side": "long" if up else "short", "side_ko": "롱" if up else "숏", "setup": "pullback",
                    "kind": "limit", "kind_ko": "지정가", "zone": sorted([first, round(first + sign * 0.3 * atr, 2)]),
                    "first": first, "avg_entry": first, "stop": stop, "stop_pct": round((stop / first - 1) * 100, 2),
                    "targets": targets, "from_now_pct": round((first / price - 1) * 100, 2), "expected_r": None,
                    "ev_r": None, "p_t1": None, "n": 0, "thin": True, "why": ["대본(가짜 실행) 예시 값"],
                    "stand_aside": "대본 예시 — 기록이 얇아 기대값 없음(참고용)"},
            "levels": {"support": [{"price": round(price - k * atr, 2), "strength": 3.0 - k, "basis": "대본 지지"}
                                   for k in (1, 2)],
                       "resistance": [{"price": round(price + k * atr, 2), "strength": 3.0 - k, "basis": "대본 저항"}
                                      for k in (1, 2)]},
            "chart": {"timeframe": CHART_TF[hid], "summary": f"{channels.TF_KO[CHART_TF[hid]]} — 대본 차트 읽기 예시.",
                      "trend": {"state": "sideways", "ema20": round(price, 2), "ema50": round(price * 1.002, 2),
                                "basis": "대본 예시"},
                      "above": [{"price": round(price + atr, 2), "name": "대본 저항", "refs": [{"kind": "sr", "side": "high", "at": None}],
                                 "distance_pct": round(atr / price * 100, 2)}],
                      "below": [{"price": round(price - atr, 2), "name": "대본 지지", "refs": [{"kind": "sr", "side": "low", "at": None}],
                                 "distance_pct": round(-atr / price * 100, 2)}],
                      "patterns": []},
        }
        primary = out[hid]["map"]
        opposite = "short" if up else "long"
        other = {**primary, "side": opposite, "first": round(2 * price - first, 2),
                 "zone": sorted(round(2 * price - z, 2) for z in primary["zone"]),
                 "stop": round(2 * price - stop, 2),
                 "targets": [{**t, "price": round(2 * price - t["price"], 2)} for t in targets]}
        out[hid]["scenarios"] = _scenarios({"best": primary, "other": other, "regime": "range"}, h, scripted=True)
    view = {"regime": "range", "summary": "1시간봉 박스 · 일봉 박스(대본)",
            "frames": {"1d": {"regime": "range", "adx": 16.0, "as_of": None, "basis": ["대본 예시"],
                              "box": {"high": round(price * 1.06, 2), "low": round(price * 0.95, 2), "touches_high": 3,
                                      "touches_low": 2, "width_pct": 11.58, "width_atr": 4.2, "round_trips": 2}}}}
    fit = horizon_fit(view, cls, 3.0)
    fit["errors"] = None
    got = {"instrument_class": cls, "horizons": out, "horizon_fit": fit, "horizons_note": None, "horizons_fake": True}
    return finish(got, plan, main, ticker)


async def _accumulation_inputs(prices, ticker: str):
    errors: dict[str, str] = {}

    async def short():
        for tf in ("240m", "60m"):
            result = await _bounded(prices.candles(ticker, tf, channels.BARS[tf]), tf, errors)
            if result and result.get("bars"):
                return tf, result
        return "240m", None

    short_result, daily, weekly = await asyncio.gather(
        _bounded(short(), "short accumulation", errors),
        _bounded(prices.candles(ticker, "1d", accumulation_live.DAILY_BARS), "mid accumulation", errors),
        _bounded(prices.candles(ticker, "1w", accumulation_live.WEEKLY_BARS), "long accumulation", errors),
    )
    tf, hourly = short_result or ("240m", None)
    return (tf, "1d"), (hourly, daily), weekly.get("bars") if weekly else None


async def build(prices, ticker: str, plan: dict, main: str, sr_main: dict | None, *, same_day: bool,
                scripted: bool = False, risk=None) -> dict:
    """``plan``'s horizons · instrument_class · horizon_fit (a dict to merge into the plan): ``maps`` with
    the model's plan and lines on them (``finish``). ``scripted``: a fake run with no prices gets ``fake``'s."""
    if prices is None and scripted:
        return fake(ticker, plan, main)
    base = maps(prices, ticker, main, sr_main, same_day=same_day, risk=risk)
    if prices is None or not same_day:
        return finish(await base, plan, main, ticker)
    got, inputs = await asyncio.gather(base, _accumulation_inputs(prices, ticker))
    rows = got["horizons"]
    price = next((value for row in rows.values() if (value := (row.get("map") or {}).get("price_now"))), None)
    read = await _bounded(accumulation_live.read(*inputs, datetime.now(UTC), price), "accumulation", {})
    views = (read or (None, None))[0] or {}
    for hid, row in rows.items():
        row["accumulation"] = views.get(ACCUMULATION_VIEW[hid])
    return finish(got, plan, main, ticker)


async def maps(prices, ticker: str, main: str, sr_main: dict | None, *, same_day: bool, risk=None) -> dict:
    """The four horizons' deterministic pieces, no model yet (``source`` "map")."""
    cls = instrument_class(ticker)
    if prices is None or not same_day:
        why = "지난 날짜의 분석이라 지금 봉으로 만든 기간별 계획은 붙이지 않았어요" if prices is not None else "시세 원천 없음"
        return {"instrument_class": cls, "horizons": None, "horizon_fit": None, "horizons_note": why}

    regime_errors: dict[str, str] = {}
    rows, view = await asyncio.gather(
        asyncio.gather(*(_one(prices, ticker, horizons.HORIZONS[h], sr_main, main, risk) for h in ORDER)),
        _bounded(regime_live.view(prices, ticker), "regime", regime_errors))
    out = {r["horizon"]: r for r in rows}
    swing_atr = (out.get("swing") or {}).get("atr")
    ref = ((out.get("swing") or {}).get("map") or {}).get("price_now")
    atr_pct = swing_atr / ref * 100 if swing_atr and ref and not math.isnan(swing_atr) else None
    fit = horizon_fit(view or {}, cls, atr_pct)
    fit["errors"] = regime_errors or None
    return {"instrument_class": cls, "horizons": out, "horizon_fit": fit, "horizons_note": None}


# ---------------------------------------------------------------- the deciding agents' prompt

_CLASS_EN = {
    "leverage": ("a daily-reset leveraged product ({basis}): its moves are a multiple of the underlying's and "
                 "volatility decay erodes it in a sideways market - prefer the shorter horizons there and size smaller."),
    "inverse": ("a daily-reset inverse product ({basis}): it gains when the underlying falls; volatility decay "
                "erodes it over time - hold it only while the down move lasts."),
    "bond": ("a bond / Treasury ETF: a slow mover driven by rates and a safe haven when stocks sell off - "
             "short horizons have little room between stop and target."),
    "defensive": ("a defensive stock or sector (staples, utilities, health care, telecom): low beta, slower moves, "
                  "holds up when the market sells off."),
}


def class_note(cls: dict) -> str | None:
    """The instrument's class for the deciding agents (#696: in place of the owner's chosen style)."""
    text = _CLASS_EN.get(cls.get("id"))
    if not text:
        return None
    basis = f"{cls.get('multiple'):g}x {cls.get('underlying')}" if cls.get("multiple") else ""
    return "Instrument class: " + text.format(basis=basis)


def mandate(main: str, subject: str) -> str:
    """The PM's four horizon lines (desk.plan.split_horizon_lines reads them; fixed English labels)."""
    rows = "\n".join(f"{HORIZON_LABELS[h]} Plan: <Buy|Overweight|Hold|Underweight|Sell> / Entry: <number> / "
                     f"Stop: <number> / Target: <number> / Why: <one short clause>" for h in ORDER)
    holds = "; ".join(f"{HORIZON_LABELS[h]} {horizons.HORIZONS[h].holding_en}" for h in ORDER)
    return ("All four horizons, one decision - the portfolio manager only (the trader and the risk debaters do "
            "not write these): after the final levels line (and the Entry line), end the decision with exactly "
            f"these four lines, prices in {subject}'s quote currency, keeping the English labels and the English "
            f"rating word in any output language:\n{rows}\n"
            f"Holding: {holds}. The {HORIZON_LABELS[main]} line repeats the final levels above. Each horizon "
            "is its own call on its own bars - a scalp may be a Buy while the swing is a Hold; Hold still gives "
            "the level at which to act. Base each on that horizon's support/resistance and ATR (the desk's "
            "per-horizon reference, when given) and keep reward:risk of at least 1.5.")


def _num(x) -> str:
    return f"{x:.4g}" if isinstance(x, (int, float)) else "?"


def prompt_note(got: dict | None) -> str | None:
    """``maps``' four horizons in a few lines for the deciding agents: levels, ATR and the live playbook's pick."""
    rows = (got or {}).get("horizons")
    if not rows:
        return None
    out = ["The desk's per-horizon reference (no model; each horizon's own bars):"]
    for hid in ORDER:
        r = rows.get(hid)
        if not r:
            continue
        lv = r.get("levels") or {}
        sup = ", ".join(_num(x["price"]) for x in lv.get("support") or []) or "none"
        res = ", ".join(_num(x["price"]) for x in lv.get("resistance") or []) or "none"
        bits = [(f"- {HORIZON_LABELS[hid]} ({CHART_TF[hid]} chart, ATR {_num(r.get('atr'))}): support {sup} / "
                f"resistance {res}")]
        m = r.get("map") or {}
        if m.get("side"):
            t1 = next((t["price"] for t in m.get("targets") or [] if t.get("role") == "t1"), None)
            bits.append(f"playbook {m['side']} {m.get('kind') or ''} near {_num(m.get('first'))}, stop "
                        f"{_num(m.get('stop'))}, T1 {_num(t1)}")
        elif m.get("stand_aside"):
            bits.append("playbook: stand aside")
        if (r.get("chart") or {}).get("trend"):
            bits.append(f"trend {r['chart']['trend'].get('state')}")
        out.append("; ".join(bits))
    fit = got.get("horizon_fit") or {}
    if fit.get("best"):
        out.append(f"Regime now: {fit.get('regime')} - suits {', '.join(HORIZON_LABELS[h] for h in fit['best'])}.")
    return "\n".join(out)
