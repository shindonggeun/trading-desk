"""Recent SEC filings of a US-listed company — material events (8-K) and insider trades (Form 4) (#148).

Keyless: sec.gov/files/company_tickers.json maps tickers to CIKs (refreshed daily) and
data.sec.gov/submissions/CIK##########.json lists a company's recent filings. SEC asks for a
User-Agent that names the requester (SEC_EDGAR_USER_AGENT) and at most ten requests a second; a run
makes one or two, cached for half an hour.

What reaches the agents is the filing type, date and 8-K item codes translated into words: what
happened, not the filings' text. Form 4 is counted, not read (buy or sell needs the XML of each one).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import UTC, date, datetime, timedelta

import httpx

from . import net
from .events_calendar import user_agent
from .ttl_cache import TTLCache

log = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
TIMEOUT_SEC = 10.0
TICKERS_TTL_SEC = 24 * 3600
SUBMISSIONS_TTL_SEC = 30 * 60
EIGHT_K_DAYS = 30
INSIDER_DAYS = 30
MAX_8K = 5
SUBS_MAX = 64

# 8-K items (sec.gov/fast-answers/answersform8khtm) worth a line in a trading note
ITEMS = {
    "1.01": "material agreement",
    "1.02": "agreement terminated",
    "1.03": "bankruptcy",
    "2.01": "acquisition or sale of assets completed",
    "2.02": "results of operations (earnings)",
    "2.03": "new debt obligation",
    "2.05": "restructuring / exit costs",
    "2.06": "impairment",
    "3.01": "delisting notice",
    "3.02": "unregistered equity sale",
    "4.01": "auditor change",
    "4.02": "prior financials unreliable",
    "5.02": "director / officer change",
    "5.07": "shareholder vote results",
    "7.01": "Regulation FD disclosure",
    "8.01": "other material event",
}


_US = re.compile(r"[A-Z]{1,6}(?:[.-][A-Z])?")


def is_us_ticker(ticker: str) -> bool:
    """Plain US symbols (and share classes: BRK.B, BRK-B) only: no exchange suffix (005930.KS),
    index (^GSPC) or future (CL=F)."""
    return bool(_US.fullmatch(ticker or ""))


class Filings:
    def __init__(self, client: httpx.AsyncClient | None = None, clock=time.monotonic) -> None:
        self._client = client
        self._clock = clock
        self._ciks: tuple[float, dict[str, int]] | None = None
        self._subs: TTLCache[int, dict] = TTLCache(SUBS_MAX, SUBMISSIONS_TTL_SEC, clock=lambda: self._clock())
        self._lock = asyncio.Lock()

    async def _get_json(self, url: str):
        client = self._client or net.async_client(timeout=TIMEOUT_SEC)
        try:
            response = await client.get(url, headers={"User-Agent": user_agent(), "Accept": "application/json"})
            response.raise_for_status()
            return response.json()
        finally:
            if self._client is None:
                await client.aclose()

    async def cik(self, ticker: str) -> int | None:
        async with self._lock:
            if self._ciks is None or self._clock() - self._ciks[0] >= TICKERS_TTL_SEC:
                rows = await self._get_json(TICKERS_URL)
                table = {str(r["ticker"]).upper(): int(r["cik_str"]) for r in rows.values()}
                self._ciks = (self._clock(), table)
        # BRK.B is listed as BRK-B
        return self._ciks[1].get(ticker.upper().replace(".", "-"))

    async def recent(self, ticker: str, today: date | None = None) -> dict | None:
        """{8k: [{date, items: [words]}], insider: {count, last}, company} or None for a non-US
        ticker or one SEC does not know."""
        if not is_us_ticker(ticker):
            return None
        cik = await self.cik(ticker)
        if cik is None:
            return None
        data = self._subs.get(cik)
        if data is None:
            data = await self._get_json(SUBMISSIONS_URL.format(cik=cik))   # a failure raises: nothing is kept
            self._subs.put(cik, data)
        return summarise(data, today or datetime.now(UTC).date())


def summarise(data: dict, today: date) -> dict:
    recent = (data.get("filings") or {}).get("recent") or {}
    forms, dates, items = recent.get("form") or [], recent.get("filingDate") or [], recent.get("items") or []
    eight_k, insider = [], []
    for i, form in enumerate(forms):
        try:
            filed = date.fromisoformat(dates[i])
        except (IndexError, ValueError):
            continue
        if form in ("8-K", "8-K/A") and filed >= today - timedelta(days=EIGHT_K_DAYS) and len(eight_k) < MAX_8K:
            codes = [c.strip() for c in (items[i] if i < len(items) else "").split(",") if c.strip()]
            words = [ITEMS[c] for c in codes if c in ITEMS]
            eight_k.append({"date": filed.isoformat(), "form": form, "items": words or ["filing"]})
        elif form in ("4", "4/A") and filed >= today - timedelta(days=INSIDER_DAYS):
            insider.append(filed)
    return {"company": " ".join(str(data.get("name") or "").split())[:120] or None, "8k": eight_k,
            "insider": {"count": len(insider), "last": max(insider).isoformat() if insider else None,
                        "days": INSIDER_DAYS}}


FILINGS = Filings()


def prompt_note(summary: dict | None) -> str | None:
    """The agents' "Recent SEC filings" block, or None when there is nothing to say."""
    if not summary or (not summary["8k"] and not summary["insider"]["count"]):
        return None
    lines = [("Recent SEC filings (EDGAR, official; the filing types and 8-K item codes, not their text). "
              "An earnings 8-K or officer change explains gaps; heavy Form 4 activity is a reason to check "
              "insider selling with get_insider_transactions before leaning long.")]
    for row in summary["8k"]:
        lines.append(f"- {row['date']} {row['form']}: {', '.join(row['items'])}")
    ins = summary["insider"]
    if ins["count"]:
        lines.append(f"- Form 4 (insider trades): {ins['count']} filing(s) in the last {ins['days']} days, latest {ins['last']}")
    return "\n".join(lines)


async def note_for(ticker: str, timeout: float = 8.0) -> str | None:
    """The note for a run; None on any failure (a run never waits long on SEC)."""
    try:
        return prompt_note(await asyncio.wait_for(FILINGS.recent(ticker), timeout))
    except Exception as exc:  # noqa: BLE001 - incl. timeout
        log.info("SEC filings for %s failed: %s", ticker, exc)
        return None
