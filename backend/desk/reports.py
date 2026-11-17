"""#495: every few days the desk picks the subject that moved most (a major index or a favorite) and writes
a beginner report about it: a question, 3-5 numbered sections with a term box and a small price picture,
past scenes like this one (from the price history only), stages, a checklist and a closing line.

Every number comes from the desk's own calculation (the closes, the volume, RSI, the support/resistance zones,
the primary trend); the model (one call on the ChatGPT plan) only rewords the desk's draft, and a sentence
carrying a number the desk did not give is dropped (#493 ``explain`` rules). Images (#497) are not drawn here:
``sections[].image`` stays ``None``.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import math
import secrets
import statistics
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.engine import Engine

from desk import explain, indicators, market_calendar, primary_trend, runner, sr
from desk.db import favorites as favorites_t
from desk.db import now as db_now
from desk.db import reports as reports_t
from desk.db import users as users_t

log = logging.getLogger(__name__)

VERSION = 1
DAILY_BARS = 800             # about three years: the past scenes
WEEKLY_BARS = 156
WEEK = 5                     # trading days in "the last week"
AFTER = 20                   # trading days after a past scene
BASE = 60                    # the window for the prior low / high (before the last week)
MAX_FAVORITES = 8
MAX_SIMILAR = 3
KR_DONE = time(16, 30)       # the automatic report waits for the Korean close and the closing prices
RETRY_SEC = 30 * 60          # no free ChatGPT slot (a run is going): try again later
MANUAL_GAP_SEC = 10 * 60     # a manual report at most this often (each one costs a model call and a push)
MIN_SECTIONS = 3
INDEXES = tuple((sym, name) for sym, _market, name in primary_trend.MARKETS)

Fetch = Callable[[str], tuple[list[dict], list[dict]]]   # symbol -> (daily bars, weekly bars), oldest first


# ---------------------------------------------------------------- numbers

def _r(x: float | None, nd: int = 1) -> float | None:
    return None if x is None or not math.isfinite(x) else round(x, nd)


def _p(x: float | None) -> str:
    return "—" if x is None else explain._p(x)


def _pct(x: float) -> str:
    return f"{x:+.1f}%"


def _mean(xs: Iterable[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _zone_of(symbol: str) -> ZoneInfo:
    s = symbol.upper()
    return market_calendar.KST if s.startswith("^KS") or s.endswith((".KS", ".KQ")) else market_calendar.ET


def _day(at: str, zone: ZoneInfo) -> str:
    """A bar's trading day on its exchange (a KRX daily bar opens at 15:00 UTC the day before)."""
    try:
        return datetime.fromisoformat(at).astimezone(zone).date().isoformat()
    except ValueError:
        return at[:10]


def _divergence(daily: list[dict], rsis: list[float | None]) -> str | None:
    """A simple RSI divergence over the last 40 days: a lower low with a higher RSI ("bull"), or a higher
    high with a lower RSI ("bear")."""
    n = len(daily)
    if n < 45:
        return None
    lows, highs = [b["low"] for b in daily], [b["high"] for b in daily]

    def at(lo: int, hi: int, values: list[float], pick) -> int:
        return pick(range(lo, hi), key=lambda i: values[i])

    a, b = at(n - 40, n - 20, lows, min), at(n - 20, n, lows, min)
    if lows[b] < lows[a] and rsis[a] is not None and rsis[b] is not None and rsis[b] > rsis[a] + 2:
        return "bull"
    a, b = at(n - 40, n - 20, highs, max), at(n - 20, n, highs, max)
    if highs[b] > highs[a] and rsis[a] is not None and rsis[b] is not None and rsis[b] < rsis[a] - 2:
        return "bear"
    return None


def _trend(daily: list[dict]) -> tuple[str | None, bool]:
    """The primary trend now (Korean name) and whether it changed in the last two weeks."""
    rows = [{"day": b.get("day") or b["at"][:10], **{k: b[k] for k in ("open", "high", "low", "close")}} for b in daily]
    try:
        states = primary_trend.daily_states(rows)
    except Exception:  # noqa: BLE001 - too short a history: no trend
        return None, False
    if not states:
        return None, False
    now = states[-1]["state"]
    before = states[-11]["state"] if len(states) > 10 else now
    return primary_trend.STATE_KO.get(now), now != before


def _zones(daily: list[dict], weekly: list[dict], close: float) -> tuple[list[dict], float | None]:
    """The support zones under the close (nearest first), the resistance zones above it, and the ATR."""
    try:
        # the last weekly bar is the week still trading (reports come out on weekdays): finished weeks only
        out = sr.compute(daily[-200:], weekly[-WEEKLY_BARS - 1:-1], None, weekly_complete=True, horizon="swing")
    except ValueError:
        return [], None
    levels = []
    for lv in out.get("levels") or []:
        lo, hi = sorted(lv["zone"])
        levels.append({"kind": lv["kind"], "low": round(lo, 2), "high": round(hi, 2),
                       "touches": lv.get("touches"), "held": lv.get("held")})
    levels.sort(key=lambda z: abs((z["low"] + z["high"]) / 2 - close))
    return levels, out.get("atr")


def similar(daily: list[dict], change: float) -> list[dict]:
    """Past days whose one-week move went the same way and at least 80% as far (and 2% or more), each one
    a month apart, with what came ``AFTER`` days later: the latest ``MAX_SIMILAR``. Prices only."""
    if abs(change) < 2:
        return []
    closes = [b["close"] for b in daily]
    out, last = [], None
    for i in range(len(closes) - 1 - AFTER - WEEK, WEEK, -1):    # newest first; the scene must have an after
        move = (closes[i] / closes[i - WEEK] - 1) * 100
        if move * change <= 0 or abs(move) < 0.8 * abs(change):
            continue
        if last is not None and last - i < AFTER:
            continue
        after = (closes[i + AFTER] / closes[i] - 1) * 100
        low = min(closes[i:i + AFTER + 1])
        out.append({"date": daily[i].get("day") or daily[i]["at"][:10], "what": f"1주 {_pct(move)}",
                    "after": f"{AFTER}거래일 뒤 {_pct(after)} (그사이 가장 낮을 때 {_pct((low / closes[i] - 1) * 100)})",
                    "move": _r(move), "after_pct": _r(after)})
        last = i
        if len(out) >= MAX_SIMILAR:
            break
    return out


def measure(symbol: str, name: str, kind: str, daily: list[dict], weekly: list[dict]) -> dict | None:
    """The desk's numbers for one subject, and its score: how much there is to explain now."""
    zone = _zone_of(symbol)
    daily = [{**b, "day": _day(b["at"], zone)} for b in daily if b.get("close")]
    if len(daily) < BASE + WEEK + 15:
        return None
    closes = [b["close"] for b in daily]
    close = closes[-1]
    week = (close / closes[-1 - WEEK] - 1) * 100
    # how unusual the week is for this subject: a 3x ETF's ordinary week must not outscore an index's big one
    past = [(closes[i] / closes[i - WEEK] - 1) * 100
            for i in range(max(WEEK, len(closes) - 1 - WEEK - 250), len(closes) - 1 - WEEK, WEEK)]
    usual_move = statistics.pstdev(past) if len(past) >= 8 else None
    week_x = abs(week) / usual_move if usual_move else abs(week) / 2
    vols = [b.get("volume") or 0 for b in daily]
    recent, usual = _mean(vols[-WEEK:]), _mean(vols[-WEEK - 20:-WEEK])
    vol_x = recent / usual if recent and usual else None
    base = daily[-BASE - WEEK:-WEEK]
    prior_low, prior_high = min(b["low"] for b in base), max(b["high"] for b in base)
    to_low, to_high = (close / prior_low - 1) * 100, (prior_high / close - 1) * 100
    rsis = indicators.rsi(closes)
    rsi_now = rsis[-1]
    div = _divergence(daily, rsis)
    trend, turned = _trend(daily)
    zones, atr = _zones(daily, weekly, close)

    score, reasons = min(week_x, 5.0), [f"1주 {_pct(week)}"]
    if usual_move and week_x >= 1.5:
        reasons.append(f"평소 한 주 움직임의 {week_x:.1f}배")
    if vol_x and vol_x >= 1.3:
        score += (vol_x - 1) * 1.5
        reasons.append(f"거래량 평소의 {vol_x:.1f}배")
    if to_low <= 3:
        score += 1.5
        reasons.append("전저점 아래" if to_low < 0 else f"전저점까지 {to_low:.1f}%")
    if to_high <= 3:
        score += 1.5
        reasons.append("전고점 위" if to_high < 0 else f"전고점까지 {to_high:.1f}%")
    if div:
        score += 1.5
        reasons.append("RSI 상승 다이버전스" if div == "bull" else "RSI 하락 다이버전스")
    if turned and trend:
        score += 2
        reasons.append(f"대세 국면이 {trend}(으)로 바뀜")
    return {"symbol": symbol, "name": name, "kind": kind, "as_of": daily[-1]["day"],
            "close": _r(close, 2), "week_pct": _r(week), "volume_x": _r(vol_x),
            "prior_low": _r(prior_low, 2), "prior_high": _r(prior_high, 2),
            "to_low_pct": _r(to_low), "to_high_pct": _r(to_high), "rsi": _r(rsi_now), "divergence": div,
            "trend": trend, "trend_turned": turned, "atr": _r(atr, 2),
            "supports": [z for z in zones if z["high"] <= close][:2],
            "resistances": [z for z in zones if z["low"] >= close][:2],
            "similar": similar(daily, week), "score": round(score, 2), "reasons": reasons}


def pick(measured: list[dict]) -> tuple[dict, list[dict]] | None:
    """The subject with the highest score, and every candidate for the "why" (highest first)."""
    got = sorted((m for m in measured if m), key=lambda m: m["score"], reverse=True)
    return (got[0], got) if got else None


# ---------------------------------------------------------------- the draft

def _zone(z: dict) -> str:
    return f"{_p(z['low'])} ~ {_p(z['high'])}"


def _record(z: dict) -> str:
    if z.get("touches") and z.get("held") is not None:
        return f" 지금까지 {z['touches']}번 닿아 {z['held']}번 버텼어요."
    return ""


def rules(f: dict) -> dict:
    """The whole report from the desk's numbers: the fallback and the model's draft."""
    name, close, week = f["name"], f["close"], f["week_pct"]
    sup, res = f["supports"], f["resistances"]
    down = week < 0
    if f["to_low_pct"] is not None and f["to_low_pct"] < 0:
        title = f"{name}, 전저점이 깨졌는데 더 내려갈까?"
    elif f["to_low_pct"] is not None and f["to_low_pct"] <= 3:
        title = f"{name}, 전저점까지 깨지면 더 내려갈까?"
    elif f["to_high_pct"] is not None and f["to_high_pct"] < 0:
        title = f"{name}, 전고점을 넘었는데 더 올라갈까?"
    elif f["to_high_pct"] is not None and f["to_high_pct"] <= 3:
        title = f"{name}, 전고점을 넘으면 더 올라갈까?"
    elif down:
        title = f"{name}, 일주일에 {abs(week):.1f}% 빠졌는데 지금은 어떻게 봐야 할까?"
    else:
        title = f"{name}, 일주일에 {week:.1f}% 올랐는데 따라가도 될까?"
    vol = f" 거래량은 평소의 {f['volume_x']:.1f}배였어요." if f.get("volume_x") else ""
    sections = [{
        "title": "지난 일주일에 무슨 일이 있었나",
        "body": f"{name}은(는) 지난 일주일 {_pct(week)} 움직여 {_p(close)}에 있어요.{vol} "
                f"앞선 석 달 남짓의 가장 낮은 곳(전저점)은 {_p(f['prior_low'])}, 가장 높은 곳(전고점)은 {_p(f['prior_high'])}이에요.",
        "highlight": f"지금 {_p(close)} · 전저점 {_p(f['prior_low'])} · 전고점 {_p(f['prior_high'])}",
        "term": {"name": "전저점 · 전고점", "explain": "앞서 가격이 가장 낮았던 곳과 가장 높았던 곳이에요. 많은 사람이 이 자리를 기억해서 다시 오면 반응이 나오기 쉬워요."},
        "chart": {"kind": "levels", "levels": [f["prior_high"], f["prior_low"]], "labels": ["전고점", "전저점"],
                  "marks": [close]}, "image": None}]
    if sup:
        z = sup[0]
        sections.append({
            "title": "받쳐 줄 수 있는 자리",
            "body": f"가장 가까운 지지 구역은 {_zone(z)}이에요.{_record(z)} 이 구역 안에서 종가가 버티면 숨을 고를 수 있어요.",
            "highlight": f"지지 구역 {_zone(z)}",
            "term": {"name": "지지 구역", "explain": "가격이 내려오다 여러 번 멈췄던 가격대예요. 한 점이 아니라 폭이 있는 띠로 봐요."},
            "chart": {"kind": "zone", "levels": [z["high"], z["low"]], "labels": ["구역 위", "구역 아래"], "marks": [close]},
            "image": None})
    if res:
        z = res[0]
        sections.append({
            "title": "막힐 수 있는 자리",
            "body": f"위로는 {_zone(z)} 저항 구역이 가까워요.{_record(z)} 이 구역을 종가로 넘기 전에는 되돌림을 생각해 두면 좋아요.",
            "highlight": f"저항 구역 {_zone(z)}",
            "term": {"name": "저항 구역", "explain": "가격이 올라가다 여러 번 밀렸던 가격대예요. 넘고 나면 거꾸로 받쳐 주는 자리가 되기도 해요."},
            "chart": {"kind": "zone", "levels": [z["high"], z["low"]], "labels": ["구역 위", "구역 아래"], "marks": [close]},
            "image": None})
    if f.get("rsi") is not None:
        div = {"bull": " 가격은 더 낮아졌는데 RSI 는 덜 낮아졌어요(상승 다이버전스). 파는 힘이 약해지고 있다는 신호일 수 있어요.",
               "bear": " 가격은 더 높아졌는데 RSI 는 덜 높아졌어요(하락 다이버전스). 사는 힘이 약해지고 있다는 신호일 수 있어요."
               }.get(f.get("divergence") or "", "")
        zone_word = "많이 팔린 쪽" if f["rsi"] <= 30 else "많이 사들인 쪽" if f["rsi"] >= 70 else "중간"
        sections.append({
            "title": "힘은 얼마나 남았나",
            "body": f"RSI 는 {f['rsi']:.1f}로 {zone_word}이에요.{div}",
            "highlight": f"RSI {f['rsi']:.1f}",
            "term": {"name": "RSI", "explain": "최근 14일 동안 오른 힘과 내린 힘을 0~100으로 나타낸 값이에요. 30 아래는 많이 팔렸고, 70 위는 많이 사들였다는 뜻이에요."},
            "chart": {"kind": "none", "levels": [], "labels": [], "marks": []}, "image": None})
    if f.get("trend"):
        turned = " 최근 2주 사이에 바뀌었어요." if f.get("trend_turned") else ""
        sections.append({
            "title": "큰 흐름은 어떤가",
            "body": f"200일 평균선과 고점 · 저점 흐름으로 본 큰 흐름은 '{f['trend']}'이에요.{turned} 큰 흐름과 같은 쪽으로 대응하면 덜 흔들려요.",
            "highlight": f"큰 흐름: {f['trend']}",
            "term": {"name": "대세 국면", "explain": "몇 달 단위로 본 시장의 큰 방향이에요. 하루 이틀 움직임보다 느리게 바뀌어요."},
            "chart": {"kind": "none", "levels": [], "labels": [], "marks": []}, "image": None})
    sim = f.get("similar") or []
    if len(sections) < MIN_SECTIONS and sim:
        sections.append({
            "title": "예전에 비슷했던 때는",
            "body": " ".join(f"{x['date']}에 {x['what']}였고, {x['after']}였어요." for x in sim),
            "highlight": f"비슷했던 때 {len(sim)}번",
            "term": {"name": "과거 비슷한 장면", "explain": "이 종목의 가격 기록에서 한 주 움직임이 지금과 비슷했던 날이에요. 같은 결과가 나온다는 뜻은 아니에요."},
            "chart": {"kind": "none", "levels": [], "labels": [], "marks": []}, "image": None})
    sections = sections[:explain.MAX_SECTIONS]

    # stages: where to watch on the way down - the nearest support, the next one (or the prior low), and
    # the line under which the view is wrong
    first = sup[0] if sup else None
    second = sup[1] if len(sup) > 1 else None
    prior_low = f["prior_low"] if f["prior_low"] is not None and f["prior_low"] < close else None
    below = [x for x in (prior_low, second["low"] if second else None, first["low"] if first else None)
             if x is not None and x < close]
    atr = f.get("atr") or close * 0.02
    stop = round((min(below) if below else close) - atr, 2)     # always under the close
    stages = []
    if first:
        stages.append({"label": "1단계", "low": first["low"], "high": first["high"],
                       "why": "가장 가까운 지지 구역이에요. 여기서 종가가 버티는지 먼저 봐요."})
    if second:
        stages.append({"label": "2단계", "low": second["low"], "high": second["high"],
                       "why": "첫 구역이 깨지면 다음으로 받쳐 줄 수 있는 구역이에요."})
    elif prior_low is not None and (not first or prior_low < first["low"]):
        stages.append({"label": "2단계", "low": prior_low, "high": prior_low,
                       "why": "앞선 전저점이에요. 여기까지 내려오면 많은 사람이 지켜봐요."})
    stages.append({"label": "최대", "low": stop, "high": stop,
                   "why": "이 아래로 종가가 내려가면 받쳐 준다는 생각이 틀린 거예요. 그때는 정리하는 쪽으로 봐요."})

    if len(sections) < MIN_SECTIONS:
        sections.append({
            "title": "틀렸다고 볼 자리",
            "body": f"종가가 {_p(stop)} 아래로 내려가면 지금 생각이 틀린 거예요. 미리 정해 두면 흔들릴 때 덜 망설여요.",
            "highlight": f"{_p(stop)} 아래면 생각을 바꿔요",
            "term": {"name": "손절 기준", "explain": "생각이 틀렸다고 인정하고 정리하는 가격이에요. 손실을 작게 묶어 두는 장치예요."},
            "chart": {"kind": "levels", "levels": [stop], "labels": ["최대"], "marks": [close]}, "image": None})
    checklist = []
    if first:
        checklist.append({"q": "종가가 지지 구역 위인가요?", "a": f"{_zone(first)} 안이나 위에서 끝나는지 봐요."})
    if res:
        checklist.append({"q": "저항을 넘었나요?", "a": f"{_zone(res[0])} 위에서 종가가 나와야 넘었다고 봐요."})
    if f.get("volume_x"):
        checklist.append({"q": "거래량이 줄었나요?", "a": f"지금은 평소의 {f['volume_x']:.1f}배예요. 거래량이 줄며 버티면 숨을 고르는 신호예요."})
    if f.get("rsi") is not None:
        checklist.append({"q": "RSI 가 어디쯤인가요?", "a": f"지금 {f['rsi']:.1f}예요. 30 아래나 70 위에서는 반대로 움직이기 쉬워요."})
    checklist.append({"q": "틀렸을 때 나갈 가격을 정했나요?", "a": f"{_p(stop)} 아래로 종가가 내려가면 생각을 바꿔요."})

    if sim:
        ups = sum(1 for s in sim if (s.get("after_pct") or 0) > 0)
        summary_sim = f" 비슷했던 지난 {len(sim)}번 가운데 {ups}번은 {AFTER}거래일 뒤 더 높았어요."
    else:
        summary_sim = ""
    summary = f"{name} 1주 {_pct(week)}, 지금 {_p(close)}.{summary_sim}"
    return {
        "version": VERSION, "title": title, "summary": summary, "hook": title, "sections": sections,
        "similar": [{"date": s["date"], "what": s["what"], "after": s["after"]} for s in sim],
        "stages": stages, "checklist": checklist[:5],
        "closing": "숫자는 모두 데스크가 계산한 값이에요. 예측이 아니라, 이런 조건이면 이렇게 대응한다는 정리예요.",
        "source": "rules",
    }


def _why(subject: dict, candidates: list[dict]) -> dict:
    return {"text": f"후보 {len(candidates)}개 가운데 {subject['name']}이(가) 가장 크게 움직였어요: {', '.join(subject['reasons'])}.",
            "candidates": [{"symbol": c["symbol"], "name": c["name"], "score": c["score"], "reasons": c["reasons"]}
                           for c in candidates]}


def _facts(f: dict) -> dict:
    return {k: v for k, v in f.items() if k not in ("score", "reasons")}


def write(subject: dict, candidates: list[dict], model: Callable[[str], str] | None = None) -> dict:
    """The report for the chosen subject: the desk's draft, reworded by ``model`` (blocking, one call) when
    given. Any failure of the model leaves the draft (``fallback``)."""
    f = _facts(subject)
    draft = rules(f)
    out = draft
    if model is not None:
        try:
            p = explain.prompt(f, draft).replace(
                "초안: ", f"제목(질문형, 짧게)과 한 줄 요약도 다시 써서 title · summary 키로 함께 답해라. "
                          f"제목: {json.dumps(draft['title'], ensure_ascii=False)} "
                          f"요약: {json.dumps(draft['summary'], ensure_ascii=False)}\n초안: ", 1)
            answer = explain._json(model(p))
            allowed = explain.allowed_numbers(f, draft)
            out = explain.merge(draft, answer, allowed)
            if answer:
                for key in ("title", "summary"):
                    good = explain.clean(answer.get(key), allowed, limit=200)
                    if good:
                        out[key] = good
                        out["source"] = "ai"
                if out["hook"] == draft["hook"] and out["title"] != draft["title"]:
                    out["hook"] = out["title"]
        except Exception as exc:  # timeout, no login, an odd answer: the desk's wording
            if runner.rate_limited(exc):
                raise                    # the run slot's back-off must see the plan's rate limit
            log.warning("report on %s: model failed (%s)", subject["symbol"], type(exc).__name__)
            out = {**draft, "fallback": type(exc).__name__}
    for s in out["sections"]:
        s.setdefault("image", None)
    return {**out, "subject": {"symbol": subject["symbol"], "name": subject["name"], "kind": subject["kind"]},
            "as_of": subject["as_of"], "why": _why(subject, candidates)}


# ---------------------------------------------------------------- data and storage

def _bars(raw) -> list[dict]:
    return [dataclasses.asdict(b) if dataclasses.is_dataclass(b) else dict(b) for b in raw]


def yahoo_fetch(symbol: str) -> tuple[list[dict], list[dict]]:
    from desk import prices

    return (_bars(prices.yahoo_candles(symbol, "1d", DAILY_BARS)),
            _bars(prices.yahoo_candles(symbol, "1w", WEEKLY_BARS)))     # the app's interval name (prices maps it)


def fake_fetch(symbol: str) -> tuple[list[dict], list[dict]]:
    """A steady made-up history for ``DESK_FAKE_RUNS`` (no network): a slow wave and a sharp last week."""
    seed = sum(map(ord, symbol))
    start = date(2023, 1, 2)
    daily, price = [], 100.0 + seed % 50
    for i in range(DAILY_BARS):
        step = 0.6 * math.sin((i + seed) / 17) + 0.25 * math.sin((i * 7 + seed) / 5)
        if i > DAILY_BARS - 6:
            step -= 1.2 + (seed % 3) * 0.4
        o = price
        price = max(5.0, price * (1 + step / 100))
        daily.append({"at": f"{start + timedelta(days=i)}T00:00:00+00:00", "open": o, "high": max(o, price) * 1.006,
                      "low": min(o, price) * 0.994, "close": price,
                      "volume": 1e6 * (1 + (2 if i > DAILY_BARS - 6 else 0) + 0.3 * math.sin(i / 3))})
    weekly = []
    for k in range(0, len(daily), 5):
        chunk = daily[k:k + 5]
        weekly.append({"at": chunk[0]["at"], "open": chunk[0]["open"], "high": max(b["high"] for b in chunk),
                       "low": min(b["low"] for b in chunk), "close": chunk[-1]["close"],
                       "volume": sum(b["volume"] for b in chunk)})
    return daily, weekly[-WEEKLY_BARS:]


def due_after(last: datetime | None, every_days: int, now: datetime) -> datetime:
    """When the next automatic report is due: ``every_days`` after the last one, after the Korean close on a
    Korean trading day (``KR_DONE``); right away (today's close, or the next) when there is none yet."""
    start = (last + timedelta(days=every_days)) if last else now
    d = start.astimezone(market_calendar.KST).date()
    for _ in range(15):                  # the first Korean trading day on or after that day
        if market_calendar.is_open("kr", d):
            break
        d += timedelta(days=1)
    return datetime.combine(d, KR_DONE, market_calendar.KST).astimezone(UTC)


class Reports:
    """Stores the reports, picks the subject and writes one at a time."""

    def __init__(self, engine: Engine, fetch: Fetch = yahoo_fetch, every_days: int = 3) -> None:
        self.engine, self.fetch, self.every_days = engine, fetch, every_days
        self.generating = False

    # storage -----------------------------------------------------------
    def list(self, limit: int = 20, before: str | None = None) -> dict:
        limit = max(1, min(limit, 50))
        q = select(reports_t.c.id, reports_t.c.created_at, reports_t.c.body).order_by(
            reports_t.c.created_at.desc(), reports_t.c.id.desc())
        rows = []
        with self.engine.connect() as conn:
            at = None
            if before:                   # an unknown cursor: nothing older (never page 1 again)
                at = conn.execute(select(reports_t.c.created_at).where(reports_t.c.id == before)).scalar()
            if at is not None:
                q = q.where(or_(reports_t.c.created_at < at,
                                and_(reports_t.c.created_at == at, reports_t.c.id < before)))
            if not before or at is not None:
                rows = conn.execute(q.limit(limit + 1)).all()
        items = [_summary(r.id, r.created_at, r.body) for r in rows[:limit]]
        return {"items": items, "generating": self.generating,
                "next_due": _iso(self.next_due()) if self.every_days > 0 else None,
                "next_before": items[-1]["id"] if len(rows) > limit else None}

    def get(self, report_id: str) -> dict | None:
        with self.engine.connect() as conn:
            r = conn.execute(select(reports_t).where(reports_t.c.id == report_id)).first()
        if r is None:
            return None
        return {**r.body, **_summary(r.id, r.created_at, r.body)}

    def last_at(self) -> datetime | None:
        with self.engine.connect() as conn:
            at = conn.execute(select(reports_t.c.created_at).order_by(reports_t.c.created_at.desc()).limit(1)).scalar()
        if at is not None and at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        return at

    def next_due(self, now: datetime | None = None) -> datetime:
        return due_after(self.last_at(), self.every_days, now or datetime.now(UTC))

    def save(self, report: dict) -> dict:
        at = db_now()
        # the time first: ids sort like the reports (the page cursor breaks a same-second tie on the id)
        rid = f"rp_{int(datetime.now(UTC).timestamp() * 1000):012x}{secrets.token_hex(4)}"
        with self.engine.begin() as conn:
            conn.execute(reports_t.insert().values(id=rid, created_at=at, symbol=report["subject"]["symbol"],
                                                   title=report["title"][:200], source=report["source"], body=report))
        return {**report, **_summary(rid, at, report)}

    # writing -----------------------------------------------------------
    def owner(self) -> int | None:
        """The desk's owner (the first account): reports are shared by every account, so only the owner's
        favorites become candidates and only the owner is notified - another account's favorites never
        show up in a report or on someone else's device."""
        with self.engine.connect() as conn:
            return conn.execute(select(users_t.c.id).order_by(users_t.c.id).limit(1)).scalar()

    def candidates(self) -> list[tuple[str, str, str]]:
        out = [(sym, name, "index") for sym, name in INDEXES]
        owner = self.owner()
        if owner is None:
            return out
        with self.engine.connect() as conn:
            rows = conn.execute(select(favorites_t.c.ticker, favorites_t.c.name)
                                .where(favorites_t.c.user_id == owner)
                                .order_by(favorites_t.c.position, favorites_t.c.ticker)).all()
        seen = {s for s, _, _ in out}
        for r in rows:
            if r.ticker not in seen and len(out) < len(INDEXES) + MAX_FAVORITES:
                out.append((r.ticker, r.name or r.ticker, "favorite"))
                seen.add(r.ticker)
        return out

    def measure_all(self) -> list[dict]:
        measured, first = [], None
        for sym, name, kind in self.candidates():
            try:
                daily, weekly = self.fetch(sym)
                m = measure(sym, name, kind, daily, weekly)
            except Exception as exc:  # noqa: BLE001 - one subject's data failing never stops the report
                log.warning("report: %s skipped (%s)", sym, type(exc).__name__)
                first = first or (sym, exc)
                continue
            if m:
                measured.append(m)
        if not measured and first:           # every subject failed: say why, so the cause shows at once
            log.warning("report: every subject failed; the first, %s: %s: %s", first[0], type(first[1]).__name__,
                        str(first[1])[:200])
        return measured

    def make(self, model: Callable[[str], str] | None = None) -> dict | None:
        """One report, written and stored (blocking): ``None`` when no subject had data."""
        got = self.choose()
        return None if got is None else self.finish(got, model)

    def choose(self) -> tuple[dict, list[dict]] | None:
        """The subject and the candidates (the price fetches; no model call)."""
        got = pick(self.measure_all())
        if got is None:
            log.warning("report: no subject had data")
        return got

    def finish(self, got: tuple[dict, list[dict]], model: Callable[[str], str] | None = None) -> dict:
        """The chosen subject's report written (one model call when ``model``) and stored."""
        report = write(*got, model=model)
        if report.get("fallback"):
            log.info("report on %s kept with the desk's wording", got[0]["symbol"])
        return self.save(report)


def _iso(at: datetime | None) -> str | None:
    if at is None:
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return at.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _summary(rid: str, at: datetime, body: dict) -> dict:
    return {"id": rid, "created_at": _iso(at), "title": body.get("title"), "summary": body.get("summary"),
            "subject": body.get("subject"), "source": body.get("source")}
