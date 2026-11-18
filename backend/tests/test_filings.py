"""#628 (#148) recent SEC filings: US tickers only, the summary, the agents' note, the cache (no network)."""

import asyncio
import json
from datetime import date

import httpx
import pytest

from desk import filings as fl

TODAY = date(2026, 11, 18)
SUBS = {"name": "  Berkshire   Hathaway Inc ", "filings": {"recent": {
    "form": ["8-K", "4", "8-K", "10-Q", "8-K", "4", "8-K/A"],
    "filingDate": ["2026-11-10", "2026-11-12", "2026-09-01", "2026-11-01", "2026-11-03", "2026-10-01", "bad"],
    "items": ["2.02,9.01", "", "5.02", "", "1.01, 7.01, 99.9", "", "8.01"]}}}


@pytest.mark.parametrize(("ticker", "us"), [
    ("NVDA", True), ("BRK.B", True), ("BRK-B", True), ("005930.KS", False), ("^GSPC", False), ("CL=F", False),
    ("", False), ("nvda", False),
])
def test_us_tickers_only(ticker, us):
    assert fl.is_us_ticker(ticker) is us


def test_the_summary_and_the_note():
    s = fl.summarise(SUBS, TODAY)
    assert s["company"] == "Berkshire Hathaway Inc"
    assert s["8k"] == [{"date": "2026-11-10", "form": "8-K", "items": ["results of operations (earnings)"]},
                       {"date": "2026-11-03", "form": "8-K", "items": ["material agreement", "Regulation FD disclosure"]}]
    assert s["insider"] == {"count": 1, "last": "2026-11-12", "days": fl.INSIDER_DAYS}   # the October one is old
    note = fl.prompt_note(s)
    assert note.splitlines()[1] == "- 2026-11-10 8-K: results of operations (earnings)"
    assert "Form 4 (insider trades): 1 filing(s) in the last 30 days, latest 2026-11-12" in note
    assert fl.prompt_note(fl.summarise({"filings": {"recent": {}}}, TODAY)) is None and fl.prompt_note(None) is None


class Sec:
    def __init__(self):
        self.asked, self.fail = [], False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(request)
        if request.url.path.endswith("company_tickers.json"):
            return httpx.Response(200, json={"0": {"ticker": "BRK-B", "cik_str": 1067983}})
        if self.fail:
            return httpx.Response(503)
        return httpx.Response(200, content=json.dumps(SUBS).encode())


def test_recent_reads_sec_once_per_window_and_never_keeps_a_failure(monkeypatch):
    monkeypatch.setattr(fl, "user_agent", lambda: "desk-test")
    sec, now = Sec(), [0.0]
    client = httpx.AsyncClient(transport=httpx.MockTransport(sec))
    f = fl.Filings(client, clock=lambda: now[0])

    async def go(ticker):
        return await f.recent(ticker, TODAY)

    assert asyncio.run(go("005930.KS")) is None and sec.asked == []      # not a US ticker: no request
    assert asyncio.run(go("BRK.B"))["company"] == "Berkshire Hathaway Inc"   # BRK.B is listed as BRK-B
    assert asyncio.run(go("MSFT")) is None                                # SEC does not know it
    asyncio.run(go("BRK.B"))
    subs = [r for r in sec.asked if "submissions" in r.url.path]
    assert len(subs) == 1 and subs[0].headers["User-Agent"] == "desk-test"
    now[0] = fl.SUBMISSIONS_TTL_SEC + 1
    sec.fail = True
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(go("BRK.B"))
    sec.fail = False
    assert asyncio.run(go("BRK.B"))["insider"]["count"] == 1               # the failure was not kept
    assert len([r for r in sec.asked if "submissions" in r.url.path]) == 3
    asyncio.run(client.aclose())
