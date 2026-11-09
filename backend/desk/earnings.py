"""Company earnings dates for the market calendar (#243). Two keyless sources:

- Nasdaq's earnings calendar (api.nasdaq.com/api/calendar/earnings?date=YYYY-MM-DD): every US report
  of a day with its timing (before the open / after the close), the consensus EPS and the market cap.
  One request per weekday; the calendar keeps the big names (``MAJOR_CAP``) and the watched ones.
- Yahoo (yfinance ``Ticker.calendar``) for Korean tickers: the next expected report date and the
  average EPS estimate. One request per ticker, only for the watched Korean ones.

A background pass (desk.api, with the economic calendar) refreshes this month and the next. A
failed day or ticker keeps what it had. Unofficial endpoints: when they change the calendar shows
no earnings, it does not fail.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger(__name__)

NASDAQ_URL = "https://api.nasdaq.com/api/calendar/earnings"
TIMEOUT_SEC = 20.0
GAP_SEC = 0.5                  # between Nasdaq day requests
MAX_FAILS_IN_ROW = 5           # a stalled Nasdaq ends the pass's day requests (each would wait the timeout)
KST = ZoneInfo("Asia/Seoul")
MAJOR_CAP = 100e9              # US reports shown without being watched: market cap at least this
DEFAULT_KR = ("005930.KS", "000660.KS", "373220.KS", "207940.KS", "005380.KS", "035420.KS", "035720.KS")
_TICKER = re.compile(r"^[A-Z0-9.\-^=]{1,15}$")
WHEN = {"time-pre-market": "pre", "time-after-hours": "after"}


def _money(text: str | None) -> float | None:
    try:
        return float(str(text).replace("$", "").replace(",", "").replace("(", "-").replace(")", ""))
    except (TypeError, ValueError):
        return None


def parse_nasdaq(payload: dict, day: date) -> list[dict]:
    rows = ((payload or {}).get("data") or {}).get("rows") or []
    out = []
    for r in rows:
        sym = str(r.get("symbol") or "").strip().upper()
        if not _TICKER.match(sym):
            continue
        out.append({"ticker": sym, "name": str(r.get("name") or "")[:80], "date": day.isoformat(), "market": "us",
                    "when": WHEN.get(r.get("time"), "unknown"), "eps_estimate": _money(r.get("epsForecast")),
                    "market_cap": _money(r.get("marketCap")), "source": "nasdaq"})
    return out


def weekdays(first: date, last: date) -> list[date]:
    out, d = [], first
    while d <= last:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _yahoo_next(ticker: str) -> dict | None:
    import yfinance as yf

    cal = yf.Ticker(ticker).calendar or {}
    dates = cal.get("Earnings Date") or []
    if not dates:
        return None
    d = dates[0]
    d = d.date() if isinstance(d, datetime) else d
    avg = cal.get("Earnings Average")
    return {"ticker": ticker, "name": None, "date": d.isoformat(), "market": "kr", "when": "unknown",
            "eps_estimate": float(avg) if isinstance(avg, (int, float)) else None, "market_cap": None,
            "source": "yahoo"}


class Earnings:
    def __init__(self, yahoo=_yahoo_next) -> None:
        self.us: dict[str, list[dict]] = {}       # ISO day -> rows
        self.kr: dict[str, dict] = {}             # ticker -> next report
        self.want_kr: set[str] = set(DEFAULT_KR)
        self.refreshed_at: str | None = None
        self.error: str | None = None
        self._yahoo = yahoo

    def want(self, tickers) -> None:
        """Korean tickers a page watches (favorites): fetched on the next pass."""
        new = {t for t in tickers if t.endswith((".KS", ".KQ"))} - self.want_kr
        if new:
            self.want_kr = self.want_kr | new          # a new set, never mutated under a reader

    async def refresh(self, client: httpx.AsyncClient | None = None, today: date | None = None,
                      gap: float = GAP_SEC) -> int:
        today = today or datetime.now(KST).date()        # the month as the page sees it
        first = today.replace(day=1)
        last = (first + timedelta(days=62)).replace(day=1) - timedelta(days=1)      # the end of next month
        # Built aside and swapped in at the end: the calendar handler reads these from the threadpool
        us, kr, want = dict(self.us), dict(self.kr), sorted(self.want_kr)
        ok, failures, in_row = 0, [], 0
        own = client is None
        client = client or httpx.AsyncClient(timeout=TIMEOUT_SEC, headers={
            "User-Agent": "Mozilla/5.0 (trading-desk)", "Accept": "application/json"})
        try:
            for d in weekdays(first, last):
                try:
                    resp = await client.get(NASDAQ_URL, params={"date": d.isoformat()})
                    resp.raise_for_status()
                    us[d.isoformat()] = parse_nasdaq(resp.json(), d)
                    ok += 1
                    in_row = 0
                except Exception as exc:  # noqa: BLE001 - one day down keeps its last rows
                    failures.append(f"nasdaq {d}: {type(exc).__name__}")
                    in_row += 1
                    if in_row >= MAX_FAILS_IN_ROW:
                        failures.append("nasdaq: stopped after repeated failures")
                        break
                if gap:
                    await asyncio.sleep(gap)
        finally:
            if own:
                await client.aclose()
        for t in want:
            try:
                row = await asyncio.to_thread(self._yahoo, t)
                if row:
                    kr[t] = row
                    ok += 1
            except Exception as exc:  # noqa: BLE001
                failures.append(f"yahoo {t}: {type(exc).__name__}")
        cutoff = (first - timedelta(days=40)).isoformat()
        self.us = {k: v for k, v in us.items() if k >= cutoff}
        self.kr = kr
        if ok:
            self.refreshed_at = datetime.now(UTC).isoformat(timespec="seconds")
        self.error = f"{len(failures)} failed (first: {failures[0]})" if failures else None
        return ok

    def for_days(self, first: date, last: date, watch: set[str]) -> dict[str, list[dict]]:
        """ISO day -> the reports to show: watched tickers and US names of ``MAJOR_CAP`` or more."""
        out: dict[str, list[dict]] = {}
        lo, hi = first.isoformat(), last.isoformat()
        us, kr = self.us, self.kr                      # whole dicts swapped by refresh: one consistent view
        for day, rows in us.items():
            if lo <= day <= hi:
                keep = [{**r, "watched": r["ticker"] in watch} for r in rows
                        if r["ticker"] in watch or (r["market_cap"] or 0) >= MAJOR_CAP]
                if keep:
                    out[day] = sorted(keep, key=lambda r: (not r["watched"], -(r["market_cap"] or 0)))
        for t, row in kr.items():
            if lo <= row["date"] <= hi and (t in watch or t in DEFAULT_KR):
                out.setdefault(row["date"], []).append({**row, "watched": t in watch})
        return out


CACHE = Earnings()
