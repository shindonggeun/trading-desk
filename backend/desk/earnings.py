"""Company earnings dates for the market calendar (#243, #308). Two keyless sources:

- Nasdaq's earnings calendar (api.nasdaq.com/api/calendar/earnings?date=YYYY-MM-DD): every US report
  of a day with its timing (before the open / after the close), the consensus EPS, last year's EPS,
  the analyst count, the fiscal quarter and the market cap. One request per weekday; the calendar
  keeps the big names (``MAJOR_CAP``) and the watched ones.
- Yahoo (yfinance ``Ticker.calendar`` / ``earnings_estimate``): the next expected report date with
  the EPS and revenue consensus and their range. Korean tickers always come from here; US tickers
  for the revenue consensus Nasdaq lacks, and - when Nasdaq fails - for their dates too
  (``MAJOR_US`` and the watched ones).

#308: a Nasdaq failure no longer empties the month.
- Each failure keeps its HTTP status (``nasdaq 2026-11-02: 403``) and the log shows the answer's
  first line. 429, 5xx and timeouts are retried twice after a short wait; a 403 stops Nasdaq for
  the pass (no browser disguise to get round a refusal).
- A failed pass is retried sooner than the usual six hours (``RETRY_STEPS_SEC``).
- What was fetched survives a restart: a JSON file in the server volume (``CACHE_FILE``, no schema
  change). A day that failed keeps its previous rows, marked ``stale`` with their ``refreshed_at``.
- A Korean row is named by the broker's listing (``name_of``, desk.universe).

A background pass (desk.api, with the economic calendar) refreshes this month and the next.
Unofficial endpoints: when they change the calendar shows fewer earnings, it does not fail.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger(__name__)

NASDAQ_URL = "https://api.nasdaq.com/api/calendar/earnings"
TIMEOUT_SEC = 20.0
GAP_SEC = 0.5                  # between Nasdaq day requests
NASDAQ_RETRIES = 2             # a 429, 5xx or timeout is tried this many more times
RETRY_WAIT_SEC = (2.0, 5.0)    # before each retry
MAX_FAILS_IN_ROW = 5           # days that failed after their retries: the pass stops asking Nasdaq
RETRY_STEPS_SEC = (1800.0, 3600.0, 7200.0, 14400.0)   # the next pass after 1, 2, 3, 4+ failed passes
KST = ZoneInfo("Asia/Seoul")
MAJOR_CAP = 100e9              # US reports shown without being watched: market cap at least this
DEFAULT_KR = ("005930.KS", "000660.KS", "373220.KS", "207940.KS", "005380.KS", "035420.KS", "035720.KS")
# The largest US names: their dates come from Yahoo when Nasdaq fails, and their revenue consensus
# always does (Yahoo symbols).
MAJOR_US = ("AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "TSLA", "BRK-B", "JPM", "LLY", "V", "WMT",
            "ORCL", "MA", "NFLX", "XOM", "COST", "JNJ", "HD", "PG", "ABBV", "BAC", "AMD", "KO", "CRM", "UNH",
            "CVX", "MU", "PLTR")
DETAIL_TTL_SEC = 24 * 3600     # a US ticker's Yahoo consensus is fetched again after a day
DETAIL_MAX = 40                # Yahoo consensus lookups per pass
CACHE_FILE = Path.home() / ".tradingagents" / "desk-cache" / "earnings.json"   # the backend's volume
_TICKER = re.compile(r"^[A-Z0-9.\-^=]{1,15}$")
WHEN = {"time-pre-market": "pre", "time-after-hours": "after"}
DETAIL = ("eps_estimate", "eps_low", "eps_high", "eps_year_ago", "revenue_estimate", "revenue_low", "revenue_high",
          "analysts", "fiscal_quarter", "currency")


def _money(text: str | None) -> float | None:
    try:
        return float(str(text).replace("$", "").replace(",", "").replace("(", "-").replace(")", ""))
    except (TypeError, ValueError):
        return None


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _int(v) -> int | None:
    f = _num(v)
    return int(f) if f is not None else None


def blank(**fields) -> dict:
    """A row with every detail field present (None when unknown)."""
    return {"name": None, "when": "unknown", "market_cap": None, **dict.fromkeys(DETAIL), **fields}


def parse_nasdaq(payload: dict, day: date) -> list[dict]:
    rows = ((payload or {}).get("data") or {}).get("rows") or []
    out = []
    for r in rows:
        sym = str(r.get("symbol") or "").strip().upper()
        if not _TICKER.match(sym):
            continue
        out.append(blank(ticker=sym, name=str(r.get("name") or "")[:80] or None, date=day.isoformat(), market="us",
                         when=WHEN.get(r.get("time"), "unknown"), eps_estimate=_money(r.get("epsForecast")),
                         eps_year_ago=_money(r.get("lastYearEPS")), analysts=_int(r.get("noOfEsts")),
                         fiscal_quarter=str(r.get("fiscalQuarterEnding") or "").strip() or None,
                         currency="USD", market_cap=_money(r.get("marketCap")), source="nasdaq"))
    return out


def weekdays(first: date, last: date) -> list[date]:
    out, d = [], first
    while d <= last:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _yahoo_next(ticker: str) -> dict | None:
    """The next report of ``ticker`` with its consensus (yfinance), or None."""
    import yfinance as yf

    t = yf.Ticker(ticker)
    cal = t.calendar or {}
    dates = cal.get("Earnings Date") or []
    if not dates:
        return None
    d = dates[0]
    d = d.date() if isinstance(d, datetime) else d
    korean = ticker.endswith((".KS", ".KQ"))
    row = blank(ticker=ticker, date=d.isoformat(), market="kr" if korean else "us", source="yahoo",
                eps_estimate=_num(cal.get("Earnings Average")), eps_low=_num(cal.get("Earnings Low")),
                eps_high=_num(cal.get("Earnings High")), revenue_estimate=_num(cal.get("Revenue Average")),
                revenue_low=_num(cal.get("Revenue Low")), revenue_high=_num(cal.get("Revenue High")),
                currency="KRW" if korean else "USD")
    try:
        frame = t.earnings_estimate
        q = (frame.to_dict("index") if frame is not None and not frame.empty else {}).get("0q") or {}
        row["analysts"], row["eps_year_ago"] = _int(q.get("numberOfAnalysts")), _num(q.get("yearAgoEps"))
    except Exception as exc:  # noqa: BLE001 - the date and consensus stand without the analyst count
        log.info("yahoo earnings estimate %s: %s", ticker, exc)
    return row


class DayFailed(Exception):
    """One Nasdaq day failed after its retries; ``code`` is the status (or timeout / error name)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class Refused(DayFailed):
    """Nasdaq answered 403: not retried, the pass stops asking."""


def _first_line(resp: httpx.Response) -> str:
    text = (resp.text or "").strip()
    return text.splitlines()[0][:160] if text else ""


async def nasdaq_day(client: httpx.AsyncClient, day: date, waits=RETRY_WAIT_SEC) -> list[dict]:
    """One weekday of Nasdaq reports: 429 / 5xx / timeouts retried NASDAQ_RETRIES times, 403 refused."""
    last = "error"
    for attempt in range(NASDAQ_RETRIES + 1):
        if attempt and waits:
            await asyncio.sleep(waits[min(attempt - 1, len(waits) - 1)])
        try:
            resp = await client.get(NASDAQ_URL, params={"date": day.isoformat()})
        except httpx.TimeoutException:
            last = "timeout"
            continue
        except httpx.TransportError as exc:
            last = type(exc).__name__
            continue
        if resp.status_code == 403:
            log.warning("nasdaq %s: 403 %s", day, _first_line(resp))
            raise Refused("403")
        if resp.status_code == 429 or resp.status_code >= 500:
            last = str(resp.status_code)
            log.info("nasdaq %s: %s %s (attempt %d)", day, resp.status_code, _first_line(resp), attempt + 1)
            continue
        if resp.status_code >= 400:
            log.warning("nasdaq %s: %s %s", day, resp.status_code, _first_line(resp))
            raise DayFailed(str(resp.status_code))
        try:
            return parse_nasdaq(resp.json(), day)
        except ValueError as exc:      # not JSON (a block page)
            log.warning("nasdaq %s: unreadable answer %s", day, _first_line(resp))
            raise DayFailed("unreadable") from exc
    raise DayFailed(last)


def _stamp(iso: str | None) -> str:
    """'11/11 09:00' in Korean time, for the page's message."""
    if not iso:
        return "없음"
    return datetime.fromisoformat(iso).astimezone(KST).strftime("%m/%d %H:%M")


class Earnings:
    def __init__(self, yahoo=_yahoo_next, name_of: Callable[[str], str | None] | None = None,
                 cache_file: Path | None = None) -> None:
        self.us: dict[str, list[dict]] = {}       # ISO day -> Nasdaq rows
        self.us_at: dict[str, str] = {}           # ISO day -> when its rows were fetched
        self.kr: dict[str, dict] = {}             # ticker -> next report (Yahoo)
        self.fallback: dict[str, dict] = {}       # US ticker -> next report from Yahoo (Nasdaq failed)
        self.details: dict[str, dict] = {}        # US ticker -> Yahoo consensus (+ "fetched_at")
        self.want_kr: set[str] = set(DEFAULT_KR)
        self.want_us: set[str] = set()
        self.refreshed_at: str | None = None      # the last pass that fetched anything
        self.pass_at: str | None = None           # the last pass, fetched or not
        self.error: str | None = None             # the last pass's failures (log form)
        self.failed_sources: list[str] = []
        self.refused: bool = False                # Nasdaq answered 403 on the last pass
        self.failed_passes = 0
        self.name_of = name_of
        self.cache_file = cache_file
        self._yahoo = yahoo

    def want(self, tickers) -> None:
        """Tickers a page watches (favorites): Korean ones fetched from Yahoo on the next pass, US
        ones covered by Yahoo when Nasdaq fails and given their revenue consensus."""
        kr = {t for t in tickers if t.endswith((".KS", ".KQ"))} - self.want_kr
        us = {t for t in tickers if _TICKER.match(t) and not t.endswith((".KS", ".KQ")) and "^" not in t
              and "=" not in t and not t.endswith("-USD")} - self.want_us
        if kr:
            self.want_kr = self.want_kr | kr          # a new set, never mutated under a reader
        if us:
            self.want_us = self.want_us | us

    # --- persistence -------------------------------------------------------------------------

    def _path(self) -> Path:
        return self.cache_file or CACHE_FILE

    def save(self) -> None:
        path = self._path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"us": self.us, "us_at": self.us_at, "kr": self.kr, "fallback": self.fallback,
                                       "details": self.details, "refreshed_at": self.refreshed_at}), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            log.warning("earnings cache not saved (%s): %s", path, exc)

    def load(self) -> bool:
        """The last saved rows (after a restart); a missing or broken file leaves the cache empty."""
        path = self._path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return False
        except (OSError, ValueError) as exc:
            log.warning("earnings cache unreadable (%s): %s", path, exc)
            return False
        self.us, self.us_at = data.get("us") or {}, data.get("us_at") or {}
        self.kr, self.fallback = data.get("kr") or {}, data.get("fallback") or {}
        self.details, self.refreshed_at = data.get("details") or {}, data.get("refreshed_at")
        return True

    # --- the pass ----------------------------------------------------------------------------

    def next_delay(self, usual: float) -> float:
        """Seconds to the next pass: the usual six hours, sooner after failed passes."""
        if not self.failed_passes:
            return usual
        return min(usual, RETRY_STEPS_SEC[min(self.failed_passes, len(RETRY_STEPS_SEC)) - 1])

    async def refresh(self, client: httpx.AsyncClient | None = None, today: date | None = None,
                      gap: float = GAP_SEC, waits=RETRY_WAIT_SEC) -> int:
        today = today or datetime.now(KST).date()        # the month as the page sees it
        first = today.replace(day=1)
        last = (first + timedelta(days=62)).replace(day=1) - timedelta(days=1)      # the end of next month
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        # Built aside and swapped in at the end: the calendar handler reads these from the threadpool
        us, us_at, kr = dict(self.us), dict(self.us_at), dict(self.kr)
        fallback, details = dict(self.fallback), dict(self.details)
        ok, failures, in_row, refused, nasdaq_failed = 0, [], 0, False, False
        own = client is None
        client = client or httpx.AsyncClient(timeout=TIMEOUT_SEC, headers={
            "User-Agent": "trading-desk (personal calendar)", "Accept": "application/json"})
        try:
            for d in weekdays(first, last):
                try:
                    us[d.isoformat()] = await nasdaq_day(client, d, waits)
                    us_at[d.isoformat()] = stamp
                    ok += 1
                    in_row = 0
                except Refused:
                    failures.append(f"nasdaq {d}: 403")
                    refused = nasdaq_failed = True
                    break
                except DayFailed as exc:   # one day down keeps its last rows
                    failures.append(f"nasdaq {d}: {exc.code}")
                    nasdaq_failed = True
                    in_row += 1
                    if in_row >= MAX_FAILS_IN_ROW:
                        failures.append("nasdaq: stopped after repeated failures")
                        break
                if gap:
                    await asyncio.sleep(gap)
        finally:
            if own:
                await client.aclose()

        async def yahoo(t: str) -> dict | None:
            try:
                return await asyncio.to_thread(self._yahoo, t)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"yahoo {t}: {type(exc).__name__}")
                return None

        for t in sorted(self.want_kr):
            if row := await yahoo(t):
                kr[t] = row
                ok += 1
        # US: their dates from Yahoo when Nasdaq failed; their revenue consensus in any case
        asked = 0
        now = datetime.now(UTC)
        for t in sorted(set(MAJOR_US) | self.want_us):
            fresh = (details.get(t) or {}).get("fetched_at")
            if not nasdaq_failed and fresh and (now - datetime.fromisoformat(fresh)).total_seconds() < DETAIL_TTL_SEC:
                continue
            if asked >= DETAIL_MAX:
                break
            asked += 1
            if row := await yahoo(t):
                details[t] = {**{k: row.get(k) for k in DETAIL}, "fetched_at": stamp}
                if nasdaq_failed:
                    fallback[t] = row
                ok += 1
        if not nasdaq_failed:
            fallback = {}          # Nasdaq answered every day: its rows are the US calendar again

        cutoff = (first - timedelta(days=40)).isoformat()
        self.us = {k: v for k, v in us.items() if k >= cutoff}
        self.us_at = {k: v for k, v in us_at.items() if k >= cutoff}
        self.kr, self.fallback, self.details = kr, fallback, details
        self.pass_at = stamp
        if ok:
            self.refreshed_at = stamp
        self.refused = refused
        self.error = f"{len(failures)} failed (first: {failures[0]})" if failures else None
        if failures:
            log.warning("earnings pass: %s", "; ".join(failures[:8]))
        self.failed_sources = sorted({f.split(" ", 1)[0].rstrip(":") for f in failures})
        self.failed_passes = self.failed_passes + 1 if nasdaq_failed else 0
        self.save()
        return ok

    # --- the view ----------------------------------------------------------------------------

    @property
    def stale(self) -> bool:
        """Some shown rows are older than the last pass (a source failed)."""
        return bool(self.failed_sources)

    def message(self) -> str | None:
        """What the page says when a source failed (Korean)."""
        if not self.failed_sources:
            return None
        parts = []
        if "nasdaq" in self.failed_sources:
            first = (self.error or "").split("(first: ", 1)[-1]
            code = first.split(": ", 1)[-1].rstrip(")") if first.startswith("nasdaq") else ""
            parts.append("Nasdaq 응답 거부(403)" if self.refused else f"Nasdaq 일부 날짜 실패({code})" if code
                         else "Nasdaq 일부 날짜 실패")
        if "yahoo" in self.failed_sources:
            parts.append("Yahoo 일부 종목 실패")
        return f"{' · '.join(parts)} — 마지막 자료({_stamp(self.refreshed_at)}) 표시 중"

    def _name(self, row: dict) -> str | None:
        if row.get("name"):
            return row["name"]
        if self.name_of is not None:
            try:
                return self.name_of(row["ticker"])
            except Exception:  # noqa: BLE001 - a name lookup never hides a report
                return None
        return None

    def _with_detail(self, row: dict) -> dict:
        extra = self.details.get(row["ticker"]) or {}
        out = blank(**row)
        for k in DETAIL:
            if out.get(k) is None and extra.get(k) is not None:
                out[k] = extra[k]
        out["name"] = self._name(out)
        return out

    def for_days(self, first: date, last: date, watch: set[str]) -> dict[str, list[dict]]:
        """ISO day -> the reports to show: watched tickers and US names of ``MAJOR_CAP`` or more.
        A Nasdaq day older than the last pass is ``stale``; Yahoo's dates fill in for a US name
        Nasdaq did not list (it failed)."""
        out: dict[str, list[dict]] = {}
        lo, hi = first.isoformat(), last.isoformat()
        us, us_at, kr, fallback = self.us, self.us_at, self.kr, self.fallback   # one consistent view
        listed: set[str] = set()
        for day, rows in us.items():
            if lo <= day <= hi:
                at = us_at.get(day)
                stale = at is None or (self.pass_at is not None and at < self.pass_at)
                keep = [{**self._with_detail(r), "watched": r["ticker"] in watch, "refreshed_at": at, "stale": stale}
                        for r in rows if r["ticker"] in watch or (r.get("market_cap") or 0) >= MAJOR_CAP]
                listed.update(r["ticker"] for r in keep)
                if keep:
                    out[day] = keep
        for t, row in fallback.items():
            if lo <= row["date"] <= hi and t not in listed and (t in watch or t in MAJOR_US):
                out.setdefault(row["date"], []).append(
                    {**self._with_detail(row), "watched": t in watch, "refreshed_at": self.refreshed_at, "stale": False})
        for rows in out.values():
            rows.sort(key=lambda r: (not r["watched"], -(r.get("market_cap") or 0)))
        for t, row in kr.items():
            if lo <= row["date"] <= hi and (t in watch or t in DEFAULT_KR):
                out.setdefault(row["date"], []).append(
                    {**self._with_detail(row), "watched": t in watch, "refreshed_at": self.refreshed_at, "stale": False})
        return out

    def source_status(self) -> dict:
        return {"refreshed_at": self.refreshed_at, "error": self.message(), "stale": self.stale,
                "failed_sources": list(self.failed_sources)}


CACHE = Earnings()
