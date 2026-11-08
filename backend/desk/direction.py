"""Which way to lean (#166): a direction score from the price trend, -100 (short) to +100 (long).

The basket backtest (#144) says the desk loses most on trades against the trend - a short fade in
an uptrend averaged -0.4R - so the playbooks ask this score which side is "with" the trend and
take the other side smaller or not at all (``apply``). Everything here is arithmetic on daily
bars: the moving-average stack, the 50-day slope, the swing structure (higher highs and lows or
lower ones) and the 20-day return. Each part is kept with its value so the page and the note can
say why. Flows, the market's risk score and the news layer can join later as further parts.
"""

from __future__ import annotations

from . import horizons

MIN_BARS = 60
LEAN = 30          # |score| from here: one side is "with" the trend
STRONG = 60        # "strong" on the page and in the note
SWING_K = 3
# #166 stage 2: "market" is desk.risk_regime's selling score (reproduced in the backtest, swept),
# "macro" the ticker's own surging news theme (live only - the backtest cannot replay it). Both are
# shown as evidence at weight 0: swing basket 2019-, costs in, gated, train / held-out (2024-05-17):
# market 0 -0.1372R / -0.1175R, 0.5 -0.1368 / -0.1169, 1.0 -0.1361 / -0.1176, 1.5 -0.1359 / -0.1178 -
# no held-out gain. "macro" cannot be measured, so it must not move which shorts the desk drops.
WEIGHTS = {"ma_stack": 1.5, "slope50": 1.0, "structure": 1.0, "ret20": 0.5, "market": 0.0, "macro": 0.0}
VALIDATED = ("ma_stack", "slope50", "structure", "ret20", "market")   # the parts the backtest measured
MARKET_NEUTRAL = 25.0   # a selling score here votes neither way; 75 is a full short vote
HYSTERESIS = 0          # keep an earlier lean this far under LEAN (10 was swept: -0.1361R / -0.1176R vs
                        # -0.1359R / -0.1168R without - it dropped more shorts that were not worse: off)
TURN_BARS = 5           # the lean this many sessions ago, for the hysteresis and the turn
MACRO_SPIKE = 1.5
APPLY = True       # set False to re-measure the counter-trend rows (desk.backtest by_direction)
APPLY_HORIZONS = ("swing",)   # the backtest that decided the rule ran on swing; other horizons are only marked


def _sma(values: list[float], n: int) -> float | None:
    return sum(values[-n:]) / n if len(values) >= n else None


def _clip(x: float) -> float:
    return max(-1.0, min(1.0, x))


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def _pivots(values: list[float], high: bool) -> list[int]:
    """Swing points, strict on the left: of two equal highs a few bars apart only the first counts
    (a plateau is one swing, not a lower high)."""
    k = SWING_K
    if high:
        return [i for i in range(k, len(values) - k)
                if values[i] > max(values[i - k:i]) and values[i] >= max(values[i + 1:i + k + 1])]
    return [i for i in range(k, len(values) - k)
            if values[i] < min(values[i - k:i]) and values[i] <= min(values[i + 1:i + k + 1])]


def _market_part(market: dict | None) -> dict | None:
    """desk.risk_regime.score as a vote: calm market leans long a little, a dump short. Once the
    dump shows exhaustion it no longer votes short (the turn is what a dip catch waits for)."""
    if not market or market.get("score") is None:
        return None
    value = _clip(-(market["score"] - MARKET_NEUTRAL) / (75 - MARKET_NEUTRAL))
    if market.get("phase") == "exhausting":
        value = max(value, 0.0)
    return {"id": "market", "value": round(value, 2),
            "text": f"시장 투매 점수 {market['score']}({market.get('label') or market.get('phase')})"}


def _macro_part(macro: dict | None) -> dict | None:
    """The ticker's directly relevant theme, fresh and surging: its tone is the vote. The headlines
    stay untrusted input - only the counted tone and spike are used here."""
    for row in (macro or {}).get("themes") or []:
        if row.get("relevance") == 2 and not row.get("stale") and row.get("tone") is not None \
                and (row.get("spike") or 0) >= MACRO_SPIKE:
            value = _clip(row["tone"] / 2) * min(1.0, row["spike"] / 3)
            if value:
                return {"id": "macro", "value": round(value, 2),
                        "text": f"{row.get('label') or row['id']} 뉴스 급증 {row['spike']:.1f}배 · 톤 {row['tone']:+.1f}"}
    return None


def score(daily: list[dict], market: dict | None = None, macro: dict | None = None) -> dict | None:
    """{"score", "bias": long|short|neutral, "strong", "turning", "held", "parts": [...]} or None
    with too few bars. ``market`` (desk.risk_regime.score) and ``macro`` (desk.macro_news brief)
    join as parts when given. The lean has a hysteresis against the price-only lean TURN_BARS ago:
    it is kept until the score falls HYSTERESIS under LEAN, and a score that crossed zero since then
    is ``turning`` (fresh entries need a stated reason)."""
    now = _score(daily, market, macro)
    if now is None:
        return None
    before = _score(daily[:-TURN_BARS])
    if before:
        keep = LEAN - HYSTERESIS
        if now["bias"] == "neutral" and before["bias"] != "neutral" \
                and (now["score"] >= keep if before["bias"] == "long" else now["score"] <= -keep):
            now["bias"], now["held"] = before["bias"], True
        now["turning"] = _sign(now["score"]) * _sign(before["score"]) < 0 and abs(before["score"]) >= LEAN / 2
        now["before"] = before["score"]
    return now


def _score(daily: list[dict], market: dict | None = None, macro: dict | None = None) -> dict | None:
    if len(daily) < MIN_BARS:
        return None
    closes = [b["close"] for b in daily]
    close = closes[-1]
    sma20, sma50, sma200 = _sma(closes, 20), _sma(closes, 50), _sma(closes, 200)
    parts = []

    # a tie votes neither way: a halted or flat stock is no downtrend
    votes = [_sign(close - sma20), _sign(close - sma50), _sign(sma20 - sma50)] + ([_sign(sma50 - sma200)] if sma200 else [])
    stack = sum(votes) / len(votes)
    parts.append({"id": "ma_stack", "value": round(stack, 2),
                  "text": f"이평 배열 {'위' if stack > 0 else '아래' if stack < 0 else '혼조'}"
                          f"(종가 {'>' if close > sma20 else '<'} 20일선, 20일선 {'>' if sma20 > sma50 else '<'} 50일선)"})

    then = sum(closes[-70:-20]) / 50 if len(closes) >= 70 else None
    if then:
        change = sma50 / then - 1
        parts.append({"id": "slope50", "value": round(_clip(change / 0.05), 2),
                      "text": f"50일선 20일 기울기 {change * 100:+.1f}%"})

    window = daily[-90:]
    highs, lows = [b["high"] for b in window], [b["low"] for b in window]
    ph, pl = _pivots(highs, True), _pivots(lows, False)
    if len(ph) >= 2 and len(pl) >= 2:
        legs = _sign(highs[ph[-1]] - highs[ph[-2]]) + _sign(lows[pl[-1]] - lows[pl[-2]])
        value = legs / 2                               # +1 higher highs and lows, -1 lower, 0 mixed or equal
        # a close through the last swing high / low says more than the old swings
        if close > highs[ph[-1]]:
            value = max(value, 0.5)
        elif close < lows[pl[-1]]:
            value = min(value, -0.5)
        parts.append({"id": "structure", "value": value,
                      "text": "고점 · 저점 높이는 중" if value > 0.5 else "고점 · 저점 낮추는 중" if value < -0.5
                      else "직전 고점 돌파" if value >= 0.5 and close > highs[ph[-1]]
                      else "직전 저점 이탈" if value <= -0.5 and close < lows[pl[-1]]
                      else "고점 · 저점 엇갈림"})

    if len(closes) > 20:
        ret = close / closes[-21] - 1
        parts.append({"id": "ret20", "value": round(_clip(ret / 0.10), 2), "text": f"20일 수익률 {ret * 100:+.1f}%"})

    parts += [p for p in (_market_part(market), _macro_part(macro)) if p]
    for p in parts:
        p["validated"] = p["id"] in VALIDATED
        p["weight"] = WEIGHTS[p["id"]]           # 0: shown as evidence, not counted in the score
    total = sum(WEIGHTS[p["id"]] for p in parts)
    value = round(100 * sum(WEIGHTS[p["id"]] * p["value"] for p in parts) / total) if total else 0
    bias = "long" if value >= LEAN else "short" if value <= -LEAN else "neutral"
    return {"score": value, "bias": bias, "strong": abs(value) >= STRONG, "turning": False, "held": False,
            "parts": parts}


def side_vs(direction: dict | None, side: str) -> str:
    """with | against | neutral for one playbook side."""
    if not direction or direction["bias"] == "neutral":
        return "neutral"
    return "with" if (direction["bias"] == "long") == (side == "long") else "against"


def apply(pb: dict, direction: dict | None, h: horizons.Horizon, empty=None) -> dict:
    """Mark each side with / against the trend; with APPLY a SHORT against an uptrend (score
    >= LEAN) is dropped. A long against a downtrend is only marked. The basket backtest (swing, 6
    tickers, 2019-, costs in) decided it: counter-trend shorts averaged -0.38R, and even halved
    ones (score 30-60) -0.23R, while counter-trend dip catches made -0.10R, better than the average
    plan - catching the crash is what the long is for. Dropping them took the whole book from
    -0.177R to -0.143R (the validation 30% from -0.200R to -0.151R). Only on APPLY_HORIZONS (swing):
    elsewhere the sides are marked, not changed. ``empty(reason)`` makes the empty side (desk.playbook
    passes its own, so the page keeps one side shape). Pure: returns a new dict."""
    if not pb:
        return pb
    out = {**pb, "direction": direction}
    for name in ("long", "short"):
        side = out.get(name)
        if not side:
            continue
        rel = side_vs(direction, name)
        side = {**side, "direction": rel}
        if direction and direction.get("turning"):
            side["turning"] = True             # the lean crossed zero lately: a fresh entry needs a reason
        if APPLY and h.id in APPLY_HORIZONS and rel == "against" and name == "short"                 and side.get("setup") not in (None, "none"):
            reason = f"방향 판정 롱 우세({direction['score']:+d}) — 상승 추세를 거스르는 숏은 쉼"
            side = {**(empty(reason) if empty else _none(side, h)), "direction": rel, "reason": reason}
        out[name] = side
    return out


def _none(side: dict, h: horizons.Horizon) -> dict:
    return {"setup": "none", "reason": None, "size_hint": side.get("size_hint"), "flat_by_close": h.flat_by_close,
            "zone": None, "entries": [], "avg_entry": None, "stop": None, "risk": None, "targets": [],
            "time_stop": None, "conditions": [], "score": 0, "source": {}, "model": None}


def prompt_note(direction: dict | None) -> str | None:
    if not direction:
        return None
    word = {"long": "LONG-leaning", "short": "SHORT-leaning", "neutral": "neutral"}[direction["bias"]]
    why = "; ".join(f"{p['id']} {p['value']:+.2f}" + ("" if p.get("validated", True) else " (live only, unvalidated)")
                    + (" (context, not in the score)" if p.get("weight", 1) == 0 else "")
                    for p in direction["parts"])
    return (f"Direction (desk: price trend, market selling score, news): {word} {direction['score']:+d}"
            + (" (strong)" if direction["strong"] else "")
            + (" (kept from the earlier lean)" if direction.get("held") else "")
            + (f" - TURNING: crossed zero from {direction['before']:+d} in {TURN_BARS} sessions, a fresh entry"
               " either way needs a stated reason" if direction.get("turning") else "")
            + f" - {why}. A plan against this direction needs a "
            "stated reason (a crash low to catch, an exhausted move); the desk drops shorts against an uptrend.")
