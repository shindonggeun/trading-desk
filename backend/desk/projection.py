"""The chart's look ahead (#299): a probability cone and two or three scenario paths through the
desk's own levels - not a forecast of one price.

- **Cone**: the close now, widened by the recent bar volatility times the square root of the bars
  ahead (68% and 95% bands). The widths are calibrated on past bars (``desk.projection_bt``): the
  multipliers that made the bands hold 68% / 95% on the training years, with the held-out coverage
  reported next to them.
- **Paths**: up to the nearest resistance (and the next one), down to the nearest support (and the
  next one), or staying between them. The probabilities are a driftless walk at the same volatility
  (which level is met first within the horizon) - the measured calibration of that says how far to
  trust them. A path's points carry the level's basis (volume profile, swing, trendline, pattern).
- **Note**: a short Korean explanation. The language model only words the desk's numbers; a note
  with a number the desk did not give is dropped for the desk's own sentence.

Arithmetic on price history, not a forecast.
"""

from __future__ import annotations

import itertools
import json
import math
import random
import re
from pathlib import Path

from .ttl_cache import TTLCache

STEPS = {"1d": 20, "1w": 12, "60m": 35}        # bars ahead per timeframe
SIGMA_BARS = 60                                # returns the volatility is read from
MIN_RETURNS = 20
Z95 = 1.96
SIMS = 2000
SEED = 166
MAX_LEVELS = 2                                 # levels per side a path goes through
MIN_GAP = 0.25                                 # a level nearer than this share of the 68% band's
                                               # half-width at the horizon is noise: a bar or two away
CAL_PATH = Path(__file__).parent / "data" / "projection_calibration.json"
_cal: dict | None = None


def calibration(timeframe: str) -> dict | None:
    global _cal
    if _cal is None:
        try:
            _cal = json.loads(CAL_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cal = {}
    return (_cal.get("frames") or {}).get(timeframe)


def sigma(bars: list[dict]) -> float | None:
    """Standard deviation of the last SIGMA_BARS log close-to-close returns."""
    closes = [b["close"] for b in bars[-(SIGMA_BARS + 1):] if b.get("close")]
    rets = [math.log(b / a) for a, b in itertools.pairwise(closes) if a > 0 and b > 0]
    if len(rets) < MIN_RETURNS:
        return None
    mean = sum(rets) / len(rets)
    return math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)) or None


def cone(price: float, sig: float, steps: int, mult68: float = 1.0, mult95: float = 1.0) -> list[dict]:
    out = []
    for j in range(1, steps + 1):
        s = sig * math.sqrt(j)
        out.append({"bar": j, "p50": _r(price), "p16": _r(price * math.exp(-mult68 * s)),
                    "p84": _r(price * math.exp(mult68 * s)), "p2_5": _r(price * math.exp(-Z95 * mult95 * s)),
                    "p97_5": _r(price * math.exp(Z95 * mult95 * s))})
    return out


def first_touch(price: float, up: float | None, down: float | None, sig: float, steps: int,
                sims: int = SIMS, seed: int = SEED, scale: float = 1.0) -> dict:
    """Share of driftless walks (bar volatility ``sig`` x ``scale``) that reach ``up`` first, ``down``
    first, or neither within ``steps`` bars, and the median bars to each. A side without a level is
    never reached."""
    rng = random.Random(seed)
    lu = math.log(up / price) if up else None
    ld = math.log(down / price) if down else None
    s = sig * scale
    hits = {"up": [], "down": []}
    for _ in range(sims):
        x = 0.0
        for j in range(1, steps + 1):
            x += rng.gauss(0.0, s)
            if lu is not None and x >= lu:
                hits["up"].append(j)
                break
            if ld is not None and x <= ld:
                hits["down"].append(j)
                break
    med = lambda v: sorted(v)[len(v) // 2] if v else None
    p_up, p_down = len(hits["up"]) / sims, len(hits["down"]) / sims
    return {"up": round(p_up, 3), "down": round(p_down, 3), "none": round(1 - p_up - p_down, 3),
            "bars_up": med(hits["up"]), "bars_down": med(hits["down"])}


def _r(price: float) -> float:
    return float(round(price)) if price >= 1000 else round(price, 2)


def _levels(sr: dict, price: float, gap: float = 0.0) -> tuple[list[dict], list[dict]]:
    """Distinct levels above (nearest first) and below (nearest first) the price: horizontal zones,
    then each converging pattern's two lines where they are now."""
    rows = [{"price": lv["price"], "why": (lv.get("basis") or ("저항" if lv.get("kind") == "resistance" else "지지")).split(" · ")[0],
             "strength": lv.get("strength")} for lv in sr.get("levels") or [] if lv.get("price")]
    for p in sr.get("patterns") or []:
        if p.get("state") == "converging":
            rows.append({"price": p["upper"]["to"]["price"], "why": f"{p['label']} 윗선", "strength": None})
            rows.append({"price": p["lower"]["to"]["price"], "why": f"{p['label']} 아랫선", "strength": None})
    above = sorted((r for r in rows if r["price"] > price * math.exp(gap)), key=lambda r: r["price"])
    below = sorted((r for r in rows if r["price"] < price * math.exp(-gap)), key=lambda r: -r["price"])
    dedup = lambda rs: [r for i, r in enumerate(rs) if not any(abs(r["price"] / o["price"] - 1) < 0.004 for o in rs[:i])]
    return dedup(above)[:MAX_LEVELS], dedup(below)[:MAX_LEVELS]


def project(sr: dict, bars: list[dict], timeframe: str = "1d", price: float | None = None) -> dict:
    """The cone, the paths and the desk's own Korean note for the bars' timeframe."""
    steps = STEPS.get(timeframe, 20)
    price = price or (bars[-1]["close"] if bars else None)
    sig = sigma(bars)
    cal = calibration(timeframe)
    base = {"timeframe": timeframe, "steps": steps, "price": price, "sigma": round(sig, 5) if sig else None,
            "calibration": cal, "cone": [], "paths": [], "levels": {"above": [], "below": []}}
    if not price or not sig:
        return {**base, "note_ko": "변동성을 계산할 봉이 모자라 예상도를 그리지 않습니다.", "note_source": "desk"}
    mult68, mult95 = (cal or {}).get("mult68", 1.0), (cal or {}).get("mult95", 1.0)
    above, below = _levels(sr, price, MIN_GAP * mult68 * sig * math.sqrt(steps))
    touch = first_touch(price, above[0]["price"] if above else None, below[0]["price"] if below else None,
                        sig, steps, scale=mult68)
    paths = []

    def walk(name: str, ko: str, prob: float, first_bars: int | None, levels: list[dict]) -> None:
        """``prob`` is the first level's (met first); the next level is drawn as where the walk goes on."""
        if not levels or prob <= 0:
            return
        pts, at, prev = [{"bar": 0, "price": _r(price), "why": "지금"}], 0, price
        for i, lv in enumerate(levels):
            if i == 0:
                at = first_bars or steps
            else:   # the next level: the same walk's typical time for that distance
                at = min(steps, at + max(1, round((math.log(lv["price"] / prev) / (sig * mult68)) ** 2)))
            pts.append({"bar": at, "price": _r(lv["price"]), "why": lv["why"]})
            prev = lv["price"]
            if at >= steps:
                break
        paths.append({"name": name, "label": ko, "prob": prob, "points": pts})

    walk("bull", "강세", touch["up"], touch["bars_up"], above)
    walk("bear", "약세", touch["down"], touch["bars_down"], below)
    if above and below:
        paths.append({"name": "range", "label": "범위", "prob": touch["none"],
                      "points": [{"bar": 0, "price": _r(price), "why": "지금"},
                                 {"bar": steps, "price": _r(price), "why": f"{_r(below[0]['price'])} ~ {_r(above[0]['price'])} 안"}]})
    out = {**base, "cone": cone(price, sig, steps, mult68, mult95), "paths": paths, "first_touch": touch,
           "levels": {"above": above, "below": below}}
    out["note_ko"], out["note_source"] = desk_note(out), "desk"
    return out


def fmt(price: float) -> str:
    """A price as text: thousands separated and whole from 1000 up, two decimals below."""
    return f"{round(price):,}" if price >= 1000 else f"{price:.2f}".rstrip("0").rstrip(".")


def desk_note(p: dict) -> str:
    """Three plain sentences from the numbers alone (the fallback and the model's reference)."""
    steps, cone_end = p["steps"], p["cone"][-1] if p["cone"] else None
    lines = []
    if cone_end:
        lines.append(f"{steps}봉 뒤 가격은 68% 확률로 {fmt(cone_end['p16'])} ~ {fmt(cone_end['p84'])}, "
                     f"95% 확률로 {fmt(cone_end['p2_5'])} ~ {fmt(cone_end['p97_5'])} 범위입니다(최근 변동성 기준).")
    above, below, t = p["levels"]["above"], p["levels"]["below"], p.get("first_touch") or {}
    if above and below:
        lines.append(f"위 {fmt(above[0]['price'])}({above[0]['why']})에 먼저 닿을 확률 {round(100 * t['up'])}%, "
                     f"아래 {fmt(below[0]['price'])}({below[0]['why']})에 먼저 닿을 확률 {round(100 * t['down'])}%, "
                     f"{steps}봉 안에 둘 다 안 닿을 확률 {round(100 * t['none'])}%입니다.")
    elif above or below:
        lv = (above or below)[0]
        lines.append(f"가까운 레벨은 {'위' if above else '아래'} {fmt(lv['price'])}({lv['why']}) 하나뿐입니다.")
    cal = p.get("calibration") or {}
    if cal.get("coverage68_valid") is not None:
        lines.append(f"과거 검증에서 68% 범위에 실제 가격이 든 비율은 {cal['coverage68_valid']}%였습니다. "
                     "방향 예측이 아니라 확률 범위입니다.")
    else:
        lines.append("과거 검증 기록이 없어 범위 폭은 보정되지 않았습니다. 방향 예측이 아니라 확률 범위입니다.")
    return " ".join(lines)


_NUM = re.compile(r"(?<![\d.,])-?\d[\d,]*(?:\.\d+)?%?")


def _norm(token: str) -> str:
    t = token.replace(",", "")
    if "." in t and not t.endswith("%"):
        t = t.rstrip("0").rstrip(".")
    return t


def allowed_numbers(p: dict) -> set[str]:
    """The numbers a note may contain, normalised (no commas, no trailing .0): the prices the desk
    drew (now, band edges at the horizon, levels, path points), the bar counts on the paths, and
    the percentages it computed - each percentage only with its % sign."""
    prices = {p.get("price")}
    if p.get("cone"):
        end = p["cone"][-1]
        prices |= {end["p16"], end["p84"], end["p2_5"], end["p97_5"]}
    for side in ("above", "below"):
        prices |= {lv["price"] for lv in p["levels"][side]}
    bars = {p["steps"]}
    for path in p.get("paths") or []:
        for pt in path["points"]:
            prices.add(pt["price"])
            bars.add(pt["bar"])
    t = p.get("first_touch") or {}
    pcts = {round(100 * t[k]) for k in ("up", "down", "none") if t.get(k) is not None}
    pcts |= {round(100 * path["prob"]) for path in p.get("paths") or []} | {68, 95}
    cal = p.get("calibration") or {}
    pcts |= {cal[k] for k in ("coverage68_valid", "coverage95_valid") if cal.get(k) is not None}
    out = {_norm(fmt(x)) for x in prices if x} | {_norm(str(x)) for x in prices if x}
    out |= {str(b) for b in bars}
    out |= {_norm(f"{x}") + "%" for x in pcts}
    out |= {_norm(t) for t in _NUM.findall(desk_note(p))}   # the desk's own sentences (level names: "×2")
    out |= {_norm(t) for x in p["levels"]["above"] + p["levels"]["below"] for t in _NUM.findall(x["why"])}
    return out


def checked(note: str | None, p: dict) -> str | None:
    """The model's note when every number in it is one the desk gave (a percentage only as a
    percentage, no signs), else None."""
    if not isinstance(note, str) or not note.strip() or len(note) > 600:
        return None
    allowed = allowed_numbers(p)
    for token in _NUM.findall(note):
        if token.startswith("-") or _norm(token) not in allowed:
            return None
    return note.strip()


def build_prompt(p: dict) -> str:
    paths = "; ".join(f"{x['label']} {round(100 * x['prob'])}%: " + " → ".join(f"{fmt(pt['price'])}({pt['why']}, {pt['bar']}봉)" for pt in x["points"])
                      for x in p["paths"])
    return ("너는 주식 차트 설명가다. 아래 숫자만 써서 한국어 3문장으로 설명해라. 새 숫자를 만들지 말고 숫자는 아래에 쓴 그대로 "
            "옮겨라. 예측이 아니라 확률 범위 · 시나리오라고 밝혀라. 매수 · 매도 권유는 하지 마라.\n"
            f"데스크 요약: {desk_note(p)}\n경로: {paths}")


def llm_note(prompt: str) -> str:
    """The TradingAgents provider and quick model, one attempt (as desk.news_digest). Blocking."""
    from tradingagents.default_config import build_default_config
    from tradingagents.llm_clients import create_tier_client

    llm = create_tier_client(build_default_config(), "quick", timeout=60, max_retries=0).get_llm()
    return llm.invoke(prompt).content


def fake_note(prompt: str) -> str:
    """DESK_FAKE_RUNS: the desk's own summary line, as a model that only repeats would."""
    return prompt.split("데스크 요약: ", 1)[1].split("\n", 1)[0] if "데스크 요약: " in prompt else ""


CACHE_MAX = 256
NOTE_MIN_TTL_SEC = 300.0       # a model note only for horizons cached this long (swing): no call per minute


class Projections:
    """Cached per ticker and horizon for that horizon's level TTL (desk.horizons sr_ttl_sec). The
    desk's note comes back at once; the model's wording of it is filled in afterwards, only while a
    run slot is free (#242's rule), and kept only if every number in it is the desk's (``checked``)."""

    def __init__(self, note_fn=None, clock=None) -> None:
        import time

        self._note_fn = note_fn
        self._clock = clock or time.monotonic
        self._cache: TTLCache[tuple[str, str], dict] = TTLCache(CACHE_MAX, 24 * 3600, clock=lambda: self._clock())
        self._tasks: set = set()

    async def get(self, ticker: str, horizon_id: str, ttl: float, levels, bars_for, slot=None) -> dict:
        """``levels()`` is awaited on a miss (desk.sr's result for the horizon); ``bars_for(levels)``
        then returns (bars, timeframe) - the swing frame when the levels fell back to it."""
        import asyncio

        key = (ticker, horizon_id)
        hit = self._cache.entry(key)               # each ask brings its own ttl
        if hit is not None and hit[0] < ttl:
            return dict(hit[1])
        sr_result = await levels()
        frame, timeframe = await bars_for(sr_result)
        out = await asyncio.to_thread(project, sr_result, frame, timeframe, sr_result.get("reference_price"))
        out = {**out, "ticker": ticker, "horizon": horizon_id, "note_pending": False,
               "fallback": sr_result.get("fallback")}
        if self._note_fn and out["paths"] and slot is not None and ttl >= NOTE_MIN_TTL_SEC:
            out["note_pending"] = True
            task = asyncio.create_task(self._fill(key, out, slot))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        self._cache.put(key, out, ttl)
        return dict(out)

    async def _fill(self, key: tuple[str, str], out: dict, slot) -> None:
        import asyncio
        import logging

        try:
            async with slot() as free:
                text = await asyncio.to_thread(self._note_fn, build_prompt(out)) if free else None
            good = checked(text, out)
            if good:
                out.update(note_ko=good, note_source="ai")
        except Exception as exc:  # noqa: BLE001 - the desk's note stands
            logging.getLogger(__name__).warning("projection note for %s failed: %s", key[0], type(exc).__name__)
        finally:
            out["note_pending"] = False
