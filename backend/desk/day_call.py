"""Today's direction call (#572): the Korean (KOSPI) and US (S&P 500) session, open to close, in five
ordered stages with a probability each - a numeric model, no LLM - and how it scored.

Stages. ``z = (close - open) / ATR14`` of the index that day (Wilder's 14-day ATR over the days
before): ``|z| <= 0.3`` flat (약보합), up to 1 weak, beyond 1 strong, long above zero and short below.

Rounds. Two calls a session, each at a fixed cutoff (KR 07:30 · 09:30 KST, US 09:00 · 10:00 ET). A
feature may read a bar only if that bar's session had closed by the cutoff (``cut``); the second round
adds the session's opening gap (the open is printed by then). Values with no history (KIS night
futures, community mood, VKOSPI) are not features: the API shows them as context, outside the model.

Model. A location-scale ordinal logistic: ``mu = b0 + b.x``, ``s = exp(g0 + g.x)`` and
``P(z <= e) = logistic((e - mu) / s)`` read at the fixed edges, blended with the training stage
frequencies. desk.day_call_train fits it (numpy, offline, like the backtests); this module only reads
the committed model (desk/data/day_call/model_<market>.json) in plain Python - the server never trains.

A call is arithmetic on price history, not a forecast to trade on: its record (hit rates, Brier,
calibration, against the baselines) is shown with it.
"""

from __future__ import annotations

import json
import math
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

from . import market_calendar

KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")
DATA = Path(__file__).parent / "data" / "day_call"

# ascending z: index 0 is the strongest fall
STAGES = ("strong_short", "weak_short", "flat", "weak_long", "strong_long")
LABEL_KO = {"strong_long": "강한 롱", "weak_long": "약한 롱", "flat": "약보합", "weak_short": "약한 숏",
            "strong_short": "강한 숏"}
DISPLAY_ORDER = ("strong_long", "weak_long", "flat", "weak_short", "strong_short")
EDGES = (0.3, 1.0)          # |z| edges: flat, then strong
ATR_DAYS = 14
ATR_WINDOW = 120            # bars an ATR is read over

# when a daily bar's session is over (its exchange, local close). FX trades round the clock: its bar
# (dated at London midnight) is over a day after it starts.
CLOSES = {"^KS11": (KST, time(15, 30)), "^GSPC": (ET, time(16, 0)), "^IXIC": (ET, time(16, 0)),
          "^VIX": (ET, time(16, 15)), "^TNX": (ET, time(17, 0)), "DX-Y.NYB": (ET, time(17, 0))}
FX = {"KRW=X"}
# 60-minute futures bars ("<symbol>@60m"): over an hour after they start. Yahoo keeps about two years.
HOURLY = ("ES=F@60m", "NQ=F@60m")
FLOW = "flow:KOSPI"         # desk.flow_history rows {day: {investor: net KRW}}: a day's row is final that evening
FLOW_WINDOW = 20
EXTRAS = ("flow", "night")  # optional feature groups: investor flow (KR), overnight futures (both)
# The calls shown side by side (#572, the user's choice after the hold-out comparison): name -> (answer,
# feature groups). prev_close: today's close against yesterday's with the overnight futures - the one
# with an edge; prev_close_plain stands in when the hourly futures are missing (a Korean Monday morning:
# no futures over the weekend); open: open -> close, "판단 보류" under HOLD_MARGIN.
CALLS = {"prev_close": ("cc", ("night",)), "prev_close_plain": ("cc", ()), "open": ("oc", ())}
CALL_KO = {"prev_close": "전일 종가 대비", "open": "시가 대비"}
HOLD_MARGIN = 10.0          # %p between P(long) and P(short) an open -> close call needs; under it, 판단 보류


@dataclass(frozen=True)
class Market:
    key: str
    target: str               # the index called
    zone: ZoneInfo
    open_at: time             # the regular session's open (round 2 reads the gap after it)
    rounds: dict[int, time]   # round -> local cutoff
    peers: tuple[str, ...]    # other series the features read
    calendar: str             # desk.market_calendar market key


MARKETS = {
    "kr": Market("kr", "^KS11", KST, time(9, 0), {1: time(7, 30), 2: time(9, 30)},
                 ("^GSPC", "^IXIC", "^VIX", "KRW=X", "^TNX"), "kr"),
    "us": Market("us", "^GSPC", ET, time(9, 30), {1: time(9, 0), 2: time(10, 0)},
                 ("^KS11", "^VIX", "^TNX", "DX-Y.NYB"), "us"),
}
SYMBOLS = tuple(sorted({m.target for m in MARKETS.values()} | {p for m in MARKETS.values() for p in m.peers}))
ROUND_KO = {1: "1차", 2: "2차"}


# ------------------------------------------------------------------ bars and the cutoff

def session_day(symbol: str, bar) -> date:
    """The trading day a daily bar belongs to (Yahoo dates it at local midnight)."""
    at = datetime.fromisoformat(bar.at)
    zone = CLOSES[symbol][0] if symbol in CLOSES else ET if symbol in HOURLY else ZoneInfo("Europe/London")
    return at.astimezone(zone).date()


def closed_at(symbol: str, bar) -> datetime:
    """When the bar's session was over: no feature may read it before."""
    if symbol in HOURLY:
        return datetime.fromisoformat(bar.at) + timedelta(hours=1)
    if symbol in FX:
        return datetime.fromisoformat(bar.at) + timedelta(days=1)
    zone, close = CLOSES[symbol]
    return datetime.combine(session_day(symbol, bar), close, zone)


_INDEX: dict[tuple, tuple[Sequence, list[datetime], dict[date, int]]] = {}


def _index(symbol: str, bars: Sequence) -> tuple[list[datetime], dict[date, int]]:
    """Each bar's close time and the position of each session day, kept per series (the backtest asks
    for the same bars thousands of times). The entry holds the list itself, so its id cannot be reused
    by another list while it is cached; a series is not changed in place once read."""
    key = (symbol, id(bars), len(bars))
    got = _INDEX.get(key)
    if got is None or got[0] is not bars:
        if len(_INDEX) > 64:
            _INDEX.clear()
        got = _INDEX[key] = (bars, [closed_at(symbol, b) for b in bars],
                             {session_day(symbol, b): i for i, b in enumerate(bars)})
    return got[1], got[2]


def cut(symbol: str, bars: Sequence, cutoff: datetime) -> list:
    """The bars whose session had closed by ``cutoff`` (oldest first) - the one leakage rule."""
    closes, _days = _index(symbol, bars)
    return [b for b in bars[:bisect_right(closes, cutoff)] if b.close]


def on_day(symbol: str, bars: Sequence, day: date):
    """The bar of session ``day``, or None."""
    i = _index(symbol, bars)[1].get(day)
    return None if i is None else bars[i]


def cutoff_of(market: str, day: date, round_: int) -> datetime:
    m = MARKETS[market]
    return datetime.combine(day, m.rounds[round_], m.zone)


def atr(bars: Sequence, n: int = ATR_DAYS) -> float | None:
    """Wilder's ATR at the last of ``bars`` (oldest first), over the last ATR_WINDOW of them: the same
    number however much history was fetched (a live call and the backtest agree)."""
    bars = bars[-(ATR_WINDOW + 1):]
    if len(bars) < n + 1:
        return None
    trs = [max(b.high - b.low, abs(b.high - p.close), abs(b.low - p.close)) for p, b in pairwise(bars)]
    value = sum(trs[:n]) / n
    for tr in trs[n:]:
        value = (value * (n - 1) + tr) / n
    return value


def stage_of(z: float, edges: tuple[float, float] = EDGES) -> str:
    flat, strong = edges
    if z > strong:
        return "strong_long"
    if z > flat:
        return "weak_long"
    if z >= -flat:
        return "flat"
    if z >= -strong:
        return "weak_short"
    return "strong_short"


# ------------------------------------------------------------------ features

# name -> (Korean label, how the value reads, whether a higher value is "long")
FEATURES = {
    "t_ret1": ("전일 등락", "atr"), "t_ret5": ("5일 흐름", "atr"), "t_ret20": ("20일 흐름", "atr"),
    "t_ma20": ("20일선 이격", "atr"), "t_oc1": ("전일 시가→종가", "atr"), "t_rv": ("최근 변동성(5일/20일)", "ratio"),
    "t_atrp": ("ATR 수준", "pct"), "us_ret1": ("전일 S&P500", "atr"), "nq_ret1": ("전일 나스닥", "atr"),
    "kospi_ret1": ("오늘 코스피", "atr"), "vix_lvl": ("VIX 수준", "level"), "vix_chg": ("VIX 변화", "logpct"),
    "krw_chg": ("원/달러 변화", "pct100"), "tnx_chg": ("미 10년물 금리 변화", "bp"), "dxy_chg": ("달러인덱스 변화", "pct100"),
    "expiry": ("만기일", "flag"), "monday": ("월요일", "flag"), "gap": ("오늘 시가 갭", "atr"),
    "frn1": ("전일 외국인 순매수", "flow"), "frn5": ("5일 외국인 순매수", "flow"), "inst1": ("전일 기관 순매수", "flow"),
    "es_night": ("밤사이 S&P 선물", "atr"), "nq_night": ("밤사이 나스닥 선물", "atr"),
}


def _logret(bars: Sequence, n: int) -> float:
    return math.log(bars[-1].close / bars[-1 - n].close)


def _rv(bars: Sequence, n: int) -> float:
    rets = [math.log(b.close / a.close) for a, b in zip(bars[-n - 1:-1], bars[-n:])]
    mean = sum(rets) / n
    return math.sqrt(sum((r - mean) ** 2 for r in rets) / (n - 1))


# KRX opens at 10:00 on the year's first session and on the 수능 (CSAT) day: the 09:30 round has no
# open yet. CSAT days (Ministry of Education; 2017 was put off a week by the Pohang earthquake, 2020 by
# COVID-19) - add each year's when it is announced.
CSAT_DAYS = {date(2017, 11, 23), date(2018, 11, 15), date(2019, 11, 14), date(2020, 12, 3), date(2021, 11, 18),
             date(2022, 11, 17), date(2023, 11, 16), date(2024, 11, 14), date(2025, 11, 13), date(2026, 11, 19)}


def kr_late_open(day: date) -> bool:
    """A KRX session that opens at 10:00: the year's first one, or the CSAT day."""
    if day in CSAT_DAYS:
        return True
    first = date(day.year, 1, 1)
    while not market_calendar.is_open("kr", first):
        first += timedelta(days=1)
    return day == first


def _expiry(market: str, day: date) -> bool:
    return any(e["date"] == day.isoformat() and e["market"] == market
               for e in market_calendar.expiries(day.year, day.month))


class NoData(ValueError):
    """Not enough finished bars before the cutoff for this call."""


def _flow(series: dict, day: date) -> dict[str, float]:
    """Foreign and institutional net buying of the KOSPI on the sessions before ``day``, each over the
    mean absolute day of the last FLOW_WINDOW (a row is final the evening of its day)."""
    rows = series.get(FLOW) or {}
    days = [d for d in sorted(rows) if d < day.isoformat()][-FLOW_WINDOW:]
    if len(days) < FLOW_WINDOW:
        raise NoData(f"flow: {len(days)} days before {day}")

    def net(d: str, who: str) -> float:
        return float(rows[d].get(who) or 0)

    scale = {w: (sum(abs(net(d, w)) for d in days) / len(days)) or 1.0 for w in ("foreigner", "institution")}
    return {"frn1": net(days[-1], "foreigner") / scale["foreigner"],
            "frn5": sum(net(d, "foreigner") for d in days[-5:]) / (5 * scale["foreigner"]),
            "inst1": net(days[-1], "institution") / scale["institution"]}


def _night(series: dict, symbol: str, since: datetime, cutoff: datetime) -> float:
    """The futures' move from the last hourly bar finished by ``since`` to the last one finished by
    ``cutoff`` (log), the most recent at most three hours before the cutoff."""
    bars = cut(symbol, series.get(symbol) or [], cutoff)
    if not bars or cutoff - closed_at(symbol, bars[-1]) > timedelta(hours=3):
        raise NoData(f"{symbol}: no bar near {cutoff}")
    closes, _days = _index(symbol, series[symbol])
    i = bisect_right(closes, since) - 1
    if i < 0 or since - closes[i] > timedelta(hours=6) or not series[symbol][i].close:
        raise NoData(f"{symbol}: no bar near {since}")
    return math.log(bars[-1].close / series[symbol][i].close)


def features(market: str, round_: int, day: date, series: dict[str, Sequence],
             extras: Sequence[str] = ()) -> tuple[dict[str, float], dict]:
    """The feature values for ``market``'s call on ``day`` at ``round_``'s cutoff, read only from bars
    finished by then (``cut``), and what the target's last finished bar and ATR were. ``extras`` adds
    feature groups (EXTRAS): "flow" (KR only) and "night" - the futures since the last session's close."""
    m = MARKETS[market]
    cutoff = cutoff_of(market, day, round_)
    t = cut(m.target, series[m.target], cutoff)
    if t and session_day(m.target, t[-1]) >= day:      # a session closed before its own day's cutoff: never
        t = t[:-1]
    if len(t) < 25:
        raise NoData(f"{m.target}: {len(t)} bars before {day}")
    a = atr(t)
    if not a:
        raise NoData("no ATR")
    atrp = a / t[-1].close
    x = {
        "t_ret1": _logret(t, 1) / atrp,
        "t_ret5": _logret(t, 5) / (atrp * math.sqrt(5)),
        "t_ret20": _logret(t, 20) / (atrp * math.sqrt(20)),
        "t_ma20": (t[-1].close / (sum(b.close for b in t[-20:]) / 20) - 1) / atrp,
        "t_oc1": (t[-1].close - t[-1].open) / a,
        "t_rv": math.log(max(_rv(t, 5), 1e-6) / max(_rv(t, 20), 1e-6)),
        "t_atrp": math.log(atrp * 100),
    }

    def peer(symbol: str, need: int = 2) -> list:
        bars = cut(symbol, series.get(symbol) or [], cutoff)
        if len(bars) < need:
            raise NoData(f"{symbol}: {len(bars)} bars by {cutoff}")
        return bars

    def peer_ret(symbol: str) -> float:
        bars = peer(symbol, ATR_DAYS + 2)
        pa = atr(bars)
        return _logret(bars, 1) / (pa / bars[-1].close)

    vix = peer("^VIX")
    x["vix_lvl"] = math.log(vix[-1].close)
    x["vix_chg"] = _logret(vix, 1)
    tnx = peer("^TNX")
    x["tnx_chg"] = (tnx[-1].close - tnx[-2].close) * 100       # basis points
    if market == "kr":
        x["us_ret1"] = peer_ret("^GSPC")
        x["nq_ret1"] = peer_ret("^IXIC")
        x["krw_chg"] = _logret(peer("KRW=X"), 1) * 100
    else:
        x["kospi_ret1"] = peer_ret("^KS11")
        x["dxy_chg"] = _logret(peer("DX-Y.NYB"), 1) * 100
    x["expiry"] = 1.0 if _expiry(m.calendar, day) else 0.0
    x["monday"] = 1.0 if day.weekday() == 0 else 0.0
    if round_ == 2:
        today = on_day(m.target, series[m.target], day)
        late = market == "kr" and kr_late_open(day)
        if today is None or not today.open or late or datetime.combine(day, m.open_at, m.zone) > cutoff:
            raise NoData(f"no {day} open by {cutoff}")
        x["gap"] = (today.open - t[-1].close) / a
    if "flow" in extras and market == "kr":
        x.update(_flow(series, day))
    if "night" in extras:
        zone, close = CLOSES[m.target]
        since = datetime.combine(session_day(m.target, t[-1]), close, zone)   # the last session's close
        x["es_night"] = _night(series, "ES=F@60m", since, cutoff) / atrp
        x["nq_night"] = _night(series, "NQ=F@60m", since, cutoff) / atrp
    return x, {"last_close": t[-1].close, "atr": a, "atr_pct": atrp * 100, "cutoff": cutoff}


LABELS = ("oc", "cc")      # open -> close (the call), or the previous close -> close (the gap included)


def label(market: str, day: date, series: dict[str, Sequence], kind: str = "oc",
          now: datetime | None = None) -> dict | None:
    """The session's answer: from the open (``kind`` "oc") or the previous close ("cc") to the close,
    z against the ATR of the days before, its stage. ``open`` is where the move is measured from. None
    while the session is not over at ``now`` (default: the real clock) - Yahoo's bar of a session in
    progress is not an answer."""
    m = MARKETS[market]
    bars = series[m.target]
    closes, days = _index(m.target, bars)
    i = days.get(day)
    if i is None or i == 0 or closes[i] > (now or datetime.now(ZoneInfo("UTC"))):
        return None
    before = [x for x in bars[max(0, i - ATR_WINDOW - 1):i] if x.close]
    a = atr(before)
    b = bars[i]
    start = b.open if kind == "oc" else (before[-1].close if before else None)
    if not a or not start or not b.close:
        return None
    z = (b.close - start) / a
    return {"open": start, "close": b.close, "atr": a, "z": z, "stage": stage_of(z),
            "return_pct": (b.close / start - 1) * 100}


# ------------------------------------------------------------------ the model (inference only)

def _sigmoid(t: float) -> float:
    if t >= 0:
        return 1 / (1 + math.exp(-t))
    e = math.exp(t)
    return e / (1 + e)


def raw_probs(model: dict, x: dict[str, float]) -> list[float]:
    """Stage probabilities (STAGES order, 0-1) of one row: the ordinal model blended with the
    training frequencies."""
    names = model["features"]
    v = [(x[n] - mu) / sd for n, mu, sd in zip(names, model["mean"], model["std"], strict=True)]
    mu = model["beta0"] + sum(b * a for b, a in zip(model["beta"], v, strict=True))
    s = math.exp(model["gamma0"] + sum(g * a for g, a in zip(model["gamma"], v, strict=True)))
    flat, strong = model["edges"]
    f = [_sigmoid((e - mu) / s) for e in (-strong, -flat, flat, strong)]
    p = [f[0], f[1] - f[0], f[2] - f[1], f[3] - f[2], 1 - f[3]]
    lam, clim = model["lambda"], model["climatology"]
    return [(1 - lam) * a + lam * c for a, c in zip(p, clim, strict=True)]


def percent(p: Sequence[float]) -> dict[str, float]:
    """0-1 probabilities as % with one decimal summing to exactly 100.0 (largest remainder)."""
    tenths = [max(v, 0.0) * 1000 for v in p]
    total = sum(tenths) or 1.0
    tenths = [v * 1000 / total for v in tenths]
    floor = [int(v) for v in tenths]
    for i in sorted(range(len(p)), key=lambda i: tenths[i] - floor[i], reverse=True)[:1000 - sum(floor)]:
        floor[i] += 1
    return {s: floor[i] / 10 for i, s in enumerate(STAGES)}


def direction(probs: dict[str, float]) -> str:
    """The larger of the two long and the two short stages; a tie is long (as the backtest scores)."""
    up = probs["strong_long"] + probs["weak_long"]
    down = probs["strong_short"] + probs["weak_short"]
    return "long" if up >= down else "short"


def top_stage(probs: dict[str, float]) -> str:
    """The most likely stage; a tie goes to the first in DISPLAY_ORDER (the long side)."""
    return max(DISPLAY_ORDER, key=lambda s: probs[s])


def _value_text(name: str, value: float) -> str:
    kind = FEATURES[name][1]
    if kind == "flag":
        return "예" if value else "아니오"
    if kind == "level":
        return f"{math.exp(value):.1f}"
    if kind == "pct":
        return f"{math.exp(value):.2f}%"
    if kind == "logpct":
        return f"{(math.exp(value) - 1) * 100:+.1f}%"
    if kind == "pct100":
        return f"{value:+.2f}%"
    if kind == "bp":
        return f"{value:+.1f}bp"
    if kind == "ratio":
        return f"{math.exp(value):.2f}배"
    if kind == "flow":
        return f"{value:+.2f}배(20일 평균 크기 대비)"
    return f"{value:+.2f} ATR"


def reasons(model: dict, x: dict[str, float], n: int = 5) -> list[dict]:
    """The features that moved this call most: their pull on the centre (long / short) or the spread
    (volatile / calm), in standardized units times the weight."""
    out = []
    for name, mu, sd, b, g in zip(model["features"], model["mean"], model["std"], model["beta"], model["gamma"],
                                  strict=True):
        v = (x[name] - mu) / sd
        loc, scale = b * v, g * v
        if abs(loc) >= abs(scale):
            effect, weight = ("long" if loc > 0 else "short"), abs(loc)
        else:
            effect, weight = ("volatile" if scale > 0 else "calm"), abs(scale)
        label_ko = FEATURES[name][0]
        word = {"long": "오르는 쪽", "short": "내리는 쪽", "volatile": "크게 움직이는 쪽", "calm": "조용한 쪽"}[effect]
        out.append({"key": name, "label": label_ko, "value": _value_text(name, x[name]), "effect": effect,
                    "weight": round(weight, 3), "text": f"{label_ko} {_value_text(name, x[name])} → {word}"})
    return sorted(out, key=lambda r: -r["weight"])[:n]


def score(probs: dict[str, float], answer: dict) -> dict:
    """How a call (``probs`` in %) did against the session's ``answer`` (desk.day_call.label)."""
    actual = answer["stage"]
    brier = sum((probs[s] / 100 - (1.0 if s == actual else 0.0)) ** 2 for s in STAGES)
    moved = answer["close"] - answer["open"]
    hit_dir = None if moved == 0 else (direction(probs) == ("long" if moved > 0 else "short"))
    return {"stage": actual, "label": LABEL_KO[actual], "open": answer["open"], "close": answer["close"],
            "return_pct": round(answer["return_pct"], 2), "ret_atr": round(answer["z"], 2),
            "hit_direction": hit_dir, "hit_stage": top_stage(probs) == actual, "brier": round(brier, 4)}


def load_model(market: str, root: Path = DATA) -> dict | None:
    """The committed model for ``market`` (both rounds), None when there is none or it is unreadable."""
    try:
        return json.loads((root / f"model_{market}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def verdict(probs: dict[str, float], hold_margin: float | None) -> str:
    """long / short, or "hold" (판단 보류) when the two sides are within ``hold_margin`` %p."""
    up = probs["strong_long"] + probs["weak_long"]
    down = probs["strong_short"] + probs["weak_short"]
    if hold_margin is not None and abs(up - down) < hold_margin:
        return "hold"
    return direction(probs)


def call(model_file: dict, market: str, round_: int, day: date, series: dict[str, Sequence],
         name: str = "prev_close") -> dict:
    """One call (``name`` in CALLS): the stage probabilities (%), the top stage, the verdict, the reasons
    and the day's edges in %. prev_close falls back to prev_close_plain when the hourly futures are
    missing (``fallback`` says so)."""
    calls = model_file["calls"]
    used = name
    model = calls[name]["rounds"][str(round_)]
    try:
        x, meta = features(market, round_, day, series, model.get("extras") or ())
    except NoData:
        if name != "prev_close":
            raise
        used = "prev_close_plain"
        model = calls[used]["rounds"][str(round_)]
        x, meta = features(market, round_, day, series, model.get("extras") or ())
    probs = percent(raw_probs(model, x))
    stage = top_stage(probs)
    flat, strong = model["edges"]
    base = "open" if name == "open" else "prev_close"
    return {"market": market, "date": day.isoformat(), "call": base, "call_label": CALL_KO[base],
            "measured_from": "open" if model["label"] == "oc" else "prev_close",
            "round": round_, "round_label": ROUND_KO[round_], "cutoff_at": meta["cutoff"].isoformat(),
            "stage": stage, "label": LABEL_KO[stage], "direction": direction(probs),
            "verdict": verdict(probs, HOLD_MARGIN if base == "open" else None),
            "probs": {s: probs[s] for s in DISPLAY_ORDER},
            "edges_pct": {"flat": round(flat * meta["atr_pct"], 2), "strong": round(strong * meta["atr_pct"], 2)},
            "reasons": reasons(model, x), "model_version": model_file["version"],
            "fallback": used != name, "features": x}


def today_call() -> dict | None:
    """#571's line of today's calls. Stored calls arrive with PR-B (#572: the day_calls table and its
    scheduler); until then there is nothing to say."""
    return None
