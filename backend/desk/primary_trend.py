"""The primary trend of the main indices - bull, bear, range or turning - and what to do in each (#460).

The tips (#330, desk.tips) read the rate and dollar regime; this reads the market's own long trend,
the first thing a beginner needs to know ("is this a bull market or a bear market?"). For each
index (KOSPI ``^KS11``, Nasdaq-100 ``^NDX``, S&P 500 ``^GSPC``, Philadelphia semiconductor ``^SOX``)
from Yahoo daily closes, on every day:

- four votes, +1 / 0 / -1: the close above / under the 200-day line; the 200-day line rising /
  flat / falling over ``SLOPE_N`` sessions (``SLOPE_FLAT`` % either way is flat); the 50-day line
  above / under the 200-day one (the golden / death cross); the swing structure (``PIVOT_K``-bar
  pivots, confirmed only ``PIVOT_K`` bars later - no look-ahead): higher highs and higher lows /
  lower highs and lower lows / mixed;
- the drawdown from the ``HIGH_N``-session high: ``CORRECTION`` % is a correction, ``BEAR_DD`` % a
  bear market by the usual definition.

The raw state: ``bear`` when the drawdown is past ``BEAR_DD`` and the votes are not positive, or
the votes sum to -3 or less; ``bull`` at +3 or more; ``range`` when the 200-day line is flat and
the votes are within two of zero; ``turning`` for the rest (the votes disagree: a cross, a break of
the 200-day line, a new low under a rising line). The state shown changes only after the new raw
state held ``HOLD`` sessions in a row (a hysteresis: one day's swing does not flip it; a bear past
``BEAR_DD`` needs only ``HOLD_BEAR_DD``) and ``since`` is the first of those sessions. While a new
raw state is building, ``pending`` says which and for how many sessions.

The record: on the index's own history (about ten years, ``HISTORY_BARS``) the forward ``FWD``
session return on the days each state held - median, the bad tenth (``p10``) and the share of
gains - next to all days. Measured 2026-11-17 on the four indices: a bear state was not followed by
a lower median (SOX +11.8% vs +6.8% over 60 sessions), but its bad tenth was deeper on three of
four (Nasdaq-100 -13.3% vs -7.7%). So the state describes the trend and sets the risk habits of the
guide; it is not a return forecast, and ``record_note`` says so with the numbers. The guide per
state is fixed text for a beginner. Pure functions apart from ``Cache``.
"""

from __future__ import annotations

import logging
import statistics
import threading
import time
from collections.abc import Callable

log = logging.getLogger(__name__)

Series = dict[str, list[dict]]

MARKETS = (("^KS11", "kospi", "코스피"), ("^NDX", "nasdaq100", "나스닥100"), ("^GSPC", "sp500", "S&P500"),
           ("^SOX", "sox", "필라델피아 반도체"))
SYMBOLS = tuple(m[0] for m in MARKETS)
HISTORY_BARS = 2600          # about ten years: the states' record
SMA_LONG, SMA_MID = 200, 50
SLOPE_N = 20                 # sessions the 200-day line's slope is measured over
SLOPE_FLAT = 0.5             # % of the line over SLOPE_N: within it the line is flat
HIGH_N = 252                 # the high the drawdown is measured from (about a year)
CORRECTION, BEAR_DD = -10.0, -20.0
PIVOT_K = 5                  # a swing high / low is the extreme of K bars either side
STRUCTURE_N = 120            # pivots older than this many sessions do not count
# Sessions a new raw state must hold before the shown state changes. Ten years of the four indices
# (live Yahoo, 2026-11-17), state changes a year: 3 sessions 6.7-9.4, 5 5.6-8.1, 10 4.0-5.7, 15 2.6-4.4,
# 20 1.8-3.3. A primary trend changes a few times a year: 15 (three weeks) - 20 lags a month.
HOLD = 15
# A fall past BEAR_DD from the year's high is a bear market by the usual definition: that bear state
# shows after this many sessions, not HOLD (Philadelphia semis 34% off its high still read "bull" for
# three weeks at HOLD).
HOLD_BEAR_DD = 3
FWD = (20, 60)               # forward sessions of the record
STATES = ("bull", "bear", "range", "turning")
STATE_KO = {"bull": "대세 상승장", "bear": "대세 하락장", "range": "박스장", "turning": "전환 중"}


def _sma(closes: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    total = 0.0
    for i, c in enumerate(closes):
        total += c
        if i >= n:
            total -= closes[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


def _pivots(values: list[float], high: bool) -> list[tuple[int, int]]:
    """(confirmed at, pivot index) of the swing highs or lows, oldest first."""
    out = []
    k = PIVOT_K
    for j in range(k, len(values) - k):
        win = values[j - k:j + k + 1]
        if values[j] == (max(win) if high else min(win)):
            if out and out[-1][1] >= j - k:      # a flat top: the first bar of it
                continue
            out.append((j + k, j))
    return out


def _structure(highs: list[tuple[int, int]], lows: list[tuple[int, int]], hv: list[float], lv: list[float],
               i: int) -> int:
    """+1 higher high and higher low, -1 lower high and lower low, 0 otherwise, on pivots confirmed
    by day ``i`` and not older than STRUCTURE_N."""
    def last_two(piv, vals):
        got = [vals[j] for at, j in piv if at <= i and j >= i - STRUCTURE_N]
        return got[-2:] if len(got) >= 2 else None
    h, lo = last_two(highs, hv), last_two(lows, lv)
    if not h or not lo:
        return 0
    if h[1] > h[0] and lo[1] > lo[0]:
        return 1
    if h[1] < h[0] and lo[1] < lo[0]:
        return -1
    return 0


def _raw(votes: dict, dd: float) -> str:
    s = sum(votes.values())
    if dd <= BEAR_DD and s <= 0:
        return "bear"
    if s >= 3:
        return "bull"
    if s <= -3:
        return "bear"
    if votes["slope"] == 0 and abs(s) <= 2:
        return "range"
    return "turning"


def hysteresis(raws: list[str], days: list[str], holds: list[int] | None = None) -> list[tuple[str, str, int]]:
    """(state shown, since, sessions the raw state has held) per day: the shown state changes only
    once a new raw state held that day's ``holds`` sessions (HOLD by default), and dates from the
    first of them."""
    holds = holds or [HOLD] * len(raws)
    out: list[tuple[str, str, int]] = []
    shown = since = run_state = run_start = None
    run_len = 0
    for raw, day, hold in zip(raws, days, holds):
        if raw == run_state:
            run_len += 1
        else:
            run_state, run_start, run_len = raw, day, 1
        if shown is None:
            shown, since = raw, day
        elif raw != shown and run_len >= hold:
            shown, since = raw, run_start
        out.append((shown, since, run_len))
    return out


def daily_states(rows: list[dict]) -> list[dict]:
    """Every day from the 200-day line on: the votes, the drawdown, the raw state and the state
    shown (with the hysteresis) and since when."""
    rows = [r for r in rows if r.get("close") is not None]
    closes = [float(r["close"]) for r in rows]
    highs_v = [float(r.get("high") or r["close"]) for r in rows]
    lows_v = [float(r.get("low") or r["close"]) for r in rows]
    long_, mid = _sma(closes, SMA_LONG), _sma(closes, SMA_MID)
    ph, pl = _pivots(highs_v, True), _pivots(lows_v, False)
    out: list[dict] = []
    for i in range(len(closes)):
        if long_[i] is None or i < SLOPE_N or long_[i - SLOPE_N] is None:
            continue
        slope = (long_[i] / long_[i - SLOPE_N] - 1) * 100
        votes = {"above": 1 if closes[i] > long_[i] else -1,
                 "slope": 1 if slope > SLOPE_FLAT else -1 if slope < -SLOPE_FLAT else 0,
                 "cross": 1 if (mid[i] or 0) > long_[i] else -1,
                 "structure": _structure(ph, pl, highs_v, lows_v, i)}
        top = max(closes[max(0, i - HIGH_N + 1):i + 1])
        dd = (closes[i] / top - 1) * 100
        out.append({"i": i, "day": rows[i]["day"], "close": closes[i], "sma200": long_[i], "sma50": mid[i],
                    "slope": slope, "votes": votes, "drawdown": dd, "raw": _raw(votes, dd)})
    holds = [HOLD_BEAR_DD if d["raw"] == "bear" and d["drawdown"] <= BEAR_DD else HOLD for d in out]
    walked = hysteresis([d["raw"] for d in out], [d["day"] for d in out], holds)
    for d, (shown, since, run), hold in zip(out, walked, holds):
        d.update(state=shown, since=since, run=run, needs=hold)
    return out


def _last_cross(days: list[dict]) -> str | None:
    for a, b in zip(reversed(days[:-1]), reversed(days[1:])):
        if a["votes"]["cross"] != b["votes"]["cross"]:
            return b["day"]
    return None


def _pct(v: float, nd: int = 1) -> str:
    return f"{v:+.{nd}f}%"


def evidence(days: list[dict]) -> list[dict]:
    """The numbers behind the state on the last day, each with a short Korean line."""
    d = days[-1]
    v = d["votes"]
    above = (d["close"] / d["sma200"] - 1) * 100
    cross_day = _last_cross(days)
    cross = "골든크로스(50일선이 200일선 위)" if v["cross"] > 0 else "데드크로스(50일선이 200일선 아래)"
    structure = {1: "고점 · 저점을 함께 높이는 중(HH · HL)", -1: "고점 · 저점을 함께 낮추는 중(LH · LL)",
                 0: "고점 · 저점 방향이 엇갈림"}[v["structure"]]
    dd = d["drawdown"]
    dd_word = "약세장 기준(−20%) 넘음" if dd <= BEAR_DD else "조정 구간(−10% 이하)" if dd <= CORRECTION else "고점 근처"
    return [
        {"id": "above_200", "value": round(above, 2), "vote": v["above"],
         "text": f"200일선(장기 평균) {'위' if v['above'] > 0 else '아래'} {_pct(above)}"},
        {"id": "slope_200", "value": round(d["slope"], 2), "vote": v["slope"],
         "text": f"200일선 {SLOPE_N}거래일 기울기 {_pct(d['slope'], 2)}"
                 f"({'오르는 중' if v['slope'] > 0 else '내리는 중' if v['slope'] < 0 else '평평'})"},
        {"id": "cross_50_200", "value": cross_day, "vote": v["cross"],
         "text": cross + (f" — {cross_day}부터" if cross_day else "")},
        {"id": "structure", "value": v["structure"], "vote": v["structure"], "text": structure},
        {"id": "drawdown", "value": round(dd, 2), "vote": None,
         "text": f"1년 고점 대비 {_pct(dd)} — {dd_word}"},
    ]


def record(days: list[dict]) -> dict:
    """Per state: the forward FWD-session return of the index on the days it held, and all days.
    The windows of neighbouring days overlap, so ``n`` counts days, far more than independent
    samples; and HOLD was chosen on the same ten years. A description of the past, not a test."""
    closes = {d["i"]: d["close"] for d in days}
    out: dict = {}
    for n in FWD:
        fwd = [(d["state"], (closes[d["i"] + n] / d["close"] - 1) * 100) for d in days if d["i"] + n in closes]
        everything = [r for _, r in fwd]
        base = {"median": round(statistics.median(everything), 2) if everything else None,
                "p10": round(statistics.quantiles(everything, n=10)[0], 2) if len(everything) >= 10 else None}
        for state in STATES:
            got = [r for s, r in fwd if s == state]
            row = out.setdefault(state, {})
            row[str(n)] = ({"median": round(statistics.median(got), 2), "win": round(100 * sum(r > 0 for r in got) / len(got), 1),
                            "p10": round(statistics.quantiles(got, n=10)[0], 2), "n": len(got),
                            "all_median": base["median"], "all_p10": base["p10"]} if len(got) >= 20 else None)
    return {"since": days[0]["day"] if days else None, "states": out}


def record_note(name: str, state: str, rec: dict) -> str | None:
    """One plain sentence on the record of the state now: its forward 60 sessions against all days.
    The ten years of the four indices did not show a bear state followed by worse returns - the
    sentence says what the numbers say, and that the state describes the trend, it does not forecast."""
    r = (rec.get("states") or {}).get(state, {}).get("60")
    if not r:
        return None
    years = rec.get("since", "")[:4]
    return (f"{name} — {years}년부터 '{STATE_KO[state]}'였던 날 뒤 60거래일: 중간값 {_pct(r['median'])}"
            f"(모든 날 {_pct(r['all_median'])}), 나쁜 쪽 10% {_pct(r['p10'])}(모든 날 {_pct(r['all_p10'])}), "
            f"오른 비율 {r['win']:.0f}%. 국면은 지금 추세를 설명할 뿐 앞으로의 수익을 예측하지 않는다.")


def market_view(symbol: str, market: str, name: str, rows: list[dict]) -> dict | None:
    days = daily_states(rows)
    if not days:
        return None
    d = days[-1]
    rec = record(days)
    pending = ({"state": d["raw"], "state_ko": STATE_KO[d["raw"]], "sessions": d["run"], "needs": d["needs"]}
               if d["raw"] != d["state"] else None)
    return {"market": market, "name": name, "symbol": symbol, "state": d["state"], "state_ko": STATE_KO[d["state"]],
            "raw": d["raw"], "pending": pending, "since": d["since"],
            # the state held since the first judged day: it began before the record, not on that day
            "since_known": d["since"] != days[0]["day"], "as_of": d["day"], "close": round(d["close"], 2),
            "score": sum(d["votes"].values()), "evidence": evidence(days), "record": rec,
            "record_note": record_note(name, d["state"], rec)}


def view(series: Series) -> list[dict]:
    """One row per index that has enough history."""
    return [m for m in (market_view(sym, key, name, series.get(sym) or []) for sym, key, name in MARKETS) if m]


# ------------------------------------------------------------------ the guide (fixed text for a beginner)

GUIDE: dict[str, dict] = {
    "bull": {
        "title": "대세 상승장 — 추세를 따라 사는 국면",
        "summary": "추세 신호 넷 중 셋 이상이 위를 가리킨다 — 대개 지수가 200일선(약 10개월 평균) 위에 있고, 그 선이 오르며, 50일선이 200일선 위다. 오르는 흐름을 거스르지 않는 것이 기본이다.",
        "do": ["오르는 종목이 잠깐 쉬어 20일선 · 지지 구간까지 내려올 때 나눠 산다(눌림목 매수)",
               "지수보다 강한 종목(상대 강도)을 고른다",
               "수익이 난 포지션은 일부 익절하고 나머지는 손절선을 본전 위로 올려 끌고 간다(추적 손절)"],
        "dont": ["고점에서 숏(하락 베팅)을 잡지 않는다 — 데스크의 종목 단위 백테스트(#166)에서 상승 추세 종목의 숏이 가장 크게 잃었다",
                 "급등한 날 장대 양봉 끝에서 추격 매수하지 않는다",
                 "오른다고 비중을 한 번에 늘리지 않는다"],
        "size": "보통 비중(계획한 금액의 100%), 레버리지 ETF 는 계좌의 일부만",
        "stops": "손절은 지지 구간 바로 아래(평균 변동폭 ATR 1~1.5배). 1차 목표에서 절반 익절",
        "prefer": [],
        "avoid": ["fade"],
    },
    "bear": {
        "title": "대세 하락장 — 현금을 지키는 국면",
        "summary": "지수가 1년 고점에서 20% 넘게 빠졌거나, 추세 신호 넷 중 셋 이상이 아래를 가리킨다(200일선 아래 · 그 선이 내림 · 데드크로스 · 고점과 저점을 낮춤). 급반등과 급락이 번갈아 나와 출렁임이 크다. 이 국면 뒤 지수가 어땠는지는 아래 기록(중간값 · 나쁜 쪽 10%)을 직접 본다 — 시장마다 다르다.",
        "do": ["현금 비중을 늘린다(쉬는 것도 매매다)",
               "사더라도 투매(큰 폭 급락 · 거래량 급증) 뒤 바닥 확인을 보고 작게, 여러 번 나눠 산다",
               "반등 때 손실 종목을 줄이는 기회로 쓴다"],
        "dont": ["'많이 빠졌으니 싸다'는 이유만으로 물타기(평단 낮추기)하지 않는다",
                 "레버리지 ETF 를 길게 들고 있지 않는다 — 하락과 출렁임에 원금이 녹는다",
                 "20일선이 아직 평평한 얕은 눌림을 바닥으로 보지 않는다"],
        "size": "절반 이하 비중. 신규 매수는 확인 뒤에만",
        "stops": "손절을 더 짧게(ATR 1배 안팎), 계획한 손절은 반드시 지킨다. 반등 1차 목표에서 대부분 익절",
        "prefer": [],
        "avoid": ["fade"],
    },
    "range": {
        "title": "박스장 — 위아래 경계에서만 움직이는 국면",
        "summary": "200일선이 평평하고 추세 신호가 한쪽으로 모이지 않는다. 지수가 일정한 범위 안에서 오르내리는 때가 많아, 방향에 크게 베팅하기보다 범위의 위아래를 본다.",
        "do": ["박스 하단(지지 구간) 근처에서만 사고 상단(저항) 근처에서 익절한다",
               "박스를 위로 뚫으면 확인(종가 돌파 뒤 되돌림 지지) 뒤 따라간다",
               "목표가를 박스 상단 안쪽으로 짧게 잡는다"],
        "dont": ["박스 한가운데서 매수하지 않는다(위아래 어느 쪽으로도 여유가 없다)",
                 "매일 리셋되는 레버리지 ETF 를 오래 들지 않는다 — 횡보에서 가치가 깎인다",
                 "한 번의 돌파 시도에 비중을 크게 싣지 않는다(가짜 돌파가 잦다)"],
        "size": "보통 비중, 한 번에 다 사지 말고 하단에서 나눠서",
        "stops": "손절은 박스 하단 바로 아래. 박스 폭의 절반 이상은 기대하지 않는다",
        "prefer": [],
        "avoid": ["fade"],
    },
    "turning": {
        "title": "전환 중 — 방향이 바뀌는지 확인하는 국면",
        "summary": "추세 신호가 서로 엇갈린다(예: 200일선을 막 넘었거나 깼다, 50일선과 200일선이 교차했다). 새 추세인지 가짜인지 아직 모른다.",
        "do": ["비중을 줄이고 확인을 기다린다 — 200일선 위(아래)에서 며칠 버티는지 본다",
               "들고 있는 포지션은 손절선을 지키고, 이익이면 일부 익절해 위험을 줄인다",
               "다음 국면(상승 · 하락)이 정해지면 그 국면의 지침으로 바꾼다"],
        "dont": ["교차 · 돌파 하루만 보고 큰 비중으로 들어가지 않는다",
                 "양쪽(롱 · 숏)을 번갈아 뒤집지 않는다 — 데스크의 종목 단위 백테스트(#214)에서 방향이 막 바뀐 구간의 새 진입은 평소보다 나빴다"],
        "size": "절반 비중",
        "stops": "손절은 평소대로, 대신 새 진입 자체를 줄인다",
        "prefer": [],
        "avoid": ["fade"],
    },
}
SETUP_KO = {"pullback": "눌림목 매수", "range_low": "박스 하단 매수", "dip_catch": "투매 하따(급락 매수)",
            "value_swing": "과매도 스윙 매수", "fade": "반등 매도(숏)"}


# No setup is recommended per state: the desk measured its setups against each stock's own direction
# (#166), not against these index states, and those records are negative or thin (pullback with the
# trend -0.05R, range low neutral -0.07R, dip catch against -0.04R per fill). ``avoid`` keeps only the
# short fade, off in every regime (#285).
GUIDE_BASIS = ("지침은 국면마다 위험을 다루는 일반적인 습관이다. 근거로 든 백테스트는 데스크의 종목 단위 기록(#166 · #214 · #285)이고, "
               "이 지수 국면으로 잰 셋업 성적은 아직 없다.")


def guide() -> dict:
    """The guide per state, with the setups named in Korean."""
    out = {}
    for state, g in GUIDE.items():
        out[state] = {**g, "state": state, "state_ko": STATE_KO[state], "basis": GUIDE_BASIS,
                      "prefer_ko": [SETUP_KO[s] for s in g["prefer"]], "avoid_ko": [SETUP_KO[s] for s in g["avoid"]]}
    return out


class Cache:
    """The trend rows for ``ttl_sec`` (thread-safe; a failed or empty fetch is remembered briefly)."""

    def __init__(self, fetcher: Callable[[tuple[str, ...]], Series], ttl_sec: float = 3 * 3600.0,
                 fail_ttl_sec: float = 300.0, clock=time.monotonic) -> None:
        self._fetch, self.ttl, self.fail_ttl, self._clock = fetcher, ttl_sec, fail_ttl_sec, clock
        self._lock = threading.Lock()
        self._hit: tuple[float, list[dict]] | None = None

    def view(self, ahead: float = 0.0) -> list[dict] | None:
        """The rows; while another caller is fetching, the last rows at once (None before any) - a
        slow Yahoo never queues the page's requests behind it. ``ahead`` (#536, the prewarm loop):
        refetch rows that expire within that many seconds."""
        if not self._lock.acquire(blocking=False):
            return self._hit[1] if self._hit else None
        try:
            hit = self._hit
            if hit and self._clock() - hit[0] < (self.ttl if len(hit[1]) == len(MARKETS) else self.fail_ttl) - ahead:
                return hit[1]
            try:
                rows = view(self._fetch(SYMBOLS) or {})
            except Exception as exc:  # noqa: BLE001 - nothing this time, asked again soon
                log.warning("primary trend fetch failed: %s", exc)
                rows = []
            if (len(rows) < len(MARKETS) and hit and len(hit[1]) == len(MARKETS)
                    and self._clock() - hit[0] < self.ttl):
                return hit[1]                    # #536: a failed refetch-ahead keeps the rows still in date
            self._hit = (self._clock(), rows)
            return rows
        finally:
            self._lock.release()
