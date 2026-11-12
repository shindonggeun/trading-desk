"""Earnings preview for one ticker (#293): what the street expects before a report, from free sources.

Every number here comes from a keyless source the desk could check (2026-11-12, NBIS · MU · 005930):

- Yahoo (yfinance): EPS and revenue consensus (``earnings_estimate`` / ``revenue_estimate``: this and
  next quarter and year, low / high, analyst count, a year ago, growth), the EPS estimate against the
  reported EPS of past reports with their times (``get_earnings_dates``), analyst price targets
  (``analyst_price_targets``), the rating counts (``recommendations_summary``), short interest
  (``info``, US only) and the option chain (US only) for the implied move.
- Naver Finance (m.stock.naver.com/api/stock/<code>/finance/quarter|annual, unofficial, already used
  for NXT prices): Korean revenue / operating profit / net income, the consensus columns marked
  ``isConsensus: Y`` (억원).
- The past reactions are computed here from Yahoo daily bars: the close-to-close move on the first
  session that could react to each report.

What no free source gives is not guessed: whisper numbers, Bloomberg's consensus and adjusted
EBITDA / capex estimates are named in ``unavailable``. Each part is fetched on its own; one failing
leaves the others (``errors``). Arithmetic over published estimates, not a forecast.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger(__name__)

NEW_YORK = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")
TTL_SEC = 6 * 3600            # a ticker's preview is fetched again after this...
NEAR_TTL_SEC = 30 * 60        # ...or this, within a day of its report
FAIL_TTL_SEC = 10 * 60        # a ticker that gave nothing at all is asked again after this
MAX_TICKERS = 256             # previews kept (least recently used dropped first)
WAIT_SEC = 60.0               # a request waits this long for a fetch; the fetch itself goes on and is kept
HISTORY = 8                   # past reports shown and averaged
WIDE_SPREAD = 0.5             # an option quote whose ask - bid is over half its mid is no price
NAVER_URL = "https://m.stock.naver.com/api/stock/{code}/finance/{kind}"
NAVER_ROWS = {"매출액": "revenue", "영업이익": "op_income", "당기순이익": "net_income", "영업이익률": "op_margin"}
PERIODS = {"0q": "this_quarter", "+1q": "next_quarter", "0y": "this_year", "+1y": "next_year"}
UNAVAILABLE = [
    {"id": "whisper", "label": "위스퍼 넘버", "why": "Earnings Whispers 등 유료 · 약관상 수집 불가"},
    {"id": "bloomberg", "label": "블룸버그 컨센서스", "why": "블룸버그 터미널(유료)"},
    {"id": "ebitda", "label": "조정 EBITDA 추정", "why": "무료 출처 없음"},
    {"id": "capex", "label": "설비투자(CapEx) 추정", "why": "무료 출처 없음"},
]
SOURCES = {"estimates": "Yahoo Finance", "history": "Yahoo Finance", "targets": "Yahoo Finance",
           "ratings": "Yahoo Finance", "implied_move": "Yahoo Finance 옵션 체인", "past_moves": "Yahoo 일봉(계산)",
           "short_interest": "Yahoo Finance", "kr_consensus": "네이버 증권"}


def is_korean(ticker: str) -> bool:
    return ticker.upper().endswith((".KS", ".KQ"))


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _int(v) -> int | None:
    f = _num(v)
    return int(f) if f is not None else None


# --- parsing (pure) ---------------------------------------------------------------------------

def estimates(rows: dict[str, dict]) -> dict | None:
    """yfinance ``earnings_estimate`` / ``revenue_estimate`` as ``frame.to_dict("index")`` ->
    {this_quarter: {avg, low, high, analysts, year_ago, growth_pct, currency}, ...}."""
    out = {}
    for key, name in PERIODS.items():
        r = rows.get(key)
        if not r or _num(r.get("avg")) is None:
            continue
        year_ago = r.get("yearAgoEps", r.get("yearAgoRevenue"))
        growth = _num(r.get("growth"))
        out[name] = {"avg": _num(r.get("avg")), "low": _num(r.get("low")), "high": _num(r.get("high")),
                     "analysts": _int(r.get("numberOfAnalysts")), "year_ago": _num(year_ago),
                     "growth_pct": round(growth * 100, 1) if growth is not None else None,
                     "currency": r.get("currency") if isinstance(r.get("currency"), str) else None}
    return out or None


def _when(at: datetime, korean: bool) -> str | None:
    """pre (before the US open) | after (after the close) | during; None for a Korean report (Yahoo's
    times for those are not the release time)."""
    if korean:
        return None
    local = at.astimezone(NEW_YORK)
    minutes = local.hour * 60 + local.minute
    if minutes == 0:          # Yahoo reads the page's hour; a report with no time announced comes as midnight
        return None
    return "pre" if minutes < 9 * 60 + 30 else "after" if minutes >= 16 * 60 else "during"


def history(rows: list[tuple[datetime, dict]], korean: bool = False) -> list[dict]:
    """``get_earnings_dates`` rows (time, {EPS Estimate, Reported EPS, Surprise(%)}) newest first."""
    out = []
    for at, r in sorted(rows, key=lambda p: p[0], reverse=True):
        est, act = _num(r.get("EPS Estimate")), _num(r.get("Reported EPS"))
        surprise = _num(r.get("Surprise(%)"))
        if surprise is None and est not in (None, 0) and act is not None:
            surprise = (act - est) / abs(est) * 100
        day = at.astimezone(KST if korean else NEW_YORK).date()
        out.append({"at": at.astimezone(UTC).isoformat(), "date": day.isoformat(), "when": _when(at, korean),
                    "estimate": est, "actual": act,
                    "surprise_pct": round(surprise, 1) if surprise is not None else None,
                    "beat": None if act is None or est is None else act >= est})
    return out


def next_report(rows: list[dict], calendar_dates: list[date], now: datetime, korean: bool = False) -> dict | None:
    """The next report: the earliest one not reported yet that is not past (by a day), else Yahoo's
    calendar date. ``time_kst`` only when the time is known (a US report); ``days`` against the
    market's own date (a day after the report, Yahoo may not have the actual yet: -1)."""
    today = now.astimezone(KST if korean else NEW_YORK).date()
    ahead = [r for r in rows if r["actual"] is None and date.fromisoformat(r["date"]) >= today - timedelta(days=1)]
    if ahead:
        r = min(ahead, key=lambda r: r["at"])
        at = datetime.fromisoformat(r["at"])
        return {"date": r["date"], "when": r["when"], "estimate": r["estimate"],
                "time_kst": at.astimezone(KST).strftime("%m-%d %H:%M") if r["when"] else None,
                "days": (date.fromisoformat(r["date"]) - today).days}
    future = sorted(d for d in calendar_dates if d >= today)
    if future:
        return {"date": future[0].isoformat(), "when": None, "estimate": None, "time_kst": None,
                "days": (future[0] - today).days}
    return None


def targets(d: dict | None) -> dict | None:
    if not d or _num(d.get("mean")) is None:
        return None
    current, mean = _num(d.get("current")), _num(d.get("mean"))
    return {"mean": mean, "median": _num(d.get("median")), "high": _num(d.get("high")), "low": _num(d.get("low")),
            "current": current,
            "upside_pct": round((mean / current - 1) * 100, 1) if current else None}


def ratings(rows: list[dict]) -> dict | None:
    """``recommendations_summary`` records -> this month's counts (period 0m)."""
    row = next((r for r in rows if r.get("period") == "0m"), rows[0] if rows else None)
    if not row:
        return None
    keys = ("strongBuy", "buy", "hold", "sell", "strongSell")
    counts = {k: _int(row.get(k)) or 0 for k in keys}
    total = sum(counts.values())
    if not total:
        return None
    return {"strong_buy": counts["strongBuy"], "buy": counts["buy"], "hold": counts["hold"],
            "sell": counts["sell"], "strong_sell": counts["strongSell"], "total": total,
            "buy_pct": round((counts["strongBuy"] + counts["buy"]) / total * 100)}


def pick_expiry(expiries: list[str], report: date, when: str | None) -> str | None:
    """The first expiry whose price includes the report: an after-close (or unknown-time) report needs
    an expiry after its day; a pre-open or intraday one can expire that day."""
    for raw in sorted(expiries):
        try:
            day = date.fromisoformat(raw)
        except ValueError:
            continue
        if day > report or (day == report and when in ("pre", "during")):
            return raw
    return None


def _mid(q: dict) -> float | None:
    bid, ask, last = _num(q.get("bid")) or 0.0, _num(q.get("ask")) or 0.0, _num(q.get("lastPrice"))
    if bid > 0 and ask >= bid:
        mid = (bid + ask) / 2
        return mid if (ask - bid) / mid <= WIDE_SPREAD else None
    if bid > 0 or ask > 0:    # one side only: as wide as a quote gets, no price
        return None
    return last if last and last > 0 else None


def implied_move(calls: list[dict], puts: list[dict], spot: float, expiry: str) -> dict | None:
    """The at-the-money straddle (call + put at the strike nearest the price, both sides quoted) over
    the price: the move the options price in by ``expiry``. None without a fair quote."""
    if not spot or spot <= 0:
        return None
    call_by = {_num(c.get("strike")): c for c in calls}
    put_by = {_num(p.get("strike")): p for p in puts}
    strikes = sorted((s for s in call_by if s is not None and s in put_by), key=lambda s: abs(s - spot))
    if not strikes:
        return None
    strike = strikes[0]
    call, put = _mid(call_by[strike]), _mid(put_by[strike])
    if call is None or put is None:
        return None
    return {"expiry": expiry, "strike": strike, "call": round(call, 2), "put": round(put, 2),
            "move_pct": round((call + put) / spot * 100, 1)}


def past_moves(reports: list[dict], closes: list[tuple[date, float]], limit: int = HISTORY,
               unknown_same_day: bool = False) -> dict | None:
    """For each past report: the close-to-close move on the first session that could react - the
    report's own day when it came before the open or during the session, else the next session.
    ``unknown_same_day``: a report without a time reacts that day (Korean reports come out in the
    morning, before or during the session)."""
    if not closes:
        return None
    days = [d for d, _ in closes]
    price = dict(closes)
    moves = []
    for r in reports:
        if r["actual"] is None:
            continue
        report = date.fromisoformat(r["date"])
        same = r["when"] in ("pre", "during") or (r["when"] is None and unknown_same_day)
        first = report if same else report + timedelta(days=1)
        i = next((k for k, d in enumerate(days) if d >= first), None)
        if i is None or i == 0:
            continue
        before, after = price[days[i - 1]], price[days[i]]
        if before:
            moves.append({"date": r["date"], "session": days[i].isoformat(),
                          "move_pct": round((after / before - 1) * 100, 1)})
        if len(moves) >= limit:
            break
    if not moves:
        return None
    return {"moves": moves, "avg_abs_pct": round(sum(abs(m["move_pct"]) for m in moves) / len(moves), 1),
            "up": sum(1 for m in moves if m["move_pct"] > 0), "n": len(moves)}


def _naver_value(raw) -> float | None:
    if not isinstance(raw, str) or raw.strip() in ("", "-"):
        return None
    return _num(raw.replace(",", ""))


def naver_consensus(payload: dict) -> list[dict]:
    """Naver's finance table -> [{period, consensus, revenue, op_income, net_income, op_margin}] oldest
    first; money in 억원 (as Naver shows it), the margin in %."""
    info = (payload or {}).get("financeInfo") or {}
    titles = info.get("trTitleList") or []
    rows = {NAVER_ROWS[r["title"]]: r.get("columns") or {} for r in info.get("rowList") or []
            if r.get("title") in NAVER_ROWS}
    out = []
    for t in titles:
        key = t.get("key")
        if not key:
            continue
        col = {name: _naver_value(((rows.get(name) or {}).get(key) or {}).get("value"))
               for name in NAVER_ROWS.values()}
        out.append({"period": str(t.get("title") or key).rstrip("."), "consensus": t.get("isConsensus") == "Y", **col})
    return out


# --- fetching (blocking: run in a worker thread) ---------------------------------------------

def _records(frame) -> dict[str, dict]:
    return {} if frame is None or getattr(frame, "empty", True) else frame.to_dict("index")


def yahoo(ticker: str, now: datetime) -> tuple[dict, list[str]]:
    """Every Yahoo part of the preview, each on its own (a failure is named in the errors)."""
    import yfinance as yf

    from . import positioning
    from .prices import yahoo_candles

    korean = is_korean(ticker)
    t = yf.Ticker(ticker)
    out: dict = {}
    errors: list[str] = []

    def part(name: str, fn: Callable[[], object]) -> None:
        try:
            value = fn()
            if value is not None:
                out[name] = value
        except Exception as exc:  # noqa: BLE001 - an unofficial source: one part down leaves the rest
            errors.append(f"{name}: {type(exc).__name__}")
            log.info("earnings preview %s %s failed: %s", ticker, name, exc)

    part("eps", lambda: estimates(_records(t.earnings_estimate)))
    part("revenue", lambda: estimates(_records(t.revenue_estimate)))

    def dated():
        frame = t.get_earnings_dates(limit=HISTORY + 4)
        if frame is None or frame.empty:
            return None
        return history([(ts.to_pydatetime(), row) for ts, row in frame.to_dict("index").items()], korean)
    part("history", dated)

    def calendar_dates():
        cal = t.calendar or {}
        return [d.date() if isinstance(d, datetime) else d for d in cal.get("Earnings Date") or []
                if isinstance(d, date)]
    cal: list[date] = []
    part("calendar", lambda: cal.extend(calendar_dates()))
    part("next", lambda: next_report(out.get("history") or [], cal, now, korean))
    part("targets", lambda: targets(t.analyst_price_targets))
    part("ratings", lambda: ratings(list(_records(t.recommendations_summary).values())))

    def moves():
        bars = yahoo_candles(ticker, "1d", 600)
        from .sr import _local_day

        return past_moves(out.get("history") or [], [(_local_day(b.at, ticker), b.close) for b in bars],
                          unknown_same_day=korean)
    part("past_moves", moves)

    if positioning.is_us_ticker(ticker):
        part("short_interest", lambda: positioning.short_interest_from(t.info or {}))
        nxt = out.get("next")

        def implied():
            if not nxt or (nxt.get("days") or 0) < 0:      # reported already (actual not in yet)
                return None
            expiry = pick_expiry(list(t.options or ()), date.fromisoformat(nxt["date"]), nxt["when"])
            spot = (out.get("targets") or {}).get("current") or _num(getattr(t.fast_info, "last_price", None))
            if not expiry or not spot:
                return None
            chain = t.option_chain(expiry)
            move = implied_move(chain.calls.to_dict("records"), chain.puts.to_dict("records"), spot, expiry)
            if move:
                # far from the report the weekly expiry is not listed yet: a later one also prices the
                # weeks after it, so the move reads larger - the page says how far
                move["days_after_report"] = (date.fromisoformat(expiry) - date.fromisoformat(nxt["date"])).days
            return move
        part("implied_move", implied)
    return out, errors


def naver(ticker: str) -> tuple[dict | None, list[str]]:
    code = ticker.split(".")[0]
    out, errors = {}, []
    for kind in ("quarter", "annual"):
        try:
            r = httpx.get(NAVER_URL.format(code=code, kind=kind), headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            r.raise_for_status()
            rows = naver_consensus(r.json())
            if rows:
                out[kind] = rows
        except Exception as exc:  # noqa: BLE001 - unofficial: the rest of the preview stands without it
            errors.append(f"kr_consensus.{kind}: {type(exc).__name__}")
    return ({"unit": "억원", **out} if out else None), errors


def _shown(rows: list[dict]) -> list[dict]:
    """The next report (the latest one not reported) and the last HISTORY reported ones, newest first."""
    pending = [r for r in rows if r["actual"] is None]
    done = [r for r in rows if r["actual"] is not None][:HISTORY]
    return ([min(pending, key=lambda r: r["at"])] if pending else []) + done


def blank(ticker: str, errors: list[str], now: datetime | None = None) -> dict:
    """A preview with nothing in it (the whole fetch failed): the same keys, the reason in ``errors``."""
    now = now or datetime.now(UTC)
    return {"ticker": ticker, "next": None, "eps": None, "revenue": None, "history": [], "implied_move": None,
            "past_moves": None, "targets": None, "ratings": None, "short_interest": None, "kr_consensus": None,
            "sources": {}, "unavailable": UNAVAILABLE, "errors": errors, "fetched_at": now.isoformat(timespec="seconds"),
            "note": "공개 추정치와 시세로 계산한 값이며 예측이 아닙니다"}


def build(ticker: str, now: datetime | None = None) -> dict:
    """The whole preview (blocking)."""
    now = now or datetime.now(UTC)
    parts, errors = yahoo(ticker, now)
    kr = None
    if is_korean(ticker):
        kr, more = naver(ticker)
        errors += more
    return {"ticker": ticker, "next": parts.get("next"), "eps": parts.get("eps"), "revenue": parts.get("revenue"),
            "history": _shown(parts.get("history") or []), "implied_move": parts.get("implied_move"),
            "past_moves": parts.get("past_moves"), "targets": parts.get("targets"), "ratings": parts.get("ratings"),
            "short_interest": parts.get("short_interest"), "kr_consensus": kr,
            "sources": {k: {"source": SOURCES[k], "at": now.isoformat(timespec="seconds")} for k in SOURCES
                        if (kr if k == "kr_consensus" else parts.get("eps" if k == "estimates" else k))},
            "unavailable": UNAVAILABLE, "errors": errors, "fetched_at": now.isoformat(timespec="seconds"),
            "note": "공개 추정치와 시세로 계산한 값이며 예측이 아닙니다"}


def checkpoints(ticker: str, limit: int = 3) -> list[dict]:
    """The ticker's watched-topic headlines (desk.sector_news) that already have the digest's Korean
    read (desk.news_digest) - newest first. No model call of its own."""
    from .news_digest import DIGEST
    from .sector_news import CACHE as SECTORS
    from .sector_news import SECTORS as ALL

    mine = {t.id for s in ALL for t in s.topics if ticker in t.tickers}
    heads = [h for tid, row in SECTORS.topics.items() if tid in mine for h in row.get("headlines") or []]
    out, seen = [], set()
    for h in sorted(heads, key=lambda h: h.get("seen") or "", reverse=True):
        r = DIGEST.reads.get(h.get("url") or "")
        if not r or not r.get("title_ko") or h["url"] in seen:
            continue
        seen.add(h["url"])
        out.append({"title_ko": r["title_ko"], "why_ko": r.get("why_ko"), "sentiment": r.get("sentiment"),
                    "impact": r.get("impact"), "url": h["url"], "seen": h.get("seen")})
        if len(out) >= limit:
            break
    return out


class Cache:
    """One preview per ticker, at most MAX_TICKERS of them (least recently used dropped). One fetch at
    a time per ticker, run as its own task: a request that gives up (WAIT_SEC, or the client gone)
    leaves the fetch to finish and be kept. A fetch that fails is kept as an empty preview for
    FAIL_TTL_SEC, so a broken ticker is not fetched again on every request."""

    def __init__(self, fetch: Callable[[str], dict] = build, clock: Callable[[], float] = time.monotonic,
                 limit: int = MAX_TICKERS) -> None:
        self._fetch = fetch
        self._clock = clock
        self._limit = limit
        self._values: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        self._tasks: dict[str, asyncio.Task] = {}

    def _keep(self, ticker: str, value: dict) -> None:
        now = self._clock()
        for k in [k for k, (until, _) in self._values.items() if until <= now]:
            del self._values[k]
        self._values[ticker] = (now + ttl(value), value)
        self._values.move_to_end(ticker)
        while len(self._values) > self._limit:
            self._values.popitem(last=False)

    async def _run(self, ticker: str) -> dict:
        try:
            value = await asyncio.to_thread(self._fetch, ticker)
        except Exception as exc:  # noqa: BLE001 - never a 500: the page says what failed
            log.info("earnings preview %s failed: %s", ticker, exc)
            value = blank(ticker, [f"preview: {type(exc).__name__}"])
        self._keep(ticker, value)
        return value

    async def get(self, ticker: str, wait: float = WAIT_SEC) -> dict:
        hit = self._values.get(ticker)
        if hit and hit[0] > self._clock():
            self._values.move_to_end(ticker)
            return hit[1]
        task = self._tasks.get(ticker)
        if task is None:
            task = asyncio.ensure_future(self._run(ticker))
            self._tasks[ticker] = task
            task.add_done_callback(lambda _t, k=ticker: self._tasks.pop(k, None))
        try:
            return await asyncio.wait_for(asyncio.shield(task), wait)
        except TimeoutError:
            return blank(ticker, ["preview: still loading"])


def ttl(value: dict) -> float:
    if not any(value.get(k) for k in ("eps", "revenue", "history", "targets", "kr_consensus")):
        return FAIL_TTL_SEC
    days = (value.get("next") or {}).get("days")
    return NEAR_TTL_SEC if days is not None and abs(days) <= 1 else TTL_SEC


CACHE = Cache()
