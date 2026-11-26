"""#916 Per-stock KRX daily foreign trading and foreign ownership from KIS, for the Korean flow board.

Three KIS quotation TRs per stock, on the desk's existing KIS client (``desk.futures_flow.KisRest``, the futures'
one: this module never issues a token - #688 leaves that to the collector). Market code ``J`` is KRX only (no NXT),
for KOSPI and KOSDAQ codes alike.

- ``FHPTJ04160001`` 종목별 투자자매매동향(일별) ``output2``: per session the stock's whole turnover and the foreign
  buy / sell values, each with its own volume. KIS does not state the money unit, so every amount is checked on
  its own (``won``): one scale must put its average price inside the day's range. An amount no scale explains is
  dropped (the board says no_data), never guessed.
- A session's row is final only when fetched at or after 20:10 KST that day (KRX publishes the figures with the
  after-hours trades from about 20:00); an earlier one is ``confirmed=False`` (the board says not_published).
- ``FHKST01010100`` 주식현재가 시세: foreign holding / listed shares is the ownership ratio. It equals KIS' HTS
  exhaustion rate only when the foreign limit is the whole listing (``no_limit``).
- ``FHKST01010400`` 주식현재가 일자별: the daily exhaustion rate - an ownership ratio only when ``no_limit``.
- Holding quantities KRX shows during session S are the confirmed ones at the previous session's close, so a value
  read on S is dated ``OWNERSHIP_LAG_SESSIONS`` sessions earlier.

Cached per (code, latest closed session) in memory and in the sealed shared tier (``kr_stock_flow``: public market
data, docs/security.md 5.2). A board request waits at most ``BUDGET_SEC``; fetches still running finish in the
background and serve the next request.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from . import market_calendar, shared_cache
from .futures_flow import _short as kis_reason
from .kr_flow_board import ForeignFlow, Ownership, Turnover
from .universe import KR_SUFFIX

# KIS [국내주식] 종목별 투자자매매동향(일별) · 주식현재가 시세 · 주식현재가 일자별
INVESTOR_PATH = "/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily"
INVESTOR_TR = "FHPTJ04160001"
PRICE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-price"
PRICE_TR = "FHKST01010100"
DAILY_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-price"
DAILY_TR = "FHKST01010400"
SOURCE = "kis"
SHARED_NS = "kr_stock_flow"
# Every KIS call of this module at once, under the app key's rate (kr_short_sales uses the same)
KIS_PARALLEL = 2
# Fetches queued at once (favorites + a Top 50 are about 100): past this a request serves what is cached
MAX_FLIGHTS = 120
# A cold Top 50 board is about 150 calls: paced to at most 10 a second so the app key's per-second limit
# (shared with the collector's KIS work) keeps headroom
MIN_GAP_SEC = 0.1
BUDGET_SEC = 2.0
# KRX's final figures with after-hours trading: 20:10 after a 15:30 close, later on a late-opening day
FINAL_DELAY = timedelta(hours=4, minutes=40)
# #1018 to be checked against KRX's own table in prod: the holdings shown during S are the previous close's
OWNERSHIP_LAG_SESSIONS = 1
FINAL_TTL = 6 * 3600
PENDING_TTL = 10 * 60
FAILURE_TTL = 60
CACHE_LIMIT = 256
# Won, thousand won, million won. Scales 1000 apart cannot both fit one day's range (that needs high > 800 x low).
SCALES = (1, 1_000, 1_000_000)
# After-hours single-price trades may sit up to 10% off the close, so outside the regular low / high
PRICE_SLACK = 0.10
# KIS' exhaustion rate has two decimals: a ratio within this of it means the limit is the whole listing
NO_LIMIT_TOLERANCE = 0.02
_CODE = re.compile(r"[0-9][0-9A-Z]{5}")
_UINT = re.compile(r"[0-9]{1,18}")
_MONEY = (("turnover", "acml_tr_pbmn", "acml_vol"),
          ("buy", "frgn_shnu_tr_pbmn", "frgn_shnu_vol"),
          ("sell", "frgn_seln_tr_pbmn", "frgn_seln_vol"))
_SERIES = ("trades", "holding", "exhaustion")

log = logging.getLogger(__name__)

Rows = tuple[list[Turnover] | None, list[ForeignFlow] | None, list[Ownership] | None]


def _uint(value) -> int | None:
    text = str(value if value is not None else "").strip()
    return int(text) if _UINT.fullmatch(text) else None


def _pct(value) -> float | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and 0 <= number <= 100 else None


def _kis_day(value) -> date | None:
    try:
        return datetime.strptime(str(value or ""), "%Y%m%d").date()
    except ValueError:
        return None


def won(amount: int | None, volume: int | None, low: int | None, high: int | None) -> int | None:
    """``amount`` in won, or None when not exactly one scale puts ``amount / volume`` in the day's widened range.

    Zero value on zero volume is a real 0; a value without volume (or volume without value) is not.
    """
    if amount is None or volume is None:
        return None
    if amount == 0 or volume == 0:
        return 0 if amount == volume == 0 else None
    if not low or not high or low > high:
        return None
    floor, ceiling = low * (1 - PRICE_SLACK), high * (1 + PRICE_SLACK)
    # The published amount is rounded to its unit: any value within half a unit of it may be the true one
    fits = [scale for scale in SCALES
            if (amount - 0.5) * scale / volume <= ceiling and (amount + 0.5) * scale / volume >= floor]
    return amount * fits[0] if len(fits) == 1 else None


def started_session(at: datetime) -> date:
    """The latest KRX session that has opened at ``at``: today from its open, else the previous trading day."""
    today = market_calendar.local_today("kr", at)
    if market_calendar.is_open("kr", today) and at >= market_calendar.session_bounds("kr", today)[0]:
        return today
    return market_calendar.previous_open("kr", today)


def ownership_as_of(session: date) -> date:
    """The close whose confirmed holdings KRX shows during ``session``."""
    day = session
    for _ in range(OWNERSHIP_LAG_SESSIONS):
        day = market_calendar.previous_open("kr", day)
    return day


def _next_open(at: datetime) -> datetime:
    today = market_calendar.local_today("kr", at)
    if market_calendar.is_open("kr", today) and at < market_calendar.session_bounds("kr", today)[0]:
        return market_calendar.session_bounds("kr", today)[0]
    return market_calendar.session_bounds("kr", market_calendar.next_open("kr", today))[0]


def _final(day: date, fetched_at: datetime) -> bool:
    return market_calendar.is_open("kr", day) and fetched_at >= market_calendar.session_close("kr", day) + FINAL_DELAY


def parse_trades(body: dict, code: str, session: date) -> list[dict]:
    """``output2`` -> one dict per trading day up to ``session``: turnover / buy / sell in won, or None each."""
    rows = body.get("output2")
    if not isinstance(rows, list):
        raise TypeError("KIS investor trend: no output2 list")
    trades: dict[date, dict] = {}
    dropped: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = _kis_day(row.get("stck_bsop_date"))
        if day is None or day > session or day in trades or not market_calendar.is_open("kr", day):
            continue
        low, high = _uint(row.get("stck_lwpr")), _uint(row.get("stck_hgpr"))
        values = {}
        for name, amount_field, volume_field in _MONEY:
            values[name] = won(_uint(row.get(amount_field)), _uint(row.get(volume_field)), low, high)
            if values[name] is None:
                dropped.append(f"{day.isoformat()} {amount_field}")
        trades[day] = {"date": day.isoformat(), **values}
    if dropped:
        log.warning("KIS stock flow %s: %d values without a fitting unit, dropped (%s)",
                    code, len(dropped), ", ".join(dropped[:3]))
    return list(trades.values())


def parse_holding(body: dict, at: datetime) -> dict:
    """``output`` -> the ownership ratio read at ``at`` (None unless 0 <= holding <= listed > 0) and ``no_limit``."""
    out = body.get("output")
    if not isinstance(out, dict):
        raise TypeError("KIS price: no output")
    held, listed = _uint(out.get("frgn_hldn_qty")), _uint(out.get("lstn_stcn"))
    ratio = held / listed * 100 if held is not None and listed and held <= listed else None
    quoted = _pct(out.get("hts_frgn_ehrt"))
    no_limit = ratio is not None and quoted is not None and abs(ratio - quoted) <= NO_LIMIT_TOLERANCE
    return {"session": started_session(at).isoformat(), "ratio_pct": ratio, "no_limit": no_limit}


def parse_exhaustion(body: dict) -> list[dict]:
    """``output`` -> the exhaustion rate per trading session (None when unreadable)."""
    rows = body.get("output")
    if not isinstance(rows, list):
        raise TypeError("KIS daily price: no output list")
    seen: dict[date, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = _kis_day(row.get("stck_bsop_date"))
        if day is None or day in seen or not market_calendar.is_open("kr", day):
            continue
        seen[day] = {"date": day.isoformat(), "pct": _pct(row.get("hts_frgn_ehrt"))}
    return list(seen.values())


def _rows_of(data: dict, market: str) -> Rows:
    ticker = data["code"] + KR_SUFFIX[market.upper()]
    turnover = flows = owned = None
    if data["trades"] is not None:
        fetched_at = datetime.fromisoformat(data["fetched_at"])
        turnover, flows = [], []
        for row in data["trades"]:
            day = date.fromisoformat(row["date"])
            final = _final(day, fetched_at)
            turnover.append(Turnover(ticker, market, day, SOURCE, row.get("turnover"), venue="krx", unit="KRW",
                                     confirmed=final, trade_scope="all_sessions"))
            flows.append(ForeignFlow(ticker, market, day, SOURCE, row.get("buy"), row.get("sell"), venue="krx",
                                     unit="KRW", confirmed=final, trade_scope="all_sessions"))
    holding, snapshot = data["holding"], None
    if holding is not None:
        owned = []
        if holding.get("ratio_pct") is not None:
            snapshot = ownership_as_of(date.fromisoformat(holding["session"]))
            owned.append(Ownership(ticker, market, snapshot, SOURCE, holding["ratio_pct"],
                                   denominator="listed_shares"))
    if data["exhaustion"] is not None:
        owned = [] if owned is None else owned
        # The exhaustion rate is the ownership ratio only when the limit is the listing (checked on the snapshot)
        basis = "listed_shares" if snapshot is not None and holding.get("no_limit") is True else None
        for row in data["exhaustion"]:
            as_of = ownership_as_of(date.fromisoformat(row["date"]))
            if as_of != snapshot:
                owned.append(Ownership(ticker, market, as_of, SOURCE, row.get("pct"), denominator=basis))
    return turnover, flows, owned


def board_rows(datas: Sequence[dict], market: str) -> Rows:
    """Board rows of every fetched stock; a series no stock answered is None (source unavailable)."""
    series: list[list | None] = [None, None, None]
    for data in datas:
        for i, part in enumerate(_rows_of(data, market)):
            if part is not None:
                series[i] = (series[i] or []) + part
    return series[0], series[1], series[2]


def _iso_day(value) -> bool:
    try:
        return isinstance(value, str) and date.fromisoformat(value) is not None
    except ValueError:
        return False


def _amount_ok(value) -> bool:
    return value is None or (type(value) is int and value >= 0)


def _pct_ok(value) -> bool:
    return value is None or (type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 100)


def _checked(value, code: str, session: date) -> dict | None:
    """A shared value back in this module's shape, or None (a miss): what the shared tier gives is re-checked."""
    if not isinstance(value, dict) or value.get("code") != code or value.get("session") != session.isoformat():
        return None
    try:
        fetched_at = datetime.fromisoformat(value["fetched_at"])
    except (KeyError, TypeError, ValueError):
        return None
    trades, holding, exhaustion = (value.get(name) for name in _SERIES)
    ok = (
        fetched_at.utcoffset() is not None
        and isinstance(trades, list) and all(
            isinstance(row, dict) and _iso_day(row.get("date"))
            and all(_amount_ok(row.get(name)) for name, _, _ in _MONEY) for row in trades)
        and isinstance(holding, dict) and _iso_day(holding.get("session"))
        and _pct_ok(holding.get("ratio_pct")) and type(holding.get("no_limit")) is bool
        and isinstance(exhaustion, list) and all(
            isinstance(row, dict) and _iso_day(row.get("date")) and _pct_ok(row.get("pct")) for row in exhaustion)
    )
    return value if ok else None


@dataclass(frozen=True)
class _Entry:
    expires_at: float
    data: dict


class KisStockFlow:
    """KIS per-stock series for the board; without a KIS client (no keys, fake runs) every answer is None."""

    def __init__(self, rest, *, clock: Callable[[], float] = time.monotonic,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC), min_gap_sec: float = MIN_GAP_SEC):
        self._rest = rest
        self._min_gap = min_gap_sec
        self._pace = asyncio.Lock()
        self._last_call = -min_gap_sec   # time.monotonic() of the last KIS call's start
        self._clock = clock
        self._now = now          # wall time of each KIS call: finality and the holding's session are judged on it
        self._gate = asyncio.Semaphore(KIS_PARALLEL)
        self._cache: OrderedDict[tuple[str, str], _Entry] = OrderedDict()
        self._flights: dict[tuple[str, str], asyncio.Task] = {}
        self._background: set[asyncio.Task] = set()

    async def collect(self, codes: Sequence[str], market: str, latest_session: date, *,
                      budget_sec: float = BUDGET_SEC) -> Rows | None:
        """Rows for ``codes`` (in priority order) that are cached or fetched within ``budget_sec``."""
        if self._rest is None:
            return None
        ordered = [code for code in dict.fromkeys(codes) if _CODE.fullmatch(code)]
        if not ordered:
            return ([], [], [])
        session = latest_session.isoformat()
        previous = market_calendar.previous_open("kr", latest_session).isoformat()
        found: dict[str, dict] = {}
        waiting: dict[asyncio.Task, str] = {}
        pending = False
        for code in ordered:
            key = (code, session)
            entry = self._cache.get(key)
            if entry is not None:
                self._cache.move_to_end(key)
                found[code] = entry.data
                if entry.expires_at <= self._clock():
                    # Served at once; its refresh runs in the background for the next request
                    self._flight(code, latest_session)
                continue
            task = self._flight(code, latest_session)
            if task is None:
                pending = True
            else:
                waiting[task] = code
        if waiting:
            # asyncio.wait cancels nothing: a fetch past the budget (or a cancelled request) keeps going for the cache
            done, _ = await asyncio.wait(waiting, timeout=budget_sec)
            for task, code in waiting.items():
                if task in done and not task.cancelled() and task.exception() is None:
                    found[code] = task.result()
                else:
                    pending = pending or task not in done
        for code in ordered:
            if code not in found and (last := self._cache.get((code, previous))) is not None:
                found[code] = last.data   # the previous session's history until this session's answer lands
        rows = board_rows([found[code] for code in ordered if code in found], market)
        if pending:
            # Still being fetched is no data yet, not a missing source
            rows = tuple([] if part is None else part for part in rows)
        return rows

    def _flight(self, code: str, session: date) -> asyncio.Task | None:
        """One fetch per (code, session) however many requests want it at once; None past ``MAX_FLIGHTS``."""
        key = (code, session.isoformat())
        task = self._flights.get(key)
        if task is None:
            if len(self._flights) >= MAX_FLIGHTS:
                return None
            task = asyncio.create_task(self._load(code, session))
            self._flights[key] = task

            def landed(done: asyncio.Task, key=key) -> None:
                if self._flights.get(key) is done:
                    del self._flights[key]
                if not done.cancelled() and (exc := done.exception()) is not None:
                    log.error("KIS stock flow %s failed", key[0], exc_info=exc)

            task.add_done_callback(landed)
        return task

    async def _load(self, code: str, session: date) -> dict:
        key = (code, session.isoformat())
        hit = await shared_cache.aget(SHARED_NS, key)
        data = _checked(hit[0], code, session) if hit else None
        if data is not None:
            self._store(key, data, min(hit[2], self._ttl(data, failed=False)))
            return data
        data, failed = await self._fetch(code, session)
        old = self._cache.get(key)
        if failed and old is not None:
            data = self._merged(data, old.data)
        ttl = self._ttl(data, failed)
        self._store(key, data, ttl)
        if not failed:
            share = asyncio.create_task(shared_cache.aput(SHARED_NS, key, data, ttl))
            self._background.add(share)
            share.add_done_callback(self._background.discard)
        return data

    @staticmethod
    def _merged(data: dict, old: dict) -> dict:
        """Keep the same session's last good series for any call that failed now."""
        merged = dict(data)
        if data["trades"] is None and old["trades"] is not None:
            # A row's finality goes with the time it was fetched
            merged["trades"], merged["fetched_at"] = old["trades"], old["fetched_at"]
        for name in ("holding", "exhaustion"):
            if data[name] is None:
                merged[name] = old[name]
        return merged

    async def _call(self, path: str, tr_id: str, params: dict) -> tuple[dict, datetime]:
        async with self._gate:
            async with self._pace:
                # Spaced from the previous call's actual start: an overslept wait cannot release a burst
                wait = self._last_call + self._min_gap - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                self._last_call = time.monotonic()
            at = self._now()
            return await self._rest.get(path, tr_id, params), at

    async def _fetch(self, code: str, session: date) -> tuple[dict, bool]:
        """The three calls; one failing leaves only its own series out (None)."""
        base = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
        investor, price, daily = await asyncio.gather(
            self._call(INVESTOR_PATH, INVESTOR_TR, {**base, "FID_INPUT_DATE_1": session.strftime("%Y%m%d"),
                                                    "FID_ORG_ADJ_PRC": "", "FID_ETC_CLS_CODE": ""}),
            self._call(PRICE_PATH, PRICE_TR, dict(base)),
            self._call(DAILY_PATH, DAILY_TR, {**base, "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "1"}),
            return_exceptions=True)
        fetched_at = self._now() if isinstance(investor, BaseException) else investor[1]
        data = {
            "code": code, "session": session.isoformat(), "fetched_at": fetched_at.isoformat(),
            "trades": self._parsed(code, INVESTOR_TR, investor, lambda body, _at: parse_trades(body, code, session)),
            "holding": self._parsed(code, PRICE_TR, price, parse_holding),
            "exhaustion": self._parsed(code, DAILY_TR, daily, lambda body, _at: parse_exhaustion(body)),
        }
        return data, any(data[name] is None for name in _SERIES)

    @staticmethod
    def _parsed(code: str, tr_id: str, result, parse: Callable[[dict, datetime], object]):
        if isinstance(result, BaseException):
            # KisError text carries codes only; anything else (an httpx error may quote a header) by type
            log.warning("KIS stock flow %s %s failed: %s", code, tr_id, kis_reason(result))
            return None
        body, at = result
        try:
            return parse(body, at)
        except (AttributeError, TypeError, ValueError) as exc:
            log.warning("KIS stock flow %s %s: unreadable answer (%s)", code, tr_id, type(exc).__name__)
            return None

    def _ttl(self, data: dict, failed: bool) -> float:
        if failed:
            return FAILURE_TTL
        fetched_at = datetime.fromisoformat(data["fetched_at"])
        latest = date.fromisoformat(data["session"])
        final = _final(latest, fetched_at) and any(row["date"] == data["session"] for row in data["trades"])
        # The holding snapshot moves on to the next session's date when that session opens
        left = (_next_open(fetched_at) - self._now()).total_seconds()
        return max(1.0, min(FINAL_TTL if final else PENDING_TTL, left))

    def _store(self, key: tuple[str, str], data: dict, ttl: float) -> None:
        self._cache[key] = _Entry(self._clock() + ttl, data)
        self._cache.move_to_end(key)
        while len(self._cache) > CACHE_LIMIT:
            self._cache.popitem(last=False)

    async def aclose(self) -> None:
        """Stop fetches and shared writes still running (shutdown, before the KIS client and Redis close)."""
        tasks = [*self._flights.values(), *self._background]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
