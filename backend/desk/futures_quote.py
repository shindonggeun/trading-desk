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
#1020 the bars kept outlive the process: each session's minutes are saved to a file on the volume the three services
share (``BARS_DIR``, as desk.feed_snapshots' - not Redis, whose keys a deploy empties), and a closed session KIS
answers empty with nothing in memory (a restart, a deploy, or nobody looked while it traded) answers that file's
bars when they are that very session's (``held``). The ``futures_close_quote`` job reads the bars with the close
(CLOSE_READ_SEC before the end) and once more CLOSE_BARS_AFTER_SEC after it (the last minute), screen or not.
A held quote keeps its own ``as_of`` (``held_at``); one taken more than HELD_CLOSE_SEC before its session
ended is ``held_early`` - not the close. So that the close is what is held, the ``futures_close_quote`` job
reads each session ``CLOSE_READ_SEC`` before it ends (``seconds_to_close_read``). KIS answering a price
of 0 counts as empty.

#689 split roles (api · collector, Redis on - desk.demand): the api answers the card from the collector's
values instead of calling KIS every 15 s itself. Each read in the api marks its answer's key wanted
(``quote:night:2026-11-16``, ``candles:day:2026-11-17`` - answer, session and opening day, what the memory
cache keys on). The collector's ``serve`` reads that key as its own reads would (its memory first, never the
shared tier) and shares the answer - the held close and the last-bar stand-in too: its ``futures_close_quote``
job is what holds the close - for at least SHARED_KEEP_SEC. A failure or a stale answer is not shared. The api
reads Redis on a memory miss (and over a failure it keeps), fills its memory and held values from it, and calls
KIS itself only when Redis has nothing (a cold start, the collector or Redis down), keeping that answer at most
FALLBACK_SEC so that the collector's replaces it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time as _time
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from . import demand, market_calendar, shared_cache
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
CLOSE_READ_SEC = 30.0             # #713 each session's quote read this long before it ends (its close, held)
HELD_CLOSE_SEC = 120.0            # a held quote taken earlier than this before the end is not the close
MAX_SLEEP_SEC = 3600.0
SHARED_NS = "futures_quote"       # #689 desk.demand's namespace, and the shared values'
ANSWERS = ("quote", "candles")    # what a key names: the quote, or the session's one-minute bars
# #689 a shared value lives at least this long in Redis: the collector rewrites a wanted key every
# demand.EVERY (15 s) and waits 30 s after a failed fetch, so three periods carry the api over one failed or
# late pass on the collector's value (older than its own ttl then - its as_of says so) instead of KIS
SHARED_KEEP_SEC = 3 * demand.EVERY[SHARED_NS]
FALLBACK_SEC = 60.0               # #689 the api's own fetch (Redis had nothing) is kept at most this long
HELD_KEYS = ("held", "held_at", "held_early")
# #1020 a session's minute bars saved here (futures-bars-<session>.json, the latest session of each), so that a closed
# one outlives a restart or a deploy; the backend's volume, shared by the api, the collector and the analyst
BARS_DIR = Path.home() / ".tradingagents" / "desk-cache"
BARS_SAVE_SEC = 300.0             # while a session trades its bars are saved at most this often (each close read too)
CLOSE_BARS_AFTER_SEC = 60.0       # the close read job reads the bars once more this long after the bell
MAX_SAVED_BARS = 2000             # a file with more is not this module's (a night has 721 minutes)
BAR_KEYS = ("o", "h", "l", "c", "v")


def _bars_path(kind: str) -> Path:
    return BARS_DIR / f"futures-bars-{kind}.json"


def _valid_bars(bars: object) -> list[dict] | None:
    """Saved bars as this module writes them (oldest first, ``t`` ISO and finite numbers), else None."""
    if not isinstance(bars, list) or not 0 < len(bars) <= MAX_SAVED_BARS:
        return None
    last = ""
    for b in bars:
        if not isinstance(b, dict) or not isinstance(b.get("t"), str) or b["t"] <= last:
            return None
        if not all(isinstance(b.get(k), int | float) and not isinstance(b.get(k), bool) and math.isfinite(b[k])
                   for k in BAR_KEYS):
            return None
        last = b["t"]
    return bars


def load_bars(kind: str, day: str) -> list[dict] | None:
    """#1020 the bars saved for the ``kind`` session that opened on ``day``, or None (none saved, another
    session's, or a file that is not one). Blocking: off the event loop."""
    path = _bars_path(kind)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        log.info("futures %s bars file unreadable: %s", kind, type(exc).__name__)
        return None
    if not isinstance(data, dict) or data.get("session") != kind or data.get("date") != day:
        return None
    return _valid_bars(data.get("bars"))


def save_bars(kind: str, day: str, bars: list[dict]) -> bool:
    """#1020 ``bars`` of the ``kind`` session that opened on ``day`` written whole (a temporary file replaced in one
    step) - unless the file already holds that session up to a later or the same minute (another process's, or a
    longer read). Blocking: off the event loop. True when written."""
    if _valid_bars(bars) is None:
        return False
    kept = load_bars(kind, day)
    if kept is not None and (kept[-1]["t"], len(kept)) >= (bars[-1]["t"], len(bars)):
        return False
    path = _bars_path(kind)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")     # one per process: the three share the volume
        tmp.write_text(json.dumps({"session": kind, "date": day, "bars": bars,
                                   "saved_at": datetime.now(UTC).isoformat(timespec="seconds")}), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        log.warning("futures %s bars not saved: %s", kind, type(exc).__name__)
        return False
    return True


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


def seconds_to_close_read(now: datetime, is_open=market_calendar.is_open) -> float:
    """#713 until the next session's end less CLOSE_READ_SEC (05:59:30, 15:44:30 on trading days) - #1020 or
    CLOSE_BARS_AFTER_SEC past an end (06:01, 15:46) - at most MAX_SLEEP_SEC (a holiday added later is seen within
    the hour)."""
    local = _kst(now)
    targets = []
    for back in range(-1, LOOKBACK_DAYS):
        d = local.date() - timedelta(days=back) if back >= 0 else local.date() + timedelta(days=1)
        if not is_open("kr", d):
            continue
        for _, end_t, plus in SESSIONS.values():
            end = datetime.combine(d + timedelta(days=plus), end_t, KST)
            # #1020 and once more after the bell: the bars' last minute
            for at in (end - timedelta(seconds=CLOSE_READ_SEC), end + timedelta(seconds=CLOSE_BARS_AFTER_SEC)):
                if at > local:
                    targets.append(at)
    if not targets:
        return MAX_SLEEP_SEC
    return max(1.0, min((min(targets) - local).total_seconds(), MAX_SLEEP_SEC))


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


def demand_key(key: tuple) -> str:
    """#689 a memory cache key ("quote", "night", "2026-11-16") as desk.demand's and the shared value's key."""
    return ":".join(key)


def parse_key(text: str) -> tuple[str, str, str]:
    """``demand_key``'s inverse: (answer, session, opening day); ValueError for anything else."""
    parts = text.split(":") if isinstance(text, str) else []
    if len(parts) == 3 and parts[0] in ANSWERS and parts[1] in SESSIONS:
        try:
            if date.fromisoformat(parts[2]).isoformat() == parts[2]:
                return parts[0], parts[1], parts[2]
        except ValueError:
            pass
    raise ValueError("not a futures quote key")


def _sound(value) -> bool:
    """A cached answer that is the fetch's own: not a failure, nor the last one marked stale after one."""
    return not isinstance(value, BaseException) and not value.get("stale")


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
        self._saved: dict[str, tuple[str, str, float]] = {}   # #1020 session -> (day, last bar, when) saved here

    def _session(self, kind: str | None) -> dict:
        now = self._now()
        current = futures_session(now, self._is_open)
        if kind is None or kind == current["shown"]:
            return current
        picked = last_session(kind, now, self._is_open)
        return {**picked, "now": current["now"], "shown": kind}

    async def _cached(self, key: tuple, live: bool, fetch) -> dict:
        split = demand.enabled()       # #689 the api of split roles: the collector keeps what it reads in Redis
        if split:
            demand.want(SHARED_NS, demand_key(key))   # every read, a memory hit too: it keeps the key collected
        async with self._locks.setdefault(key, asyncio.Lock()):
            hit = self._cache.get(key)
            fresh = hit is not None and self._clock() - hit[0] < hit[1]
            if fresh and _sound(hit[2]):
                return hit[2]
            if split:   # a miss, or a failure kept here: the collector's value comes first
                shared = await self._shared(key)
                if shared is not None:
                    return shared
            if fresh:
                if isinstance(hit[2], BaseException):
                    raise hit[2]
                return hit[2]
            value, _ttl = await self._refresh(key, live, fetch, hit, FALLBACK_SEC if split else None)
            return value

    async def _refresh(self, key: tuple, live: bool, fetch, hit, keep: float | None = None) -> tuple[dict, float]:
        """Fetch ``key`` into memory now: (the answer, its ttl), kept ``keep`` at most. A failure answers the
        last one marked stale (ttl 0 - never shared) or raises."""
        try:
            value = await fetch()
        except Exception as exc:   # KIS down, refused or slow: the last answer, marked
            log.warning("futures quote %s failed: %s", key[0], _short(exc))
            if hit and not isinstance(hit[2], BaseException):
                stale = {**hit[2], "stale": True, "error": f"새 값을 받지 못해 마지막 값을 보입니다 — {_short(exc)}"}
                # kept a little while too: a KIS outage is not asked again on every request
                self._cache[key] = (self._clock(), CACHE_FAIL_SEC, stale)
                return stale, 0.0
            # #713 nothing to show either: the failure itself is kept a little while (every poll would ask)
            self._cache[key] = (self._clock(), CACHE_FAIL_SEC, exc)
            raise
        ttl = CACHE_OPEN_SEC if live else CACHE_CLOSED_SEC
        self._cache[key] = (self._clock(), ttl if keep is None else min(ttl, keep), value)
        self._forget_before(key)
        return value, ttl

    async def _shared(self, key: tuple) -> dict | None:
        """#689 the collector's answer for ``key`` from Redis into memory (and the held values), or None."""
        hit = await shared_cache.aget(SHARED_NS, demand_key(key))
        got = shared_cache.latest(hit)
        if got is None or (key[0] == "candles" and not isinstance(got[0].get("bars"), list)):
            return None
        value, ttl, left = got
        # held for what is left of the answer's own ttl (written ``age`` ago), so the next read takes the
        # collector's next write; one past its ttl (a late pass) answers while Redis keeps it, read each time
        hold = max(0.0, min(left, ttl - hit[1]))
        self._cache[key] = (self._clock() - (ttl - hold), ttl, value)
        self._keep_good(key, value)
        self._forget_before(key)
        return value

    def _keep_good(self, key: tuple, value: dict) -> None:
        """#713 a shared answer is the session's last good one here too: when Redis has nothing later and KIS
        answers the closed session empty, the api holds it as the collector would (not a newer one it has)."""
        name, kind, day = key
        if name == "quote":
            price = value.get("price")
            if value.get("from_bars") or not isinstance(price, int | float) or price <= 0:
                return
            kept = self._good.get((kind, day))
            if kept is None or str(value.get("as_of") or "") >= str(kept.get("as_of") or ""):
                self._good[(kind, day)] = {k: v for k, v in value.items() if k not in HELD_KEYS}
            return
        bars = value.get("bars")
        if bars and isinstance(bars[-1], dict):
            kept = self._good.get(("bars", kind, day))
            if not kept or str(bars[-1].get("t") or "") >= str(kept[-1].get("t") or ""):
                self._good[("bars", kind, day)] = bars

    def _forget_before(self, key: tuple) -> None:
        """#713 one day per (answer, session) kept: an older day's cache, lock and held values go."""
        name, kind, day = key
        for k in [k for k in self._cache if k[:2] == (name, kind) and k[2] != day]:
            self._cache.pop(k, None)
            self._locks.pop(k, None)
        for k in [k for k in self._good if k[-2] == kind and k[-1] < day]:
            self._good.pop(k, None)

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
        return await self._cached(("quote", s["shown"], s["date"]), s["now"] == s["shown"],
                                  lambda: self._fetch_quote(s))

    async def _fetch_quote(self, s: dict) -> dict:
        front = front_month(_kst(self._now()).date())
        res = await self.rest.get(PRICE_PATH, PRICE_TR, {"FID_COND_MRKT_DIV_CODE": DIVISION[s["shown"]],
                                                         "FID_INPUT_ISCD": front["code"]})
        q = normalize_quote(res.get("output1") or {}, s["shown"])
        if q["price"] is None or q["price"] <= 0:          # KIS' empty: "" or 0
            kept = await self._closed_stand_in(s, front)
            if kept is not None:
                return kept
            raise RuntimeError(f"빈 시세(종목 {front['code']})")
        name = q.pop("name")
        out = self._base(s, front)
        got = {**out, "contract": {**out["contract"], "name": name}, **q}
        self._good[(s["shown"], s["date"])] = got
        return got

    async def _closed_stand_in(self, s: dict, front: dict) -> dict | None:
        """#713 a closed session answered empty: its last good quote (``held``), else its last minute bar's
        close (``from_bars``); None while it trades (an empty quote then is a real fault) or with neither."""
        if s["now"] == s["shown"]:
            return None
        kept = self._good.get((s["shown"], s["date"]))
        if kept is not None:
            early = s["end"] - datetime.fromisoformat(kept["as_of"]) > timedelta(seconds=HELD_CLOSE_SEC)
            return {**kept, "market": s["now"], "stale": False, "error": None, "held": True,
                    "held_at": kept["as_of"], "held_early": early}
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
        got = await asyncio.gather(*(self.quote(kind) for kind in SESSIONS), return_exceptions=True)
        sessions: dict[str, dict] = {}
        for kind, one in zip(SESSIONS, got, strict=True):
            if isinstance(one, BaseException):       # the other session still shows
                one = {"supported": True, "error": f"선물 시세를 받지 못했습니다 — {_short(one)}", "stale": False,
                       "session": kind}
            # a cached answer may predate the bell: the state is now's
            sessions[kind] = {**one, "market": current["now"], "state": "open" if current["now"] == kind else "closed"}
        return {**sessions[current["shown"]], "current": current["shown"], "sessions": sessions}

    async def read_close(self) -> str | None:
        """#713 the ``futures_close_quote`` job: the session ending within CLOSE_READ_SEC (+ a little) read now,
        so its held quote is its close - #1020 and its minute bars, saved; and the session that ended within
        CLOSE_BARS_AFTER_SEC (+ a little) its bars once more. The session read, or None."""
        now = _kst(self._now())
        s = futures_session(now, self._is_open)
        if s["now"] != "closed" and s["end"] - now <= timedelta(seconds=CLOSE_READ_SEC + 15):
            await self.quote(s["now"])
            await self._keep_bars(s["now"])        # #1020 its minutes with it, whether a screen looked or not
            return s["now"]
        # #1020 just after the bell: the bars once more, for the last minute (KIS may answer them empty by now)
        if s["now"] == "closed" and timedelta(0) <= now - s["end"] <= timedelta(seconds=CLOSE_BARS_AFTER_SEC + 30):
            await self._keep_bars(s["shown"])
            return s["shown"]
        return None

    async def _keep_bars(self, kind: str) -> None:
        """#1020 the session's bars read (as a screen's read would) and saved to its file now."""
        try:
            await self.candles(kind)
        except Exception as exc:  # noqa: BLE001 - the close read's quote stands; the next read tries again
            log.info("futures %s bars at the close failed: %s", kind, _short(exc))
            return
        s = self._session(kind)
        bars = self._good.get(("bars", kind, s["date"]))
        if bars:
            await self._save(kind, s["date"], bars, force=True)

    async def candles(self, session: str | None = None, interval: str = "1m") -> dict:
        if interval not in INTERVALS:
            raise ValueError(f"interval must be one of {', '.join(INTERVALS)}")
        s = self._session(session)
        if self.rest is None:
            return {"supported": False, "error": self.off, "session": s["shown"], "market": s["now"], "bars": []}
        # the one-minute bars are fetched once a session; each interval is cut from them
        one = await self._cached(("candles", s["shown"], s["date"]), s["now"] == s["shown"],
                                 lambda: self._fetch_candles(s))
        return {**one, "interval": interval, "bars": resample(one["bars"], INTERVALS[interval], s["start"])}

    async def _fetch_candles(self, s: dict) -> dict:
        front = front_month(_kst(self._now()).date())
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
            await self._save(s["shown"], s["date"], bars, force=s["now"] != s["shown"])
        elif s["now"] != s["shown"]:
            # #713 KIS answers a closed session's minutes empty (night bars at 11:37, 2026-11-20): the
            # ones taken while it traded - #1020 or, none here (a restart), the ones saved for that session
            if key not in self._good and (saved := await asyncio.to_thread(load_bars, s["shown"], s["date"])):
                self._good[key] = saved
            if key in self._good:
                return {**self._base(s, front), "bars": self._good[key], "held": True}
        return {**self._base(s, front), "bars": bars}

    async def _save(self, kind: str, day: str, bars: list[dict], force: bool = False) -> None:
        """#1020 the session's bars to its file: newer ones only, while it trades at most every BARS_SAVE_SEC."""
        last = self._saved.get(kind)
        if last is not None and last[0] == day and (
                last[1] >= bars[-1]["t"] or (not force and self._clock() - last[2] < BARS_SAVE_SEC)):
            return
        if await asyncio.to_thread(save_bars, kind, day, bars) or last is None or last[0] != day:
            self._saved[kind] = (day, bars[-1]["t"], self._clock())

    async def serve(self, key: str) -> None:
        """#689 the collector (desk.demand's handler): the wanted ``key`` (``demand_key``) read as this process
        answers it - its own memory first, then KIS; never the shared tier (it would read its own value back) -
        and shared for the api with the ttl left. A closed session's held close (or last-bar stand-in) is shared
        too: this process' ``futures_close_quote`` job holds it, the api answers the card. A failure, or the
        last answer marked stale after one, raises (desk.demand backs the key off) and shares nothing. A key
        that is not one raises ValueError; one of a session no longer shown here (the api's next read wants
        the new one) does nothing."""
        name, kind, day = parse_key(key)
        if self.rest is None:
            raise RuntimeError("no KIS client")
        s = self._session(kind)
        if s["date"] != day:
            log.debug("futures quote: the %s session of %s is not the one shown now", kind, day)
            return
        mem = (name, kind, day)
        fetch = self._fetch_quote if name == "quote" else self._fetch_candles
        async with self._locks.setdefault(mem, asyncio.Lock()):
            hit = self._cache.get(mem)
            left = hit[1] - (self._clock() - hit[0]) if hit is not None else 0.0
            if left > 0:   # what this process' reads answer now (a failure kept: not asked again yet)
                if isinstance(hit[2], BaseException):
                    raise hit[2]
                value, ttl = hit[2], left
            else:
                value, ttl = await self._refresh(mem, s["now"] == s["shown"], lambda: fetch(s), hit)
        if not _sound(value):
            raise RuntimeError("futures quote refresh failed: the last answer is not shared")
        await shared_cache.aput(SHARED_NS, key, {"value": value, "ttl": ttl}, max(ttl, SHARED_KEEP_SEC))
