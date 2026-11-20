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
- ``horizon_fit``: the regime now (trend up / down, range, turning - desk.regime_live when there is one,
  else ``classify`` here with the same shape) and so which horizons fit: a range back and forth between
  its edges suits scalp and intraday, a trend short swing and swing.

Only for a run of today (the bars now); an older run gets ``horizons`` None and says why. Everything is
bounded by TIMEOUT_SEC per piece; a piece that fails is an ``error`` on its horizon, never the run's.
"""

from __future__ import annotations

import asyncio
import logging
import math
from itertools import pairwise

from . import channels, chart_read, entry_now, horizons
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
            "defensive": ("경기방어", "방어"), "general": ("일반", "보통")}
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


# ---------------------------------------------------------------- the regime (desk.regime_live's shape)

def _adx(bars: list[dict], n: int = 14) -> float | None:
    if len(bars) < 2 * n + 1:
        return None
    tr, pdm, mdm = [], [], []
    for p, b in pairwise(bars):
        up, down = b["high"] - p["high"], p["low"] - b["low"]
        pdm.append(up if up > down and up > 0 else 0.0)
        mdm.append(down if down > up and down > 0 else 0.0)
        tr.append(max(b["high"] - b["low"], abs(b["high"] - p["close"]), abs(b["low"] - p["close"])))
    atr, pd_, md = sum(tr[:n]), sum(pdm[:n]), sum(mdm[:n])
    dxs = []
    for i in range(n, len(tr)):
        atr, pd_, md = atr - atr / n + tr[i], pd_ - pd_ / n + pdm[i], md - md / n + mdm[i]
        if atr <= 0:
            continue
        pdi, mdi = 100 * pd_ / atr, 100 * md / atr
        dxs.append(100 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi else 0.0)
    if len(dxs) < n:
        return None
    adx = sum(dxs[:n]) / n
    for dx in dxs[n:]:
        adx = (adx * (n - 1) + dx) / n
    return adx


def classify(bars: list[dict], tf: str) -> dict:
    """The regime of ``bars`` (oldest first): trend_up · trend_down (ADX 25+), range (ADX under 20 with a
    box touched at both edges), turning, unknown - the shape of desk.regime_live.classify (#694), which
    replaces this one when it is there."""
    bars = [b for b in bars if b["low"] > 0 and b["close"] > 0 and b["high"] >= b["low"]]
    out = {"regime": "unknown", "adx": None, "box": None, "basis": [], "as_of": bars[-1]["at"] if bars else None}
    adx = _adx(bars)
    if adx is None:
        out["basis"].append("봉이 부족해요")
        return out
    out["adx"] = round(adx, 1)
    last = bars[-40:]
    hi, lo = max(b["high"] for b in last), min(b["low"] for b in last)
    tr = sum(max(b["high"] - b["low"], abs(b["high"] - p["close"]), abs(b["low"] - p["close"]))
             for p, b in zip(bars[-15:], bars[-14:], strict=False)) / 14
    near = 0.5 * tr
    box = {"high": round(hi, 4), "low": round(lo, 4), "touches_high": sum(b["high"] >= hi - near for b in last),
           "touches_low": sum(b["low"] <= lo + near for b in last), "width_pct": round((hi / lo - 1) * 100, 2)}
    move = bars[-1]["close"] / bars[-21]["close"] - 1 if len(bars) > 21 else 0.0
    if adx >= 25:
        out["regime"] = "trend_up" if move > 0 else "trend_down"
        out["basis"].append(f"ADX {adx:.0f}(25 이상) · 20봉 {move * 100:+.1f}%")
    elif adx < 20 and box["touches_high"] >= 2 and box["touches_low"] >= 2:
        out["regime"], out["box"] = "range", box
        out["basis"].append(f"ADX {adx:.0f}(20 미만) · 최근 40봉 상단 {box['touches_high']}번 · 하단 "
                            f"{box['touches_low']}번 닿음(폭 {box['width_pct']}%)")
    else:
        out["regime"] = "turning"
        out["basis"].append(f"ADX {adx:.0f} — 추세도 박스도 뚜렷하지 않아요")
    return out


def _classify():
    try:
        from .regime_live import classify as theirs  # #694, when merged
        return theirs
    except ImportError:
        return classify


def horizon_fit(frames: dict[str, dict], cls: dict, atr_pct: float | None) -> dict:
    """Which horizons the regime now suits (the daily read first, else the hourly) and one sentence."""
    lead = frames.get("1d") or {}
    if lead.get("regime") in (None, "unknown"):
        lead = frames.get("60m") or lead
    regime = lead.get("regime") or "unknown"
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
    else:
        text = "장세를 판정할 봉이 부족해요."
    notes = []
    if cls["id"] in ("leverage", "inverse"):
        notes.append("레버리지 · 인버스는 오래 들수록 변동성 끌림(복리 손실)이 쌓여 짧은 기간이 유리해요")
    elif cls["id"] in ("bond", "defensive"):
        notes.append(f"{cls['label']}은 움직임이 느려 짧은 기간은 손익비가 작아요")
    if atr_pct is not None and atr_pct >= HIGH_VOL_PCT:
        notes.append(f"일봉 ATR {atr_pct:.1f}% — 변동성이 커서 스윙은 손절 폭이 넓어요")
    return {"regime": regime, "regime_ko": REGIME_KO.get(regime, regime), "best": list(best), "fit": fit,
            "sentence": " ".join([text, *[n + "." for n in notes]]), "basis": lead.get("basis") or [],
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


async def build(prices, ticker: str, plan: dict, main: str, sr_main: dict | None, *, same_day: bool) -> dict:
    """``plan``'s horizons · instrument_class · horizon_fit (a dict to merge into the plan)."""
    cls = instrument_class(ticker)
    if prices is None or not same_day:
        why = "지난 날짜의 분석이라 지금 봉으로 만든 기간별 계획은 붙이지 않았어요" if prices is not None else "시세 원천 없음"
        return {"instrument_class": cls, "horizons": None, "horizon_fit": None, "horizons_note": why}

    async def regime():
        errors: dict[str, str] = {}
        frames = {}
        fn = _classify()
        for tf, n in (("1d", 200), ("60m", 390)):
            got = await _bounded(prices.candles(ticker, tf, n), f"regime_{tf}", errors)
            if got and got.get("bars"):
                try:
                    frames[tf] = await asyncio.to_thread(fn, got["bars"], tf)
                except Exception as exc:  # noqa: BLE001 - one timeframe's read missing leaves the other
                    errors[f"regime_{tf}"] = type(exc).__name__
        return frames, errors

    rows, (frames, regime_errors) = await asyncio.gather(
        asyncio.gather(*(_one(prices, ticker, horizons.HORIZONS[h], sr_main, main) for h in ORDER)), regime())
    out = {r["horizon"]: r for r in rows}
    if main in out:
        out[main]["model"] = _model(plan)
        out[main]["source"] = "model"
    swing_atr = (out.get("swing") or {}).get("atr")
    ref = ((out.get("swing") or {}).get("map") or {}).get("price_now")
    atr_pct = swing_atr / ref * 100 if swing_atr and ref and not math.isnan(swing_atr) else None
    fit = horizon_fit(frames, cls, atr_pct)
    fit["errors"] = regime_errors or None
    return {"instrument_class": cls, "horizons": out, "horizon_fit": fit, "horizons_note": None}
