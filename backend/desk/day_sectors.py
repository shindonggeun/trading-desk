"""Today's strong and weak sectors, Korea and the US apart (#620).

Each market has a sector table (``SECTORS``): a representative ETF per sector and the market's own
benchmark ETF (KODEX 200 / SPY), and a link to the other market's matching sector (US semiconductors
SOXX ↔ KODEX 반도체). A sector's day is its **excess return** - its close-to-close return minus the
benchmark's the same session, on **adjusted** closes (``yahoo_adjusted``: a distribution's ex-date drop is
no underperformance).

For a target session D the picks use only sessions finished before D's open:

- Korea (D in KST): its own sectors' sessions before D, and the linked US sectors' sessions dated before D
  (the US day D-1 closes at 06:00 KST on D - 05:00 in US summer time).
- US (D in ET): its own sectors' sessions before D, and the linked Korean sectors' session on D itself
  (Korea's D closes at 01:30 ET, before the US open) - or the one before when Korea was closed on D.

Features per sector and day (excess returns, in %): own last session (``own_1d``), last 5 and 20 sessions
(``own_5d`` · ``own_20d``), and the linked sector's last session and 5 sessions (``link_1d`` ·
``link_5d``; 0 without a link). One pooled logistic regression per market (standardized features, a
small ridge) gives each sector the probability that it beats the benchmark on D. The top three and the
bottom three are the picks; the reasons are the features that pushed the probability most, said with
their numbers - no model writes them.

Nothing is stored: every input is a finished daily bar, so a past day's picks are recomputed the same
way, and the record is the last ``HOLDOUT_DAYS`` sessions picked by a model fit only on the sessions
before them: the top three's hit rate (beat the market), the bottom three's (lagged it), the daily rank
correlation (Spearman) of probability against excess return, against the baseline "follow yesterday's
strong sectors" (rank by ``own_1d``). Today's picks use a model fit on every finished session. After the
close (``SCORE_AFTER`` past ``session_close`` - 16:30 KST on a CSAT day) today's picks are scored the same
way. Until the next session's picks can be made (its inputs not in, or a linked session still on - Yahoo's
daily bar of a session in progress moves), the view shows the last session's picks and score.

Flow by industry (KIS has none for sectors), the news themes' tone and the earnings calendar are not
in the model: they have no stored history to fit or check it on.
"""

from __future__ import annotations

import bisect
import json
import logging
import math
import threading
import time as _time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

import numpy as np

from . import market_calendar, pools, shared_cache
from .day_call import CSAT_DAYS

log = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")
HISTORY_BARS = 2400          # about ten years of daily bars per ETF
HOLDOUT_DAYS = 90            # sessions in the record, picked by a model that never saw them
PICKS = 3
RIDGE = 1.0
MIN_ROWS = 500               # fewer training rows than this: no model
SCORE_AFTER = timedelta(minutes=30)
NOT_READY_TTL_SEC = 2 * 60   # a view still waiting for a session's bars is asked again this soon
# #864 the prewarm rebuilt both views every pass (10-14 s in production): a view still waiting for a session kept
# a 2 min life that the prewarm's 300 s "ahead" always called stale, and each build read ~30 ETFs' ten years one by
# one, both markets the same ETFs. Now a view stands until the time it can change (Cache.read), the two markets
# share the bars, a rebuild later in the day reads only the last few, and the reads go side by side.
FETCH_PARALLEL = 6           # one rebuild's ETF reads at a time
BARS_FRESH_SEC = 120.0       # bars this young are used as they are (the other market's build in the same pass)
RECENT_BARS = 10             # after the day's full read, a rebuild asks only this many (merged by date)
# #993 a restart (most are deploys) read all 30 ETFs' history again: the first prewarm pass's sectors took ~9 s. The
# day's full read is now also kept in the shared tier under a version, not the build (desk.shared_cache
# *_versioned), so the next process of the day reads only the last few on top of it. The value is each ETF's bar
# times and adjusted closes - all the picks read of a bar (``_closes`` · ``_merge``). Change its shape or meaning
# (another price, another adjustment) = bump SHARED_BARS_VERSION.
SHARED_BARS = "sector_bars"
SHARED_BARS_VERSION = 1
SHARED_BARS_TTL_SEC = 24 * 3600   # read only on the KST day of its full read, which ends within a day of it
FEATURES = ("own_1d", "own_5d", "own_20d", "link_1d", "link_5d")


@dataclass(frozen=True)
class Sector:
    id: str
    name: str
    ticker: str
    link: str | None = None          # the other market's sector id


@dataclass(frozen=True)
class Market:
    id: str
    name: str
    tz: ZoneInfo
    close: time                      # the regular session's close, local time
    benchmark: str
    sectors: tuple[Sector, ...]


# ETFs checked on Yahoo 2026-11-18 (daily history from 2016-12 unless noted)
KR = Market("kr", "국장", KST, time(15, 30), "069500.KS", (
    Sector("semis", "반도체", "091160.KS", "semis"),             # KODEX 반도체
    Sector("battery", "2차전지", "305720.KS", "lithium"),         # KODEX 2차전지산업 (2018-09)
    Sector("bio", "바이오", "244580.KS", "biotech"),              # KODEX 바이오
    Sector("auto", "자동차", "091180.KS", "discretionary"),       # KODEX 자동차
    Sector("bank", "은행", "091170.KS", "financials"),            # KODEX 은행
    Sector("broker", "증권", "102970.KS", "financials"),          # KODEX 증권
    Sector("steel", "철강", "117680.KS", "materials"),            # KODEX 철강
    Sector("chem", "에너지화학", "117460.KS", "energy"),           # KODEX 에너지화학
    Sector("build", "건설", "117700.KS", "industrials"),          # KODEX 건설
    Sector("heavy", "조선 · 중공업", "139230.KS", "industrials"),  # TIGER 200 중공업
    Sector("defense", "방산", "449450.KS", "defense"),            # PLUS K방산 (2023-01)
    Sector("internet", "인터넷", "365000.KS", "comm"),            # TIGER 인터넷TOP10 (2020-10)
    Sector("software", "소프트웨어", "157490.KS", "tech"),         # TIGER 소프트웨어
))
US = Market("us", "미장", ET, time(16, 0), "SPY", (
    Sector("semis", "반도체", "SOXX", "semis"),
    Sector("tech", "기술", "XLK", "software"),
    Sector("comm", "커뮤니케이션", "XLC", "internet"),
    Sector("discretionary", "경기소비재", "XLY", "auto"),
    Sector("staples", "필수소비재", "XLP"),
    Sector("energy", "에너지", "XLE", "chem"),
    Sector("financials", "금융", "XLF", "bank"),
    Sector("health", "헬스케어", "XLV", "bio"),
    Sector("biotech", "바이오", "XBI", "bio"),
    Sector("industrials", "산업재", "XLI", "heavy"),
    Sector("materials", "소재", "XLB", "steel"),
    Sector("utilities", "유틸리티", "XLU"),
    Sector("realestate", "리츠", "XLRE"),
    Sector("lithium", "2차전지 · 리튬", "LIT", "battery"),
    Sector("defense", "방산", "ITA", "defense"),
))
MARKETS = {"kr": KR, "us": US}


def other(m: Market) -> Market:
    return US if m.id == "kr" else KR


def session_close(m: Market, day: date) -> datetime:
    """When the market's session of ``day`` ends: KRX closes an hour late on the CSAT day."""
    close = time(16, 30) if m.id == "kr" and day in CSAT_DAYS else m.close
    return datetime.combine(day, close, m.tz)


def yahoo_adjusted(ticker: str, interval: str, count: int) -> list:
    """Daily bars with closes adjusted for distributions and splits (desk.prices reads them unadjusted)."""
    import yfinance as yf

    from .broker.base import Bar
    from .prices import _yahoo_period

    with pools.yahoo_slot():                  # #874 the process's Yahoo reads at once (desk.pools.YAHOO)
        frame = yf.Ticker(ticker).history(period=_yahoo_period(interval, count, ticker), interval="1d",
                                          auto_adjust=True)
    return frame_bars(frame, count, Bar)


def frame_bars(frame, count: int, bar) -> list:
    """The last ``count`` rows of a yfinance frame with all four prices as ``bar``s (#704). Column-wise:
    ``iterrows`` built a Series per row while holding the GIL - 73% of the server's GIL time in the
    sector cache's warm-up starved the event loop. Same bars as the row loop (tests/test_day_sectors)."""
    frame = frame.dropna(subset=["Open", "High", "Low", "Close"]).tail(count)
    if frame.empty:
        return []
    idx = frame.index
    if getattr(idx, "tz", None) is not None:
        ats = idx.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%S+00:00").tolist()
    else:                                   # a naive index (not what yfinance gives): as the row loop read it
        ats = [t.to_pydatetime().astimezone(ZoneInfo("UTC")).isoformat(timespec="seconds") for t in idx]
    cols = frame[["Open", "High", "Low", "Close"]].to_numpy(dtype=float)
    return [bar(at=at, open=o, high=h, low=lo, close=c, volume=0.0)
            for at, (o, h, lo, c) in zip(ats, cols.tolist(), strict=True)]


def symbols(m: Market) -> list[str]:
    """Every ticker a market's picks read: its benchmark and sectors, and the other market's."""
    o = other(m)
    return [m.benchmark, *(s.ticker for s in m.sectors), o.benchmark, *(s.ticker for s in o.sectors)]


# ---------------------------------------------------------------- series

class Excess:
    """One ticker's excess return (%) per session over its market's benchmark, sessions in order."""

    def __init__(self, own: dict[date, float], ref: dict[date, float]) -> None:
        days = sorted(set(own) & set(ref))
        self.days: list[date] = []
        self.values: list[float] = []
        for prev, day in pairwise(days):
            a0, a1, b0, b1 = own[prev], own[day], ref[prev], ref[day]
            if a0 > 0 and b0 > 0:
                self.days.append(day)
                self.values.append(((a1 / a0) - (b1 / b0)) * 100)

    def at(self, day: date) -> float | None:
        i = bisect.bisect_left(self.days, day)
        return self.values[i] if i < len(self.days) and self.days[i] == day else None

    def before(self, day: date, n: int, inclusive: bool = False) -> tuple[list[float], date | None]:
        """The last n values before day (or up to it, ``inclusive``) and the latest one's date."""
        i = bisect.bisect_right(self.days, day) if inclusive else bisect.bisect_left(self.days, day)
        vals = self.values[max(0, i - n):i]
        return vals, (self.days[i - 1] if i else None)


def _closes(bars, tz: ZoneInfo, upto: date | None) -> dict[date, float]:
    out = {}
    for b in bars or []:
        c = getattr(b, "close", None)
        if not c or (isinstance(c, float) and math.isnan(c)):
            continue
        d = datetime.fromisoformat(b.at).astimezone(tz).date()
        if upto is None or d <= upto:
            out[d] = float(c)
    return out


@dataclass
class Data:
    """A market's excess series: its sectors' (by id), and the other market's (by id) for the links."""
    market: Market
    own: dict[str, Excess]
    link: dict[str, Excess]
    bench_days: list[date]


def build(m: Market, bars: dict[str, list], upto: date | None = None) -> Data:
    """``bars`` ticker → daily bars (oldest first). ``upto``: drop sessions after this local day (tests)."""
    o = other(m)
    bench = _closes(bars.get(m.benchmark), m.tz, upto)
    linked_bench = _closes(bars.get(o.benchmark), o.tz, None)
    own = {s.id: Excess(_closes(bars.get(s.ticker), m.tz, upto), bench) for s in m.sectors}
    link = {s.id: Excess(_closes(bars.get(s.ticker), o.tz, None), linked_bench) for s in o.sectors}
    return Data(m, own, link, sorted(bench))


def features(data: Data, sector: Sector, day: date) -> dict | None:
    """The features of a sector for session ``day`` from sessions finished before its open; None when
    its own history is too short."""
    own = data.own[sector.id]
    vals, _ = own.before(day, 20)
    if len(vals) < 20:
        return None
    f = {"own_1d": vals[-1], "own_5d": sum(vals[-5:]), "own_20d": sum(vals), "link_1d": 0.0, "link_5d": 0.0,
         "link_day": None}
    if sector.link and sector.link in data.link:
        # Korea's D reads the US sessions dated before D; the US's D reads Korea's D (closed before the US open)
        lv, last = data.link[sector.link].before(day, 5, inclusive=data.market.id == "us")
        if lv:
            f.update(link_1d=lv[-1], link_5d=sum(lv), link_day=last)
    return f


# ---------------------------------------------------------------- the model

@dataclass
class Model:
    mean: np.ndarray
    std: np.ndarray
    w: np.ndarray                    # intercept first
    rows: int
    until: date | None

    def prob(self, f: dict) -> float:
        z = (np.array([f[k] for k in FEATURES]) - self.mean) / self.std
        return float(1 / (1 + math.exp(-(self.w[0] + z @ self.w[1:]))))

    def pushes(self, f: dict) -> dict[str, float]:
        """Each feature's push on the log-odds."""
        z = (np.array([f[k] for k in FEATURES]) - self.mean) / self.std
        return {k: float(self.w[i + 1] * z[i]) for i, k in enumerate(FEATURES)}


def fit(x: np.ndarray, y: np.ndarray, ridge: float = RIDGE, until: date | None = None) -> Model:
    """Logistic regression by Newton steps (IRLS) with an L2 penalty on the slopes."""
    mean, std = x.mean(axis=0), x.std(axis=0)
    std[std == 0] = 1.0
    z = np.hstack([np.ones((len(x), 1)), (x - mean) / std])
    w = np.zeros(z.shape[1])
    pen = np.eye(z.shape[1]) * ridge
    pen[0, 0] = 0.0
    for _ in range(25):
        p = 1 / (1 + np.exp(-(z @ w)))
        grad = z.T @ (y - p) - pen @ w
        hess = (z * (p * (1 - p))[:, None]).T @ z + pen
        step = np.linalg.solve(hess, grad)
        w += step
        if np.abs(step).max() < 1e-8:
            break
    return Model(mean, std, w, len(x), until)


def rows(data: Data, days: list[date]) -> tuple[np.ndarray, np.ndarray]:
    """Training rows: every sector on every given session that has its features and its outcome."""
    xs, ys = [], []
    for day in days:
        for s in data.market.sectors:
            f = features(data, s, day)
            out = data.own[s.id].at(day)
            if f is None or out is None or out == 0:
                continue
            xs.append([f[k] for k in FEATURES])
            ys.append(1.0 if out > 0 else 0.0)
    return np.array(xs, dtype=float).reshape(-1, len(FEATURES)), np.array(ys)


def train(data: Data, before: date) -> Model | None:
    days = [d for d in data.bench_days if d < before]
    x, y = rows(data, days)
    return _fit_rows(x, y, days)


def _fit_rows(x: np.ndarray, y: np.ndarray, days: list[date]) -> Model | None:
    if len(y) < MIN_ROWS:
        return None
    return fit(x, y, until=days[-1])


# ---------------------------------------------------------------- picks and record

def picks(data: Data, model: Model, day: date) -> list[dict]:
    """Every sector with features on ``day``, most likely to beat the market first."""
    out = []
    for s in data.market.sectors:
        f = features(data, s, day)
        if f is None:
            continue
        out.append({"id": s.id, "name": s.name, "ticker": s.ticker, "prob": round(model.prob(f), 4),
                    "_f": f, "_push": model.pushes(f), "excess_pct": _round(data.own[s.id].at(day))})
    out.sort(key=lambda r: (-r["prob"], r["id"]))
    return out


def _round(x: float | None) -> float | None:
    return None if x is None else round(x, 2)


def _spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3:
        return None
    ra, rb = _ranks(a), _ranks(b)
    if np.std(ra) == 0 or np.std(rb) == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def _ranks(v: list[float]) -> np.ndarray:
    order = np.argsort(v, kind="stable")
    r = np.empty(len(v))
    r[order] = np.arange(len(v))
    return r


def day_score(rows_: list[dict], key: str = "prob") -> dict | None:
    """One session's picks against its excess returns: top three's hits (beat the market), bottom
    three's (lagged it), the rank correlation; None when a session lacks enough outcomes."""
    done = [r for r in rows_ if r["excess_pct"] is not None]
    if len(done) < 2 * PICKS:
        return None
    done.sort(key=lambda r: (-r[key], r["id"]))
    top, bottom = done[:PICKS], done[-PICKS:]
    return {"top_hits": sum(r["excess_pct"] > 0 for r in top), "bottom_hits": sum(r["excess_pct"] < 0 for r in bottom),
            "top_excess": round(sum(r["excess_pct"] for r in top) / PICKS, 3),
            "bottom_excess": round(sum(r["excess_pct"] for r in bottom) / PICKS, 3),
            "rank_ic": _spearman([r[key] for r in done], [r["excess_pct"] for r in done])}


def record(data: Data, end: date, days: int = HOLDOUT_DAYS) -> dict:
    """The last ``days`` finished sessions before ``end``, picked by a model fit on the sessions before
    them, against following yesterday's strong sectors."""
    past = [d for d in data.bench_days if d < end][-days:]
    if not past:
        return {"days": 0}
    return _score_record(data, past, train(data, past[0]))


def _score_record(data: Data, past: list[date], model: Model | None) -> dict:
    if model is None:
        return {"days": 0}
    model_days, base_days, daily = [], [], []
    for d in past:
        got = picks(data, model, d)
        for r in got:
            r["base"] = r["_f"]["own_1d"]
        s, b = day_score(got), day_score(got, "base")
        if s is None or b is None:
            continue
        model_days.append(s)
        base_days.append(b)
        daily.append({"date": d.isoformat(), "top": [r["id"] for r in got[:PICKS]], **s})
    return {"days": len(model_days), "from": past[0].isoformat(), "to": past[-1].isoformat(),
            "trained_until": model.until.isoformat() if model.until else None,
            **_summary(model_days), "baseline": _summary(base_days), "daily": daily}


def _summary(days: list[dict]) -> dict:
    if not days:
        return {"top3_hit": None, "bottom3_hit": None, "rank_ic": None, "top3_excess": None}
    ics = [d["rank_ic"] for d in days if d["rank_ic"] is not None]
    n = len(days) * PICKS
    return {"top3_hit": round(sum(d["top_hits"] for d in days) / n, 3),
            "bottom3_hit": round(sum(d["bottom_hits"] for d in days) / n, 3),
            "rank_ic": round(sum(ics) / len(ics), 3) if ics else None,
            "top3_excess": round(sum(d["top_excess"] for d in days) / len(days), 3)}


# ---------------------------------------------------------------- reasons

def _signed(x: float) -> str:
    return f"{x:+.1f}%"


def reasons(m: Market, row: dict, up: bool) -> list[str]:
    """The features that pushed the probability the pick's way, most first, with their numbers."""
    f, push = row["_f"], row["_push"]
    sector = next(s for s in m.sectors if s.id == row["id"])
    link = next((s for s in other(m).sectors if s.id == sector.link), None) if sector.link else None
    words = {
        "own_1d": f"직전 거래일 시장 대비 {_signed(f['own_1d'])}",
        "own_5d": f"최근 5거래일 시장 대비 {_signed(f['own_5d'])}",
        "own_20d": f"최근 20거래일 시장 대비 {_signed(f['own_20d'])}",
    }
    if link is not None and f.get("link_day"):
        where = "미장" if m.id == "kr" else "국장"
        words["link_1d"] = f"{where} {link.name}({link.ticker}) {f['link_day'].month}/{f['link_day'].day} 시장 대비 {_signed(f['link_1d'])}"
        words["link_5d"] = f"{where} {link.name} 최근 5거래일 시장 대비 {_signed(f['link_5d'])}"
    sign = 1 if up else -1
    ranked = sorted((k for k in words if push[k] * sign > 0), key=lambda k: -push[k] * sign)
    # a number pushing against its own sign (a fall read as strength) is the model's reversal
    return [words[k] + (" — 되돌림 쪽" if f[k] * sign < 0 else "") for k in ranked[:3]]


# ---------------------------------------------------------------- the target session and the view

def target(m: Market, now: datetime) -> date:
    """The session the picks are for: today when the market opens today, else the next open day."""
    d = now.astimezone(m.tz).date()
    while not market_calendar.is_open(m.id, d):
        d += timedelta(days=1)
    return d


def _prev_open(market_id: str, day: date) -> date:
    d = day - timedelta(days=1)
    while not market_calendar.is_open(market_id, d):
        d -= timedelta(days=1)
    return d


def ready(data: Data, day: date, now: datetime) -> bool:
    """Every session the picks need is in and over (SCORE_AFTER past its close - Yahoo's daily bar of a
    session in progress moves): the market's last one before ``day`` and the linked one. Korea's next
    day turns at midnight KST while the US session is still on; the US's at midnight ET, before Korea's
    close."""
    m = data.market
    if _prev_open(m.id, day) not in data.bench_days:
        return False
    if now < ready_at(m, day):
        return False
    return any(_linked(m, day) in e.days for e in data.link.values())


def _linked(m: Market, day: date) -> date:
    """The other market's session ``day``'s picks need. Korea's D: the US session before D · the US's D:
    Korea's D when it opened, else the one before."""
    o = other(m)
    return day if m.id == "us" and market_calendar.is_open(o.id, day) else _prev_open(o.id, day)


def ready_at(m: Market, day: date) -> datetime:
    """The first moment ``ready`` can be true for ``day`` - the bars still have to be in by then (#864)."""
    return session_close(other(m), _linked(m, day)) + SCORE_AFTER


def stands_until(m: Market, v: dict, now: datetime) -> datetime | None:
    """#864 Until when ``v`` (built at ``now``) cannot change without new sessions: a view waiting for its target
    until that is ready, the target's picks until the session is over and scored, a scored one until the target
    moves on (Cache.cached checks the target). None: it could change now - the bars are late."""
    day = date.fromisoformat(v["target"])
    if not (v["ready"] and v["date"] == v["target"]):
        at = ready_at(m, day)
    elif v["result"] is None:
        at = session_close(m, day) + SCORE_AFTER
    else:
        return now + timedelta(days=1)
    return at if now < at else None


NOTE = "판정 시각 전에 끝난 일봉만 씁니다. 확률은 시장(벤치마크 ETF) 대비 강할 확률이고 예측이 아닙니다."


def view(m: Market, bars: dict[str, list], now: datetime) -> dict:
    """The picks for the target session, their reasons, the record, and the score once the day is over;
    the last session's (scored) while the target's inputs are not all in and over."""
    day = target(m, now)
    data = build(m, bars)
    base = {"market": m.id, "name": m.name, "date": day.isoformat(), "target": day.isoformat(), "ready": False,
            "top": [], "bottom": [], "sectors": [], "record": {"days": 0}, "result": None, "model": None, "note": NOTE}
    if ready(data, day, now):
        return _view(m, data, day, now, base)
    prev = _prev_open(m.id, day)
    if prev in data.bench_days and ready(data, prev, now):
        v = _view(m, data, prev, now, {**base, "date": prev.isoformat()})
        if v["ready"]:
            v["note"] = (f"{day.month}/{day.day} 판정은 필요한 일봉(직전 세션 · 연결 시장)이 마감된 뒤 나옵니다 — "
                         f"지금은 직전 세션({prev.month}/{prev.day})의 판정과 채점입니다. " + NOTE)
            return v
    base["note"] = "필요한 일봉(직전 세션 · 연결 시장)이 아직 들어오지 않았습니다 — 잠시 뒤 다시 보세요."
    return base


def _view(m: Market, data: Data, day: date, now: datetime, base: dict) -> dict:
    days = [d for d in data.bench_days if d < day]
    earlier, holdout = days[:-HOLDOUT_DAYS], days[-HOLDOUT_DAYS:]
    # The record must fit before its holdout. Today's model uses those same rows plus the holdout rows.
    x, y = rows(data, earlier)
    recent_x, recent_y = rows(data, holdout)
    model = _fit_rows(np.concatenate((x, recent_x)), np.concatenate((y, recent_y)), days)
    if model is None:
        return base
    got = picks(data, model, day)
    closed = now >= session_close(m, day) + SCORE_AFTER
    for r in got:
        r["reasons"] = []
        if not closed:
            r["excess_pct"] = None          # a session in progress is no outcome
    for r in got[:PICKS]:
        r["reasons"] = reasons(m, r, up=True)
    for r in got[-PICKS:]:
        r["reasons"] = reasons(m, r, up=False)
    public = [{k: v for k, v in r.items() if not k.startswith("_")} for r in got]
    base.update(ready=True, sectors=public, top=public[:PICKS], bottom=public[::-1][:PICKS],
                record=_score_record(data, holdout, _fit_rows(x, y, earlier)),
                result=day_score(got) if closed else None,
                model={"rows": model.rows, "trained_until": model.until.isoformat() if model.until else None,
                       "features": list(FEATURES), "weights": {k: round(float(model.w[i + 1]), 4)
                                                               for i, k in enumerate(FEATURES)}})
    return base


def brief_line(v: dict) -> tuple[str, str] | None:
    """The morning brief's two phrases (strong, weak) - None when the picks are not ready."""
    if not v or not v.get("ready") or not v["top"]:
        return None
    strong = " · ".join(f"{r['name']} {round(r['prob'] * 100)}%" for r in v["top"])
    weak = " · ".join(f"{r['name']} {round(r['prob'] * 100)}%" for r in v["bottom"])
    return (f"오늘 {v['name']}에서 시장보다 강할 확률이 높은 섹터: {strong}",
            f"오늘 {v['name']}에서 시장보다 약할 확률이 높은 섹터(강할 확률): {weak}")


# ---------------------------------------------------------------- the cache

@dataclass
class _BarRead:
    ticker: str
    bars: list | None
    status: str = "fetched"
    recent_calls: int = 0
    full_calls: int = 0
    origin_max_ms: float = 0.0
    restored: bool = False               # #993 started from the day's full read another process shared


def _merge(old: list, recent: list) -> list | None:
    """The day's full read with a few newer bars laid over it by date (the newer one wins), oldest first. None
    when they do not join: no bar in common, or the oldest one in common closes differently - Yahoo re-adjusted
    the history since (a distribution or a split, auto_adjust): laid over the old, it would read as a fake move.
    Then the full history is read again."""
    if not recent:
        return old
    by_at = {b.at: b for b in old}
    common = next((b for b in recent if b.at in by_at), None)
    if common is None:
        return None
    was = by_at[common.at].close
    if not was or abs(common.close / was - 1) > 1e-9:
        return None
    by_at.update((b.at, b) for b in recent)
    return [by_at[k] for k in sorted(by_at)][-HISTORY_BARS:]


def shared_value(day: date, bars: list) -> dict:
    """#993 the day's full read as the shared tier keeps it: the KST day it was read, bar times, adjusted closes."""
    return {"day": day.isoformat(), "at": [b.at for b in bars], "close": [b.close for b in bars]}


def shared_bars(value: object, today: date) -> list | None:
    """#993 bars from a shared value read today, or None: another day's read, or not that shape (a miss, never a
    page error). A bar is its close four times over (all the picks read of it) and volume 0, as frame_bars'."""
    from .broker.base import Bar

    try:
        if value["day"] != today.isoformat():
            return None
        ats, closes = value["at"], value["close"]
        if not (isinstance(ats, list) and isinstance(closes, list) and ats and len(ats) == len(closes)):
            return None
        bars = []
        for at, c in zip(ats, closes, strict=True):
            if (not isinstance(at, str) or isinstance(c, bool) or not isinstance(c, int | float)
                    or not math.isfinite(c) or c <= 0 or (bars and at <= bars[-1].at)):
                return None
            datetime.fromisoformat(at).astimezone(KST)   # what _closes does with it (an extreme offset overflows)
            bars.append(Bar(at=at, open=float(c), high=float(c), low=float(c), close=float(c), volume=0.0))
    except (TypeError, KeyError, ValueError, OverflowError):
        return None
    return bars


class Cache:
    """Each market's view, standing until it can change (``stands_until``) or for ``NOT_READY_TTL_SEC`` while its
    bars are late; one build at a time. The ETFs' bars are kept for both markets (#864), and each one's full read
    of the day in the shared tier for the next process (#993)."""

    def __init__(self, ttl: float = NOT_READY_TTL_SEC, clock=_time.monotonic) -> None:
        self.ttl, self.clock = ttl, clock
        # market → (expires at on clock, target session, view, stands until: a wall time "ahead" does not pull)
        self._views: dict[str, tuple[float, str, dict, datetime | None]] = {}
        # ticker → (read at on clock, day of the full read, bars): kept for the process's life (~30 ETFs x 2400 bars,
        # tens of MB) so a later rebuild reads only the last few
        self._bars: dict[str, tuple[float, date, list]] = {}
        # Share failed attempts across market builds for the same window as successful reads.
        self._failed_bars_at: dict[str, float] = {}
        self._unshared: set[str] = set()     # #993 tickers whose full read is too big for the shared tier (logged once)
        self._lock = threading.Lock()

    def cached(self, market: str, now: datetime, ahead: float = 0) -> dict | None:
        hit = self._views.get(market)
        if not hit or hit[1] != target(MARKETS[market], now).isoformat():
            return None
        if hit[3] is not None and now < hit[3]:
            return hit[2]                     # nothing new until then: a prewarm's "ahead" does not rebuild it
        return hit[2] if self.clock() < hit[0] - ahead else None

    @staticmethod
    def _restore(ticker: str, today: date) -> list | None:
        """#993 today's full read of ``ticker`` from the shared tier, or None (off, down, a miss, another day's). Never
        raises: it runs outside the per-ETF try, where one error would fail the whole market's build."""
        try:
            hit = shared_cache.get_versioned(SHARED_BARS, SHARED_BARS_VERSION, ticker)
            return shared_bars(hit[0], today) if hit is not None else None
        except Exception as exc:  # noqa: BLE001 - a cache is never a reason for a failed build
            log.info("day sectors: %s shared bars not read: %s", ticker, type(exc).__name__)
            return None

    def _share(self, ticker: str, today: date, bars: list) -> None:
        """#993 a full read for the next process of the day. One too big for the shared tier stays here only."""
        if not shared_cache.enabled() or ticker in self._unshared:
            return
        value = shared_value(today, bars)
        try:                                 # a non-finite close passes here: the tier itself skips it
            size = len(json.dumps(value, separators=(",", ":")))
        except (TypeError, ValueError):      # not JSON at all: kept here only - never a failed build
            return
        if size + 256 > shared_cache.MAX_VALUE_BYTES:           # room for the envelope (key, written at, ttl)
            self._unshared.add(ticker)
            log.info("day sectors: %s full read (%d bars) not shared: %d bytes of JSON", ticker, len(bars), size)
            return
        shared_cache.put_versioned(SHARED_BARS, SHARED_BARS_VERSION, ticker, value, SHARED_BARS_TTL_SEC)

    def _read_bars(self, tickers: list[str], candles: Callable[[str, str, int], list],
                   now: datetime) -> tuple[dict, bool, list[_BarRead]]:
        """Every ticker's bars, and whether all of them were read now (or a moment ago) - a view built on an
        ETF that failed is not held until it can change (Cache.read): it is asked again soon. Keep the
        last good bars and share a failed read with the other market without counting them as fresh."""
        today = now.astimezone(KST).date()

        def one(t: str) -> _BarRead:
            had = self._bars.get(t)
            failed_at = self._failed_bars_at.get(t)
            stamp = self.clock()
            result = _BarRead(t, had[2] if had else None)
            if had and stamp - had[0] < BARS_FRESH_SEC:
                result.status = "cache_hit"
                return result
            if failed_at is not None and stamp - failed_at < BARS_FRESH_SEC:
                result.status = "reused_failure"
                if had is None:              # #993 the other market's build restored them but could not refresh them
                    result.bars = self._restore(t, today)
                    result.restored = result.bars is not None
                return result
            if had is None and (shared := self._restore(t, today)) is not None:
                # #993 a fresh process: the day's full read another one shared stands in for its own - the last few
                # are read on top of it as below, and it is kept (self._bars) only once they join
                had, result.bars, result.restored = (stamp, today, shared), shared, True

            def fetch(count: int) -> list:
                result.recent_calls += count == RECENT_BARS
                result.full_calls += count == HISTORY_BARS
                start = _time.perf_counter()
                try:
                    return candles(t, "1d", count)
                finally:
                    result.origin_max_ms = max(result.origin_max_ms, (_time.perf_counter() - start) * 1000)

            full = False
            try:
                bars = None
                if had and had[1] == today and had[2]:
                    recent = fetch(RECENT_BARS)
                    # an empty answer keeps the last bars until the next try - but restored ones read all as a
                    # cold start would (they are not this process's own read: never a view worse than a cold one)
                    bars = _merge(had[2], recent) if recent else (None if result.restored else recent)
                if bars is None:                 # the day's first read, or the history moved: all of it
                    bars, full = fetch(HISTORY_BARS), True
            except Exception as exc:  # noqa: BLE001 - one ETF down leaves the rest (and its last bars)
                log.info("day sectors: %s failed: %s", t, type(exc).__name__)
                result.status = "error"
            else:
                if bars:
                    self._bars[t] = (self.clock(), today, bars)
                    self._failed_bars_at.pop(t, None)
                    result.bars = bars
                    if full:
                        self._share(t, today, bars)
                    return result
                result.status = "empty"
            self._failed_bars_at[t] = self.clock()
            return result

        with ThreadPoolExecutor(max_workers=FETCH_PARALLEL, thread_name_prefix="desk-sectors") as pool:
            reads = list(pool.map(one, tickers))
        bars = {read.ticker: read.bars for read in reads if read.bars is not None}
        return bars, all(read.status in ("cache_hit", "fetched") for read in reads), reads

    def read(self, market: str, candles: Callable[[str, str, int], list], now: datetime | None = None,
             ahead: float = 0) -> dict:
        """The market's view, rebuilt when it can have changed (``ahead``: this many seconds before a late one
        is asked again) or for another session."""
        now = now or datetime.now(KST)
        if (v := self.cached(market, now, ahead)) is not None:
            return v
        enqueued_at = _time.perf_counter()
        with self._lock:
            acquired_at = _time.perf_counter()
            if (v := self.cached(market, now, ahead)) is not None:
                return v
            m = MARKETS[market]
            fetch_at = _time.perf_counter()
            bars, complete, reads = self._read_bars(symbols(m), candles, now)
            view_at = _time.perf_counter()
            v = view(m, bars, now)
            view_ms = (_time.perf_counter() - view_at) * 1000
            until = stands_until(m, v, now) if complete else None   # an ETF missing: asked again soon
            life = (until - now).total_seconds() if until else self.ttl
            self._views[market] = (self.clock() + life, v["target"], v, until)
        states = Counter(read.status for read in reads)
        log.info(
            "day sectors: rebuild market=%s complete=%s lock_ms=%.1f bars_ms=%.1f view_ms=%.1f "
            "bar_hits=%d recent_calls=%d full_calls=%d origin_max_ms=%.1f origin_errors=%d "
            "origin_empty=%d reused_failures=%d restored=%d",
            market, complete, (acquired_at - enqueued_at) * 1000, (view_at - fetch_at) * 1000, view_ms,
            states["cache_hit"], sum(read.recent_calls for read in reads), sum(read.full_calls for read in reads),
            max((read.origin_max_ms for read in reads), default=0.0),
            states["error"], states["empty"], states["reused_failure"], sum(read.restored for read in reads),
        )
        return v
