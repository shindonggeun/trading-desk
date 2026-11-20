"""KOSPI200 front-month futures price for the day and the night session, with minute bars (#443).

desk.futures_flow (#160) shows the day session's front month next to the investor flows. This adds
the price itself for both KRX derivatives sessions:

- **day** 08:45 ~ 15:45 KST, KIS market division ``F``;
- **night** 18:00 ~ 06:00 the next morning (KRX night derivatives since 2025-06), division ``CM`` -
  the same contract code as the day front month (checked live on 2026-11-16, issue #443).

Two KIS REST calls on the client desk.futures_flow already holds (one token for both):
``inquire-price`` (FHMIF10000000) and ``inquire-time-fuopchartprice`` (FHKIF03020200, one-minute
bars, newest first, 102 a call - paged back to the session's start). KIS' own base price
(``futs_sdpr``) is the previous settlement in the day session and the day's close in the night one,
and its change / change rate are against it.

A failed refresh hands back the last good answer marked ``stale``. Secrets stay in desk.futures_flow
(KisRest never logs or returns the key, secret or token); errors here carry its short code only.

#713 both sessions, always (``both``): ``sessions.day`` and ``sessions.night`` each with ``state`` open or
closed, the top level the session trading now or, with both closed (06:00 ~ 08:44, 15:45 ~ 17:59,
weekends), the one that ended last - so the night's close shows until the day opens. A closed session
keeps the last good quote of that very session (``held``) when KIS answers it empty, and with none kept
(a restart) its last minute bar's close stands in (``from_bars``: no change against the base). Its
minute bars likewise: KIS answers a closed night's minutes empty, so the ones taken while it traded
stay (``held``). A new session is a new cache key (its opening day), so 08:45 and 18:00 are read fresh.
What is kept lives in this process: after a restart a closed session shows what KIS still gives.
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from datetime import UTC, datetime, time, timedelta

from . import market_calendar
from .futures_flow import KST, PRICE_PATH, PRICE_TR, KisRest, _num, _short, front_month

log = logging.getLogger(__name__)

CANDLE_PATH = "/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice"
CANDLE_TR = "FHKIF03020200"
DIVISION = {"day": "F", "night": "CM"}
SESSIONS = {"day": (time(8, 45), time(15, 45), 0), "night": (time(18, 0), time(6, 0), 1)}   # start, end, end + days
BASE_LABEL = {"day": "전일 정산가", "night": "주간 종가"}
INTERVALS = {"1m": 1, "5m": 5, "15m": 15}
PAGE_ROWS = 102
MAX_PAGES = 9                     # 720 night minutes / ~100 a page, and one spare
CACHE_OPEN_SEC = 15.0
CACHE_CLOSED_SEC = 600.0
CACHE_FAIL_SEC = 20.0             # a stale answer after a failed refresh, before KIS is asked again
PAGE_PAUSE_SEC = 0.25
LOOKBACK_DAYS = 10                # to find the last session past a long holiday


def _kst(now: datetime) -> datetime:
    return now.astimezone(KST)


def last_session(kind: str, now: datetime, is_open=market_calendar.is_open) -> dict:
    """The most recent ``kind`` session that has started by ``now``: its trading day (the day it
    starts on - a night belongs to the evening it opens), start and end (KST)."""
    local = _kst(now)
    start_t, end_t, plus = SESSIONS[kind]
    for back in range(LOOKBACK_DAYS + 1):
        d = local.date() - timedelta(days=back)
        if not is_open("kr", d):       # KRX holidays (market_calendar keys are lower case)
            continue
        start = datetime.combine(d, start_t, KST)
        if start <= local:
            return {"session": kind, "date": d.isoformat(), "start": start,
                    "end": datetime.combine(d + timedelta(days=plus), end_t, KST)}
    raise ValueError("no futures session in the last days")


def futures_session(now: datetime, is_open=market_calendar.is_open) -> dict:
    """``{"now": day|night|closed, "shown": day|night, ...}`` - ``shown`` is the session trading now,
    or the one that ended last (its date, start and end)."""
    local = _kst(now)
    picks = [last_session(k, now, is_open) for k in SESSIONS]
    live = next((p for p in picks if p["start"] <= local < p["end"]), None)
    shown = live or max(picks, key=lambda p: p["end"])
    return {**shown, "now": live["session"] if live else "closed", "shown": shown["session"]}


def normalize_quote(out1: dict, kind: str) -> dict:
    """KIS ``inquire-price`` output1 (numbers as strings) -> the quote's numbers."""
    return {
        "price": _num(out1.get("futs_prpr")),
        "change": _num(out1.get("futs_prdy_vrss")),
        "change_pct": _num(out1.get("futs_prdy_ctrt")),
        "prev_close": _num(out1.get("futs_sdpr")),
        "prev_close_basis": BASE_LABEL[kind],
        "open": _num(out1.get("futs_oprc")),
        "high": _num(out1.get("futs_hgpr")),
        "low": _num(out1.get("futs_lwpr")),
        "volume": _num(out1.get("acml_vol")),
        "open_interest": _num(out1.get("hts_otst_stpl_qty")),
        "open_interest_change": _num(out1.get("otst_stpl_qty_icdc")),
        "name": (out1.get("hts_kor_isnm") or "").strip() or None,
    }


def bar_time(row: dict, session: dict) -> datetime | None:
    """A minute bar's KST time. A night bar after midnight may carry the session's (opening) day,
    so an early-morning hour on that day is moved to the next one."""
    try:
        d = datetime.strptime(row["stck_bsop_date"], "%Y%m%d").date()
        h = row["stck_cntg_hour"].zfill(6)
        at = datetime.combine(d, time(int(h[:2]), int(h[2:4])), KST)
    except (KeyError, ValueError, TypeError):
        return None
    if session["session"] == "night" and d.isoformat() == session["date"] and at.time() < SESSIONS["night"][0]:
        at += timedelta(days=1)
    return at


def normalize_bars(rows: list[dict], session: dict) -> list[dict]:
    """KIS minute rows (newest first, strings) -> one-minute bars inside the session, oldest first."""
    out: dict[datetime, dict] = {}
    for row in rows:
        at = bar_time(row, session)
        c = _num(row.get("futs_prpr"))
        if at is None or c is None or not (session["start"] <= at <= session["end"]):
            continue
        out[at] = {"t": at.isoformat(), "o": _num(row.get("futs_oprc")) or c, "h": _num(row.get("futs_hgpr")) or c,
                   "l": _num(row.get("futs_lwpr")) or c, "c": c, "v": _num(row.get("cntg_vol")) or 0}
    return [out[k] for k in sorted(out)]


def resample(bars: list[dict], minutes: int, start: datetime) -> list[dict]:
    """One-minute bars -> ``minutes`` bars counted from the session's start (08:45, 18:00)."""
    if minutes == 1:
        return bars
    out: list[dict] = []
    key = None
    for b in bars:
        at = datetime.fromisoformat(b["t"])
        k = start + timedelta(minutes=((at - start) // timedelta(minutes=1)) // minutes * minutes)
        if k != key:
            out.append({**b, "t": k.isoformat()})
            key = k
        else:
            last = out[-1]
            last.update(h=max(last["h"], b["h"]), l=min(last["l"], b["l"]), c=b["c"], v=last["v"] + b["v"])
    return out


class FuturesQuote:
    """The cached quote and minute bars for /api/market/futures/quote and /candles."""

    def __init__(self, rest: KisRest | None, off: str | None = None, now=lambda: datetime.now(UTC),
                 clock=_time.monotonic, is_open=market_calendar.is_open) -> None:
        self.rest = rest
        self.off = off or "한국투자증권 키가 없어 선물 시세를 받을 수 없습니다"
        self._now, self._clock, self._is_open = now, clock, is_open
        self._cache: dict[tuple, tuple[float, float, dict]] = {}
        self._locks: dict[tuple, asyncio.Lock] = {}   # one per answer: a paging candle fetch does not hold the quote
        # #713 (session, opening day) -> its last quote with a price; ("bars", session, day) -> its minutes
        self._good: dict[tuple, dict | list] = {}

    def _session(self, kind: str | None) -> dict:
        now = self._now()
        current = futures_session(now, self._is_open)
        if kind is None or kind == current["shown"]:
            return current
        picked = last_session(kind, now, self._is_open)
        return {**picked, "now": current["now"], "shown": kind}

    async def _cached(self, key: tuple, live: bool, fetch) -> dict:
        async with self._locks.setdefault(key, asyncio.Lock()):
            hit = self._cache.get(key)
            if hit and self._clock() - hit[0] < hit[1]:
                return hit[2]
            try:
                value = await fetch()
            except Exception as exc:   # KIS down, refused or slow: the last answer, marked
                log.warning("futures quote %s failed: %s", key[0], _short(exc))
                if hit:
                    stale = {**hit[2], "stale": True, "error": f"새 값을 받지 못해 마지막 값을 보입니다 — {_short(exc)}"}
                    # kept a little while too: a KIS outage is not asked again on every request
                    self._cache[key] = (self._clock(), CACHE_FAIL_SEC, stale)
                    return stale
                raise
            self._cache[key] = (self._clock(), CACHE_OPEN_SEC if live else CACHE_CLOSED_SEC, value)
            return value

    def _base(self, s: dict, front: dict) -> dict:
        return {"supported": True, "error": None, "stale": False, "source": "kis",
                "session": s["shown"], "market": s["now"], "session_date": s["date"],
                "start": s["start"].isoformat(), "end": s["end"].isoformat(),
                "contract": {"code": front["code"], "expiry": front["expiry"], "days_left": front["days_left"]},
                "as_of": _kst(self._now()).isoformat(timespec="seconds")}

    async def quote(self, session: str | None = None) -> dict:
        s = self._session(session)
        if self.rest is None:
            return {"supported": False, "error": self.off, "session": s["shown"], "market": s["now"]}
        front = front_month(_kst(self._now()).date())

        async def fetch() -> dict:
            res = await self.rest.get(PRICE_PATH, PRICE_TR, {"FID_COND_MRKT_DIV_CODE": DIVISION[s["shown"]],
                                                             "FID_INPUT_ISCD": front["code"]})
            q = normalize_quote(res.get("output1") or {}, s["shown"])
            if q["price"] is None:
                kept = await self._closed_stand_in(s, front)
                if kept is not None:
                    return kept
                raise RuntimeError(f"빈 시세(종목 {front['code']})")
            name = q.pop("name")
            out = self._base(s, front)
            got = {**out, "contract": {**out["contract"], "name": name}, **q}
            self._good[(s["shown"], s["date"])] = got
            return got

        return await self._cached(("quote", s["shown"], s["date"]), s["now"] == s["shown"], fetch)

    async def _closed_stand_in(self, s: dict, front: dict) -> dict | None:
        """#713 a closed session answered empty: its last good quote (``held``), else its last minute bar's
        close (``from_bars``); None while it trades (an empty quote then is a real fault) or with neither."""
        if s["now"] == s["shown"]:
            return None
        kept = self._good.get((s["shown"], s["date"]))
        if kept is not None:
            return {**kept, **self._base(s, front), "contract": kept["contract"], "held": True}
        try:
            bars = (await self.candles(s["shown"]))["bars"]
        except Exception as exc:   # noqa: BLE001 - no stand-in then; the caller says the quote is empty
            log.info("futures %s stand-in bars failed: %s", s["shown"], _short(exc))
            return None
        if not bars:
            return None
        last = bars[-1]
        return {**self._base(s, front), "price": last["c"], "change": None, "change_pct": None, "prev_close": None,
                "prev_close_basis": BASE_LABEL[s["shown"]], "open": bars[0]["o"], "high": max(b["h"] for b in bars),
                "low": min(b["l"] for b in bars), "volume": sum(b["v"] for b in bars), "open_interest": None,
                "open_interest_change": None, "from_bars": True, "bar_at": last["t"]}

    async def both(self) -> dict:
        """#713 the session shown now at the top level, and ``sessions`` day and night each with its ``state``
        (a session failing alone carries its error)."""
        current = futures_session(self._now(), self._is_open)
        sessions: dict[str, dict] = {}
        for kind in SESSIONS:
            try:
                got = await self.quote(kind)
            except Exception as exc:   # noqa: BLE001 - the other session still shows
                got = {"supported": True, "error": f"선물 시세를 받지 못했습니다 — {_short(exc)}", "stale": False,
                       "session": kind, "market": current["now"]}
            sessions[kind] = {**got, "state": "open" if current["now"] == kind else "closed"}
        return {**sessions[current["shown"]], "current": current["shown"], "sessions": sessions}

    async def candles(self, session: str | None = None, interval: str = "1m") -> dict:
        if interval not in INTERVALS:
            raise ValueError(f"interval must be one of {', '.join(INTERVALS)}")
        s = self._session(session)
        if self.rest is None:
            return {"supported": False, "error": self.off, "session": s["shown"], "market": s["now"], "bars": []}
        front = front_month(_kst(self._now()).date())

        async def fetch() -> dict:
            end = min(_kst(self._now()), s["end"])
            rows: list[dict] = []
            for page_no in range(MAX_PAGES):
                if page_no:
                    await asyncio.sleep(PAGE_PAUSE_SEC)   # KIS counts calls a second, on the key the snapshots share
                res = await self.rest.get(CANDLE_PATH, CANDLE_TR, {
                    "FID_COND_MRKT_DIV_CODE": DIVISION[s["shown"]], "FID_INPUT_ISCD": front["code"],
                    "FID_HOUR_CLS_CODE": "60", "FID_PW_DATA_INCU_YN": "Y", "FID_FAKE_TICK_INCU_YN": "N",
                    "FID_INPUT_DATE_1": end.strftime("%Y%m%d"), "FID_INPUT_HOUR_1": end.strftime("%H%M%S")})
                page = res.get("output2") or []
                rows += page
                times = [t for t in (bar_time(r, s) for r in page) if t]
                if len(page) < PAGE_ROWS or not times or min(times) <= s["start"]:
                    break
                end = min(times) - timedelta(minutes=1)
            bars = normalize_bars(rows, s)
            key = ("bars", s["shown"], s["date"])
            if bars:
                self._good[key] = bars
            elif s["now"] != s["shown"] and key in self._good:
                # #713 KIS answers a closed session's minutes empty (night bars at 11:37, 2026-11-20): the
                # ones taken while it traded
                return {**self._base(s, front), "bars": self._good[key], "held": True}
            return {**self._base(s, front), "bars": bars}

        # the one-minute bars are fetched once a session; each interval is cut from them
        one = await self._cached(("candles", s["shown"], s["date"]), s["now"] == s["shown"], fetch)
        return {**one, "interval": interval, "bars": resample(one["bars"], INTERVALS[interval], s["start"])}
