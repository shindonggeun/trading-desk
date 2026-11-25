"""#577 How often a session gap fills, and how fast - from the instrument's own past.

A session gap is the new session's open away from the last session's close (Yahoo's daily bar: the
CME session for NQ=F / ES=F, the regular session for an ETF). It fills when a later low (an up gap) or
high (a down gap) trades back to that close. Every gap of at least ``MIN_GAP_ATR`` daily ATR since the
bars begin is measured, with only what was known at its open: the ATR, the 20/60-day averages and the
calendar come from the bars before it. The odds are counts: of the gaps like it, how many filled
within 1, 3, 5 and 20 sessions (the session it opened counts as the first), and the median sessions
it took. A gap too recent to have seen k sessions only counts for k when it already filled.

- Korean index futures: Yahoo keeps no KOSPI200 futures history, so ``KOSPI200F`` reads KODEX 200
  (069500.KS) - the same index on the KRX session.
- A leveraged or inverse product (SOXL, TQQQ, KORU ...) reads its underlying's gaps, and the fill
  level is also given in the product's price: its last close moved by the multiple of the
  underlying's way to the fill.
- Cause: the US macro calendar (desk.events_calendar: FOMC, CPI / jobs / PPI) where it knows the
  date, else an earnings-season window (mid-January ... the US reporting weeks), else a gap after a
  weekend or holiday, else "other". The calendar covers the years its sources list - older FOMC
  or CPI gaps count as their season or "other".

These are past frequencies, not a forecast: when a gap fills is a distribution, shown as one.
"""

from __future__ import annotations

import asyncio
import logging
import statistics
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from datetime import time as clock_time
from typing import Any

from . import market_calendar
from . import underlying as under
from .shared_cache import TieredCache
from .single_flight import KeyLocks
from .sr import _local_day
from .ttl_cache import TTLCache

log = logging.getLogger(__name__)

HISTORY_BARS = 2000          # about eight years of daily bars
ATR_DAYS = 14
MIN_GAP_ATR = 0.2            # a gap smaller than this (daily ATR) is noise
MIN_GAP_PCT = 0.001          # ...or than 0.1% of the price
HORIZONS = (1, 3, 5, 20)     # sessions, the opening one included
FOLLOW = 60                  # sessions a gap is followed for its median
OPEN_LOOKBACK = 60           # an unfilled gap older than this many sessions is not "open" any more
OPEN_MAX = 3                 # per direction, the nearest to the price first
FEW = 30                     # fewer gaps than this: "표본 적음"
SIZES = (("small", 0.2, 0.5), ("mid", 0.5, 1.0), ("large", 1.0, float("inf")))
SIZE_KO = {"small": "작은 갭(0.2~0.5 ATR)", "mid": "중간 갭(0.5~1 ATR)", "large": "큰 갭(1 ATR 이상)"}
CAUSE_KO = {"fomc": "FOMC", "macro": "물가 · 고용 지표", "earnings": "실적 시즌", "weekend": "주말 · 휴장 뒤",
            "other": "기타"}
TREND_KO = {"up": "20 · 60일선 위", "down": "20 · 60일선 아래", "mixed": "이평선 사이"}
DIR_KO = {"up": "상승 갭", "down": "하락 갭"}
CACHE_TTL_SEC = 30 * 60
WATCH = ("NQ=F", "ES=F", "KOSPI200F")      # the tips' and prewarm's instruments

# tickers with no history of their own on Yahoo, or no row in desk.related.LEVERAGED
ALIASES = {"KOSPI200F": ("069500.KS", "코스피200 선물(야후에 선물 이력이 없어 KODEX 200 일봉으로 계산)")}
EXTRA_LEVERAGED = {"KORU": ("EWY", 3.0)}   # Direxion MSCI Korea 3x - EWY tracks the same index
NAMES = {"NQ=F": "나스닥100 선물", "ES=F": "S&P500 선물", "KOSPI200F": "코스피200 선물"}
# CME futures trade round the clock but for 17:00-18:00 ET: their daily gaps are mostly the weekend's
FUTURES_NOTE = ("CME 선물은 평일 17:00~18:00(뉴욕) 한 시간만 쉬어 일봉 갭이 작고 드물다 — 표의 갭은 대부분 주말 · 휴장 뒤 갭이다. "
                "실적 · 지표 반응은 쉬지 않고 거래되는 세션 안에서 움직여 갭으로 잡히지 않는다")

Bars = list[dict]
Fetch = Callable[[str], Bars]                 # ticker -> daily bars, oldest first
Events = Callable[[], Iterable[Any]]          # -> desk.events_calendar.Event-like (id, at)


def _rows(bars) -> Bars:
    return [asdict(b) if is_dataclass(b) else dict(b) for b in bars]


def yahoo_fetch(yahoo_candles: Callable) -> Fetch:
    """A Fetch over desk.prices' Yahoo candles: HISTORY_BARS daily bars."""
    return lambda ticker: _rows(yahoo_candles(ticker, "1d", HISTORY_BARS))


# ------------------------------------------------------------------ measuring


def _atrs(bars: Bars) -> list[float | None]:
    """The Wilder ATR known before each bar opens (from the bars before it), None while too few."""
    out: list[float | None] = [None] * len(bars)
    atr = None
    ranges: list[float] = []
    for i in range(1, len(bars)):
        out[i] = atr
        p, b = bars[i - 1], bars[i]
        tr = max(b["high"] - b["low"], abs(b["high"] - p["close"]), abs(b["low"] - p["close"]))
        if atr is None:
            ranges.append(tr)
            if len(ranges) == ATR_DAYS:
                atr = sum(ranges) / ATR_DAYS
        else:
            atr = (atr * (ATR_DAYS - 1) + tr) / ATR_DAYS
    return out


def _trend(closes: list[float], i: int) -> str | None:
    """Where the last close before bar ``i`` sat against its 20- and 60-day averages."""
    if i < 60:
        return None
    last = closes[i - 1]
    above = [last > sum(closes[i - n:i]) / n for n in (20, 60)]
    return "up" if all(above) else "down" if not any(above) else "mixed"


def _season(day: date) -> bool:
    """The US reporting weeks: mid-January to mid-February, and the same a quarter later each time."""
    return day.month in (1, 4, 7, 10) and day.day >= 12 or day.month in (2, 5, 8, 11) and day.day <= 15


def _cause(day: date, prev_day: date, events: dict[date, str]) -> str:
    """The calendar's event on the gap's session or the one it follows, an earnings season, a break."""
    found = [events[d] for d in (day, prev_day) if d in events]
    if "fomc" in found:
        return "fomc"
    if found:
        return "macro"
    if _season(day):
        return "earnings"
    if (day - prev_day).days > 1:
        return "weekend"
    return "other"


def _session_day(day: date, prev_day: date) -> date:
    """#568: Yahoo dates a futures session opened at 18:00 New York with the day before until midnight, so
    the newest bar can carry the previous bar's date - it is the next weekday's session. (Through
    yfinance the two same-date rows come back as one - the newer - see ``evening_session``, #741.)"""
    return day if day > prev_day else market_calendar.next_weekday(prev_day)


FUTURES_REOPEN = clock_time(18)   # CME reopens 18:00 New York for the next session


def evening_session(bars: Bars, symbol: str, now: datetime) -> bool:
    """#741: Monday to Thursday from 18:00 New York, Yahoo dates the next session's live daily row with
    that evening's date and yfinance keeps only the newer of two same-date rows - so the session that
    ended at 17:00 is missing and the last bar is the new one, two sessions after the bar before it.
    Its open against that close is a whole day's move, not a gap. (Sunday evening drops nothing: no
    session is dated Sunday, and Friday's close to Sunday's open is a real gap.)"""
    if not symbol.upper().endswith("=F") or not bars:
        return False
    local = now.astimezone(market_calendar.ET)
    return (local.weekday() <= 3 and local.time() >= FUTURES_REOPEN
            and _local_day(bars[-1]["at"], symbol) == local.date())


def _size(atr_multiple: float) -> str:
    return next(name for name, lo, hi in SIZES if lo <= atr_multiple < hi)


def measure(bars: Bars, ticker: str, events: dict[date, str] | None = None, *, last_unpaired: bool = False) -> list[dict]:
    """Every gap in ``bars`` (oldest first): when, which way, how big, why, the trend before it, and
    when it filled (sessions, the opening one = 1) - None while not yet. ``last_unpaired``: the last
    bar does not follow the bar before it (``evening_session``) - it fills gaps but opens none."""
    events = events or {}
    atrs = _atrs(bars)
    closes = [b["close"] for b in bars]
    out = []
    for i in range(1, len(bars) - 1 if last_unpaired else len(bars)):
        prev, bar, atr = bars[i - 1], bars[i], atrs[i]
        gap = bar["open"] - prev["close"]
        if not atr or prev["close"] <= 0 or abs(gap) < MIN_GAP_ATR * atr or abs(gap) < MIN_GAP_PCT * prev["close"]:
            continue
        up = gap > 0
        level = prev["close"]
        filled = None
        deepest = 0.0                                   # how much of the gap was retraced, 0..1
        for j in range(i, min(len(bars), i + FOLLOW)):
            reach = (bar["open"] - bars[j]["low"]) if up else (bars[j]["high"] - bar["open"])
            deepest = max(deepest, min(1.0, reach / abs(gap)))
            if (bars[j]["low"] <= level) if up else (bars[j]["high"] >= level):
                filled = j - i + 1
                break
        prev_day = _local_day(prev["at"], ticker)
        day = _session_day(_local_day(bar["at"], ticker), prev_day)
        out.append({
            "index": i, "day": day.isoformat(), "direction": "up" if up else "down",
            "fill_price": round(level, 4), "open": round(bar["open"], 4),
            "gap_pct": round(gap / level * 100, 2), "atr_multiple": round(abs(gap) / atr, 2),
            "size": _size(abs(gap) / atr), "cause": _cause(day, prev_day, events), "trend": _trend(closes, i),
            "filled_in": filled, "seen": len(bars) - i, "retraced": round(deepest, 2),
        })
    return out


def odds(gaps: list[dict], after: int = 0) -> dict:
    """Of ``gaps`` still open ``after`` sessions in: the share filled within ``after`` + k sessions for
    each horizon (only gaps seen that long, or already filled, count), the median sessions to fill (of
    those filled within FOLLOW, counted from the open), and how many there were."""
    pool = [g for g in gaps if g["filled_in"] is None or g["filled_in"] > after]
    pool = [g for g in pool if g["seen"] > after]
    row: dict = {"n": len(pool)}
    for k in HORIZONS:
        span = after + k
        known = [g for g in pool if (g["filled_in"] is not None and g["filled_in"] <= span) or g["seen"] >= span]
        hit = sum(1 for g in known if g["filled_in"] is not None and g["filled_in"] <= span)
        row[f"p{k}"] = round(hit / len(known), 3) if known else None
    took = [g["filled_in"] for g in pool if g["filled_in"] is not None]
    row["median_sessions"] = statistics.median(took) if took else None
    row["filled_share"] = round(len(took) / len(pool), 3) if pool else None
    row["few"] = len(pool) < FEW
    return row


def table(gaps: list[dict]) -> dict:
    """The odds overall and by direction, size, cause and trend - and by direction x size."""
    done = [g for g in gaps if g["seen"] > 1 or g["filled_in"] is not None]
    out: dict = {"all": odds(done)}
    for key, names in (("direction", DIR_KO), ("size", SIZE_KO), ("cause", CAUSE_KO), ("trend", TREND_KO)):
        out[key] = {name: odds([g for g in done if g[key] == name]) for name in names}
    out["direction_size"] = {f"{d}_{s}": odds([g for g in done if g["direction"] == d and g["size"] == s])
                             for d in DIR_KO for s in SIZE_KO}
    return out


def open_gaps(gaps: list[dict], bars: Bars) -> list[dict]:
    """Gaps of the last OPEN_LOOKBACK sessions nothing has traded back to yet, the nearest first per
    direction, each with the odds of gaps like it (same direction and size) that were still open as long."""
    last = len(bars)
    price = bars[-1]["close"]
    out = []
    for g in gaps:
        if g["filled_in"] is not None or last - g["index"] > OPEN_LOOKBACK:
            continue
        sessions = last - g["index"]                     # sessions it has stayed open, today's included
        like = [h for h in gaps if h["direction"] == g["direction"] and h["size"] == g["size"] and h is not g]
        out.append({**{k: g[k] for k in ("day", "direction", "fill_price", "open", "gap_pct", "atr_multiple", "size",
                                         "cause", "trend")},
                    "open_sessions": sessions, "to_fill_pct": round((g["fill_price"] / price - 1) * 100, 2),
                    "odds": odds(like, after=sessions)})
    near = []
    for d in DIR_KO:
        near += sorted((g for g in out if g["direction"] == d), key=lambda g: abs(g["to_fill_pct"]))[:OPEN_MAX]
    return sorted(near, key=lambda g: abs(g["to_fill_pct"]))


# ------------------------------------------------------------------ the instrument


def basis_of(ticker: str) -> dict:
    """What a ticker's gaps are read on: itself, an alias's stand-in or a product's underlying."""
    t = ticker.upper()
    if t in ALIASES:
        sym, note = ALIASES[t]
        return {"ticker": t, "symbol": sym, "leverage": None, "note": note}
    if t.endswith("=F"):
        return {"ticker": t, "symbol": t, "leverage": None, "note": FUTURES_NOTE}
    lever = under.leverage_of(t)
    found = (lever[0], lever[1]) if lever else EXTRA_LEVERAGED.get(t)
    if found:
        sym, leverage = found
        return {"ticker": t, "symbol": sym, "leverage": float(leverage),
                "note": f"{t} 는 {sym} 의 {leverage:+g}배 상품 — {sym} 의 갭으로 계산하고 메움 가격은 {t} 가격으로도 환산"}
    return {"ticker": t, "symbol": t, "leverage": None, "note": None}


def calendar_days(events: Iterable[Any]) -> dict[date, str]:
    """The calendar's market-wide releases by their New York date: fomc, or macro (CPI, jobs, PPI)."""
    from .events_calendar import ET

    out: dict[date, str] = {}
    for e in events:
        kind = "fomc" if e.id == "fomc" else "macro" if e.id in ("cpi", "jobs", "ppi") else None
        if kind:
            day = e.at.astimezone(ET).date()
            out[day] = "fomc" if out.get(day) == "fomc" else kind
    return out


def report(fetch: Fetch, ticker: str, events: Events | None = None, now: datetime | None = None) -> dict:
    """GET /api/gaps: the open gaps, the odds table and how many gaps it rests on."""
    b = basis_of(ticker)
    bars = fetch(b["symbol"])
    if len(bars) < 80:
        raise ValueError(f"too few daily bars for {b['symbol']} ({len(bars)})")
    days = calendar_days(events() if events else [])
    gaps = measure(bars, b["symbol"], days,
                   last_unpaired=evening_session(bars, b["symbol"], now or datetime.now(UTC)))
    opened = open_gaps(gaps, bars)
    if b["leverage"]:
        own = fetch(b["ticker"])
        if own:
            p, u = own[-1]["close"], bars[-1]["close"]
            for g in opened:   # the product's price when the underlying is back at the fill
                g["product_fill_price"] = round(p * (1 + b["leverage"] * (g["fill_price"] / u - 1)), 4)
                g["product_move_pct"] = round(b["leverage"] * g["to_fill_pct"], 2)
    first = _local_day(bars[0]["at"], b["symbol"])
    return {"ticker": b["ticker"], "name": NAMES.get(b["ticker"]), "basis": b, "as_of": bars[-1]["at"],
            "last_close": bars[-1]["close"], "since": first.isoformat(), "gaps_counted": len(gaps),
            "open": opened, "table": table(gaps),
            "calendar_from": min(days).isoformat() if days else None,
            "method": {"min_gap_atr": MIN_GAP_ATR, "horizons": list(HORIZONS), "few": FEW,
                       "bar": "1d", "fill": "a later low (up gap) / high (down gap) at the last close"}}


CACHE_MAX = 128
# #628 bounded: the oldest go first. #689 then the shared tier (api pages and the analyst's notes read the same report)
_CACHE = TieredCache("gap_fill", TTLCache(CACHE_MAX, CACHE_TTL_SEC))


_READS = KeyLocks()   # #993 one report per ticker at a time
FAIL_HOLD_SEC = 5.0   # a ticker whose report just failed is not read again this soon
_failed: dict[str, tuple[float, str]] = {}   # key -> (failed at on the clock, the failure's class name)
_failed_lock = threading.Lock()


class RecentFailure(RuntimeError):
    """The ticker's report failed a moment ago (FAIL_HOLD_SEC) - not read again yet."""


def _recent_failure(key: str, now: float) -> str | None:
    with _failed_lock:
        got = _failed.get(key)
        return got[1] if got is not None and now - got[0] < FAIL_HOLD_SEC else None


def _note_failure(key: str, now: float, exc: BaseException | None) -> None:
    """Keep a failure for FAIL_HOLD_SEC (None: forget it). Bounded: only the last few seconds' failures stay."""
    with _failed_lock:
        for k in [k for k, (at, _) in _failed.items() if now - at >= FAIL_HOLD_SEC]:
            del _failed[k]
        if exc is None:
            _failed.pop(key, None)
        else:
            _failed[key] = (now, type(exc).__name__)


def read(fetch: Fetch, ticker: str, events: Events | None = None, clock: Callable[[], float] = time.monotonic,
         ahead: float = 0.0) -> dict:
    """``report`` cached CACHE_TTL_SEC; ``ahead`` refreshes one that would expire that soon (prewarm).

    #993 a cold start's asks for one ticker at once (the prewarm, the tips page, a run's note) each read eight years
    of bars: a second asker now waits for the first one's report and takes it from the cache. A fresh report never
    waits behind a refresh (checked before the lock). A failed one is kept FAIL_HOLD_SEC: the askers queued behind
    it fail at once (RecentFailure) instead of each reading the eight years again."""
    key = ticker.upper()
    hit = _CACHE.get(key, ahead=ahead, now=clock())
    if hit is not None:
        return hit
    with _READS.hold(key):
        hit = _CACHE.get(key, ahead=ahead, now=clock())
        if hit is not None:
            return hit
        if (name := _recent_failure(key, clock())) is not None:
            raise RecentFailure(f"{key}: the last read failed ({name}) a moment ago")
        try:
            result = report(fetch, key, events)   # a failure raises: nothing is kept
        except Exception as exc:
            _note_failure(key, clock(), exc)
            raise
        _note_failure(key, clock(), None)
        _CACHE.put(key, result, now=clock())
        return result


def cached(ticker: str, clock: Callable[[], float] = time.monotonic) -> dict | None:
    """The report already made, if still fresh - never fetches (for the tips' page)."""
    return _CACHE.get(ticker.upper(), now=clock())


# ------------------------------------------------------------------ words


def _pct(p: float | None) -> str:
    return "?" if p is None else f"{round(p * 100)}%"


def line_ko(rep: dict, gap: dict) -> str:
    """One Korean sentence on an open gap: where it fills, how often gaps like it did, how fast."""
    o = gap["odds"]
    name = rep.get("name") or rep["ticker"]
    where = f"{gap['fill_price']:g}"
    if gap.get("product_fill_price") is not None:
        where += f"(이 상품 약 {gap['product_fill_price']:g}, {gap['product_move_pct']:+.1f}%)"
    few = " · 표본 적음" if o["few"] else ""
    took = f", 메운 경우 중앙값 {o['median_sessions']:g}거래일" if o["median_sessions"] is not None else ""
    return (f"{name} {gap['day']} {DIR_KO[gap['direction']]} {gap['gap_pct']:+.2f}%({CAUSE_KO[gap['cause']]}) — "
            f"{where} 까지 {gap['to_fill_pct']:+.2f}% 남음, {gap['open_sessions']}거래일째 열림. "
            f"비슷한 갭의 5거래일 안 메움 {_pct(o['p5'])} · 20거래일 {_pct(o['p20'])}{took} (표본 {o['n']}{few})")


def entry_warning(rep: dict, side: str | None = None, price: float | None = None) -> dict | None:
    """#577 for the entry summary: the nearest open gap the entry sits beyond - a long above an up gap
    (or a short under a down gap) may be pulled back to its fill. ``side`` long / short keeps the gaps
    against that side only. None when there is none."""
    price = price or rep["last_close"]
    for g in rep["open"]:
        if side in ("long", "short") and g["direction"] != ("up" if side == "long" else "down"):
            continue
        beyond = (g["direction"] == "up" and price > g["fill_price"]) or (g["direction"] == "down" and price < g["fill_price"])
        if beyond:
            move = g.get("product_move_pct", g["to_fill_pct"])
            side = "갭 위 매수" if g["direction"] == "up" else "갭 아래 매도"
            o = g["odds"]
            return {"direction": g["direction"], "fill_price": g["fill_price"], "move_pct": move,
                    "product_fill_price": g.get("product_fill_price"), "p5": o["p5"], "p20": o["p20"],
                    "n": o["n"], "few": o["few"],
                    "text": f"{side} — 갭이 메워지면 약 {move:+.1f}% (비슷한 갭 5거래일 안 메움 {_pct(o['p5'])}, 표본 {o['n']})"}
    return None


def prompt_note(rep: dict | None) -> str | None:
    """The agents' block: the open gaps and their past odds (English, numbers only)."""
    if not rep or not rep["open"]:
        return None
    lines = [(f"Session gaps on {rep['basis']['symbol']} (desk, {rep['gaps_counted']} gaps since {rep['since']}; "
             "past frequencies, not a forecast):")]
    for g in rep["open"][:2]:
        o = g["odds"]
        prod = (f"; the product at about {g['product_fill_price']:g} ({g['product_move_pct']:+.1f}%)"
                if g.get("product_fill_price") is not None else "")
        lines.append(
            f"- open {g['direction']} gap of {g['day']} ({g['gap_pct']:+.2f}%, {g['atr_multiple']:g} ATR, {g['cause']}), "
            f"fills at {g['fill_price']:g} ({g['to_fill_pct']:+.2f}% from the last close){prod}; open {g['open_sessions']} "
            f"sessions. Gaps like it still open that long filled within 5 sessions {_pct(o['p5'])}, 20 sessions "
            f"{_pct(o['p20'])}, median {o['median_sessions']} sessions (n={o['n']}{', few' if o['few'] else ''}).")
    return "\n".join(lines)


async def note_for(fetch: Fetch, ticker: str, events: Events | None = None, timeout: float = 20.0) -> str | None:
    """The analysis note for ``ticker`` (the run's own instrument: the underlying of a product)."""
    return prompt_note(await asyncio.wait_for(asyncio.to_thread(read, fetch, ticker, events), timeout))

