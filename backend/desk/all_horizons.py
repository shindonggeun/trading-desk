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

from . import channels, chart_read, entry_now, horizons, regime_live
from .sr import support_resistance
from .underlying import leverage_of

log = logging.getLogger(__name__)

TIMEOUT_SEC = 20.0
CHART_TF = {"scalp": "5m", "intraday": "15m", "short_swing": "60m", "swing": "1d"}
LEVELS_EACH = 2
ORDER = ("scalp", "intraday", "short_swing", "swing")

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
    if lev and lev[1] < 0:
        cid, basis = "inverse", f"{lev[0]} 의 하루 {lev[1]}배(인버스)"
    elif lev and abs(lev[1]) > 1:
        cid, basis = "leverage", f"{lev[0]} 의 하루 {lev[1]}배"
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


async def _bounded(coro, what: str, errors: dict):
    try:
        return await asyncio.wait_for(coro, TIMEOUT_SEC)
    except Exception as exc:  # noqa: BLE001 - incl. timeout: this piece is missing, the rest stands
        errors[what] = type(exc).__name__
        log.info("all horizons: %s failed: %s", what, type(exc).__name__)
        return None


async def _one(prices, ticker: str, h: horizons.Horizon, sr_main: dict | None, main: str) -> dict:
    errors: dict[str, str] = {}
    tf = CHART_TF[h.id]

    async def chart():
        got = await prices.candles(ticker, tf, channels.BARS[tf])
        bars = got["bars"]
        return await asyncio.to_thread(chart_read.frame, bars, tf, channels.is_live(bars, tf))

    sr_task = (asyncio.sleep(0, sr_main) if h.id == main and sr_main
               else support_resistance(prices, ticker, None, horizon=h.id))
    sr, en, ch = await asyncio.gather(_bounded(sr_task, "levels", errors),
                                      _bounded(entry_now.entry_now(prices, ticker, h.id), "entry", errors),
                                      _bounded(chart(), "chart", errors))
    return {"horizon": h.id, "label": h.label, "holding": h.holding, "flat_by_close": h.flat_by_close,
            "source": "map", "model": None, "map": _map(en), "levels": _levels(sr),
            "atr": (sr or {}).get("atr"), "chart": _chart(ch), "errors": errors or None}


def _model(plan: dict) -> dict:
    """The model's own plan on the run's horizon."""
    return {k: plan.get(k) for k in ("opinion", "rating", "bias", "entry_price", "entry_type", "stop_loss",
                                     "price_target", "long_trigger", "short_trigger", "valid_until")}


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
            "source": "map", "model": None, "atr": round(atr, 4), "errors": None,
            "map": {"stand_aside": None, "regime": "range", "price_now": price, "atr": round(atr, 4),
                    "side": "long" if up else "short", "side_ko": "롱" if up else "숏", "setup": "pullback",
                    "kind": "limit", "kind_ko": "지정가", "zone": sorted([first, round(first + sign * 0.3 * atr, 2)]),
                    "first": first, "avg_entry": first, "stop": stop, "stop_pct": round((stop / first - 1) * 100, 2),
                    "targets": targets, "from_now_pct": round((first / price - 1) * 100, 2), "expected_r": None,
                    "ev_r": None, "p_t1": None, "n": 0, "thin": True, "why": ["대본(가짜 실행) 예시 값"]},
            "levels": {"support": [{"price": round(price - k * atr, 2), "strength": 3.0 - k, "basis": "대본 지지"}
                                   for k in (1, 2)],
                       "resistance": [{"price": round(price + k * atr, 2), "strength": 3.0 - k, "basis": "대본 저항"}
                                      for k in (1, 2)]},
            "chart": {"timeframe": CHART_TF[hid], "summary": f"{channels.TF_KO[CHART_TF[hid]]} — 대본 차트 읽기 예시.",
                      "trend": "sideways", "above": [], "below": [], "patterns": []},
        }
    out[main]["model"] = _model(plan)
    out[main]["source"] = "model"
    view = {"regime": "range", "summary": "1시간봉 박스 · 일봉 박스(대본)",
            "frames": {"1d": {"regime": "range", "adx": 16.0, "as_of": None, "basis": ["대본 예시"],
                              "box": {"high": round(price * 1.06, 2), "low": round(price * 0.95, 2), "touches_high": 3,
                                      "touches_low": 2, "width_pct": 11.58}}}}
    fit = horizon_fit(view, cls, 3.0)
    fit["errors"] = None
    return {"instrument_class": cls, "horizons": out, "horizon_fit": fit, "horizons_note": None, "horizons_fake": True}


async def build(prices, ticker: str, plan: dict, main: str, sr_main: dict | None, *, same_day: bool,
                scripted: bool = False) -> dict:
    """``plan``'s horizons · instrument_class · horizon_fit (a dict to merge into the plan). ``scripted``: a
    fake run with no prices gets ``fake``'s."""
    cls = instrument_class(ticker)
    if prices is None and scripted:
        return fake(ticker, plan, main)
    if prices is None or not same_day:
        why = "지난 날짜의 분석이라 지금 봉으로 만든 기간별 계획은 붙이지 않았어요" if prices is not None else "시세 원천 없음"
        return {"instrument_class": cls, "horizons": None, "horizon_fit": None, "horizons_note": why}

    regime_errors: dict[str, str] = {}
    rows, view = await asyncio.gather(
        asyncio.gather(*(_one(prices, ticker, horizons.HORIZONS[h], sr_main, main) for h in ORDER)),
        _bounded(regime_live.view(prices, ticker), "regime", regime_errors))
    out = {r["horizon"]: r for r in rows}
    if main in out:
        out[main]["model"] = _model(plan)
        out[main]["source"] = "model"
    swing_atr = (out.get("swing") or {}).get("atr")
    ref = ((out.get("swing") or {}).get("map") or {}).get("price_now")
    atr_pct = swing_atr / ref * 100 if swing_atr and ref and not math.isnan(swing_atr) else None
    fit = horizon_fit(view or {}, cls, atr_pct)
    fit["errors"] = regime_errors or None
    return {"instrument_class": cls, "horizons": out, "horizon_fit": fit, "horizons_note": None}
