import asyncio
import time
from datetime import UTC, date, datetime, timedelta

import httpx

from desk import events_calendar as ec
from desk import filings as fl
from desk import macro_news as m

FOMC_HTML = """
<h4><a>2026 FOMC Meetings</a></h4>
<div class="row fomc-meeting"><div class="fomc-meeting__month col-xs-5"><strong>January</strong></div>
  <div class="fomc-meeting__date col-xs-4">27-28</div><div>Statement</div></div>
<div class="row fomc-meeting"><div class="fomc-meeting__month col-xs-5"><strong>Dec</strong></div>
  <div class="fomc-meeting__date col-xs-4">8-9*</div></div>
<h4><a>2027 FOMC Meetings</a></h4>
<div class="row fomc-meeting"><div class="fomc-meeting__month col-xs-5"><strong>Jan/Feb</strong></div>
  <div class="fomc-meeting__date col-xs-4">31-1</div></div>
<div class="row fomc-meeting"><div class="fomc-meeting__month col-xs-5"><strong>Nonsense</strong></div>
  <div class="fomc-meeting__date col-xs-4">tbd</div></div>
"""

BLS_ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;TZID=US-Eastern:20261106T083000
SUMMARY:Employment Situation
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=US-Eastern:20261110T083000
SUMMARY:Consumer Price Index
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=US-Eastern:20261113T083000
SUMMARY:Producer Price Index
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=US-Eastern:20261112T100000
SUMMARY:Metropolitan Area Employment and Unemployment (Monthly)
END:VEVENT
END:VCALENDAR
"""

NOW = datetime(2026, 11, 5, 15, 0, tzinfo=UTC)   # Thursday 10:00 ET


def test_fomc_decision_is_the_meetings_last_day_at_two_pm_eastern():
    events = ec.parse_fomc(FOMC_HTML)
    assert [e.to_dict()["at_et"] for e in events] == [
        "2026-01-28 14:00 ET", "2026-12-09 14:00 ET", "2027-02-01 14:00 ET"]
    assert events[0].at == datetime(2026, 1, 28, 19, 0, tzinfo=UTC)          # EST: UTC-5
    assert {e.id for e in events} == {"fomc"} and all(e.importance == 2 for e in events)


def test_bls_keeps_the_market_movers_in_eastern_time():
    events = ec.parse_bls(BLS_ICS)
    assert [(e.id, e.importance) for e in events] == [("jobs", 2), ("cpi", 2), ("ppi", 1)]
    assert events[0].at == datetime(2026, 11, 6, 13, 30, tzinfo=UTC)          # 08:30 EST


def _calendar() -> ec.Calendar:
    return ec.Calendar(by_source={"fed": ec.parse_fomc(FOMC_HTML), "bls": ec.parse_bls(BLS_ICS)})


def test_upcoming_blackout_and_the_agents_note():
    cal = _calendar()
    assert [e.id for e in cal.upcoming(NOW, days=14)] == ["jobs", "cpi", "ppi"]
    assert [e.id for e in cal.upcoming(NOW, days=14, importance=2)] == ["jobs", "cpi"]
    assert cal.blackout(NOW) is None                                           # jobs report 22.5h away
    assert cal.blackout(NOW + timedelta(hours=12)).id == "jobs"                # 10.5h before it
    assert cal.blackout(datetime(2026, 11, 6, 14, 0, tzinfo=UTC)).id == "jobs"  # 30 min after
    assert cal.blackout(datetime(2026, 11, 6, 15, 0, tzinfo=UTC)) is None
    assert cal.blackout(datetime(2026, 11, 13, 12, 0, tzinfo=UTC)) is None     # PPI is not market-wide

    note = ec.prompt_note(cal, NOW)
    assert "2026-11-06 08:30 ET (in 22h): Jobs report" in note and "MARKET-WIDE" in note
    assert "CPI" in note and "PPI" not in note.split("CPI")[0]
    assert "Event window now" not in note
    assert "Event window now: Jobs report" in ec.prompt_note(cal, NOW + timedelta(hours=12))
    assert ec.prompt_note(ec.Calendar(), NOW) is None

    view = cal.view(NOW + timedelta(hours=12))
    assert view["blackout"]["id"] == "jobs" and view["events"][0]["at_et"] == "2026-11-06 08:30 ET"


def test_refresh_keeps_a_failed_sources_last_events():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers["user-agent"])
        if "federalreserve" in request.url.host:
            return httpx.Response(200, text=FOMC_HTML)
        return httpx.Response(403, text="denied")

    cal = ec.Calendar(by_source={"bls": ec.parse_bls(BLS_ICS)})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await cal.refresh(client, now=NOW)
    assert asyncio.run(go()) == 1
    assert "bls: HTTPStatusError" in cal.error and cal.refreshed_at == NOW.isoformat()
    assert {e.id for e in cal.events()} == {"fomc", "jobs", "cpi", "ppi"}     # BLS kept from before
    assert all("python-httpx" not in ua for ua in calls)                        # official sites refuse it


# --- SEC filings ----------------------------------------------------------------------------

SUBMISSIONS = {
    "name": "TESLA,  INC.",
    "filings": {"recent": {
        "form": ["4", "8-K", "4", "10-Q", "8-K", "4"],
        "filingDate": ["2026-11-02", "2026-10-21", "2026-10-15", "2026-10-22", "2026-08-01", "2026-09-01"],
        "items": ["", "2.02,9.01", "", "", "5.02", ""],
    }},
}


def test_summarise_translates_8k_items_and_counts_insider_filings():
    s = fl.summarise(SUBMISSIONS, date(2026, 11, 5))
    assert s["company"] == "TESLA, INC."
    assert s["8k"] == [{"date": "2026-10-21", "form": "8-K", "items": ["results of operations (earnings)"]}]
    assert s["insider"] == {"count": 2, "last": "2026-11-02", "days": 30}       # 09-01 is too old
    note = fl.prompt_note(s)
    assert "2026-10-21 8-K: results of operations (earnings)" in note and "2 filing(s)" in note
    assert fl.prompt_note({"company": None, "8k": [], "insider": {"count": 0, "last": None, "days": 30}}) is None


def test_us_ticker_rule():
    assert fl.is_us_ticker("NVDA") and fl.is_us_ticker("BRK-B")
    assert not any(fl.is_us_ticker(t) for t in ("005930.KS", "^GSPC", "CL=F", ""))


def test_recent_maps_the_ticker_and_caches_both_lookups():
    hits = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(request.url.path)
        assert "python-httpx" not in request.headers["user-agent"]
        if request.url.path.endswith("company_tickers.json"):
            return httpx.Response(200, json={"0": {"cik_str": 1318605, "ticker": "TSLA", "title": "Tesla"},
                                             "1": {"cik_str": 1067983, "ticker": "BRK-B", "title": "Berkshire"}})
        assert request.url.path == "/submissions/CIK0001318605.json"
        return httpx.Response(200, json=SUBMISSIONS)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            f = fl.Filings(client)
            first = await f.recent("TSLA", date(2026, 11, 5))
            again = await f.recent("TSLA", date(2026, 11, 5))
            unknown = await f.recent("ZZZZ", date(2026, 11, 5))
            korean = await f.recent("005930.KS", date(2026, 11, 5))
            return first, again, unknown, korean, await f.cik("BRK.B")
    first, again, unknown, korean, brk = asyncio.run(go())
    assert first == again and first["8k"][0]["items"] == ["results of operations (earnings)"]
    assert unknown is None and korean is None and brk == 1067983
    assert hits.count("/files/company_tickers.json") == 1 and len(hits) == 2


def test_a_slow_or_failing_sec_never_holds_a_run(monkeypatch):
    class Slow:
        async def recent(self, _t):
            await asyncio.sleep(5)

    monkeypatch.setattr(fl, "FILINGS", Slow())
    started = time.monotonic()
    assert asyncio.run(fl.note_for("NVDA", timeout=0.05)) is None
    assert time.monotonic() - started < 1


# --- GDELT cool-off -------------------------------------------------------------------------

def test_a_gdelt_429_sends_passes_to_google_news_for_fifteen_minutes():
    class Limited:
        calls = 0

        async def get(self, *_, **__):
            Limited.calls += 1
            raise httpx.HTTPStatusError("429", request=httpx.Request("GET", "https://g"),
                                        response=httpx.Response(429, request=httpx.Request("GET", "https://g")))

    async def rss(theme, now):
        return {"id": theme.id, "label": theme.label, "articles_24h": 1, "spike": 1.0, "spike_kind": "raw-share",
                "tone": None, "headlines": [], "why": theme.why, "source": "google-news", "fetched_at": now.isoformat()}

    cache = m.Cache()
    asyncio.run(cache.refresh(gdelt=Limited(), now=NOW, rss=rss))
    assert Limited.calls == 1 and cache.gdelt_resume_at == NOW + timedelta(minutes=15)
    asyncio.run(cache.refresh(gdelt=Limited(), now=NOW + timedelta(minutes=10), rss=rss))
    assert Limited.calls == 1                                   # still cooling off: GDELT not asked
    asyncio.run(cache.refresh(gdelt=Limited(), now=NOW + timedelta(minutes=16), rss=rss))
    assert Limited.calls == 2                                   # tried again after the pause


# --- reaching the agents and the API ---------------------------------------------------------

def test_filings_and_calendar_notes_reach_the_agents_and_the_calendar_api(db_url, monkeypatch):
    from datetime import timedelta as td

    from fastapi.testclient import TestClient

    from desk.api import create_app, desk_news_note
    from desk.auth import Auth
    from desk.db import make_engine
    from desk.runner import make_fake_runner
    from desk.settings import Settings

    monkeypatch.setattr(ec, "CACHE", _calendar())
    seen, rendered = [], []
    fake = make_fake_runner(0)

    def runner(request, emit, portfolio):
        rendered.append(portfolio.render(request.ticker) if portfolio is not None else "")
        return fake(request, emit, portfolio)

    async def filings(ticker):
        seen.append(ticker)
        return "SEC NOTE: 8-K earnings"

    app = create_app(Settings(database_url=db_url, fake_runs=True, fake_delay=0), runner=runner)
    engine = make_engine(db_url)
    Auth(engine, td(hours=1), td(hours=1)).create_user("owner", "correct horse battery")
    engine.dispose()
    with TestClient(app, base_url="https://testserver") as client:
        assert client.get("/api/events/calendar").status_code == 401
        client.post("/api/auth/login", json={"username": "owner", "password": "correct horse battery"})
        manager = client.app.state.manager
        manager.filings, manager.macro = filings, desk_news_note
        run_id = client.post("/api/runs", json={"ticker": "NVDA"}).json()["id"]
        for _ in range(200):
            if client.get(f"/api/runs/{run_id}").json()["status"] in ("done", "failed"):
                break
            time.sleep(0.02)
        assert seen == ["NVDA"] and "SEC NOTE: 8-K earnings" in rendered[0]
        view = client.get("/api/events/calendar", params={"days": 400}).json()
        assert view["error"] is None and isinstance(view["events"], list) and "blackout" in view
