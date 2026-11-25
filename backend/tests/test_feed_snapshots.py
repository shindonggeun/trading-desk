"""#996 the collector refreshes the economic calendar, the macro news and the earnings calendar; the api and the
analyst - other processes when the backend is split (#688) - follow the snapshot files it saves (desk.feed_snapshots).

A process is simulated by its own cache objects: the collector's are built here, the api's / analyst's are the module
caches swapped for fresh ones (a memory that has read nothing), both on the same files (conftest: tmp_path)."""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from desk import earnings, events_calendar, feed_snapshots, gap_fill, macro_news, roles
from tests.test_earnings_resilience import DAY
from tests.test_events_calendar import BLS_ICS, FOMC_HTML, NOW

NOV = (date(2026, 11, 1), date(2026, 11, 30))
# the Fed page ten years back: every meeting long past, so a view on the real clock lists only the BLS rows given
PAST_FOMC = FOMC_HTML.replace("2026 FOMC", "2016 FOMC").replace("2027 FOMC", "2017 FOMC")
JOBS = {"economic_calendar", "macro_news", "earnings"}


@pytest.fixture
def other_process(monkeypatch):
    """Start the api's (or the analyst's) memory: module caches that have read nothing yet."""
    def start():
        monkeypatch.setattr(events_calendar, "CACHE", events_calendar.Calendar())
        monkeypatch.setattr(macro_news, "CACHE", macro_news.Cache())
        monkeypatch.setattr(earnings, "CACHE", earnings.Earnings())
    return start


def _followed(role: str) -> list[feed_snapshots.Snapshot]:
    return feed_snapshots.followed(lambda name: roles.runs(role, name))


def _follow(snapshots) -> list[str]:
    return asyncio.run(feed_snapshots.follow(snapshots))


def _ics(*releases: tuple[str, datetime]) -> str:
    rows = [f"BEGIN:VEVENT\nDTSTART;TZID=US-Eastern:{at.astimezone(events_calendar.ET):%Y%m%dT%H%M%S}\n"
            f"SUMMARY:{summary}\nEND:VEVENT" for summary, at in releases]
    return "BEGIN:VCALENDAR\n" + "\n".join(rows) + "\nEND:VCALENDAR\n"


def _calendar_pass(cal: events_calendar.Calendar, now: datetime, bls: str = BLS_ICS, fed: int = 200,
                   fed_page: str = FOMC_HTML) -> int:
    def handler(request: httpx.Request) -> httpx.Response:
        if "federalreserve" in request.url.host:
            return httpx.Response(fed, text=fed_page)
        return httpx.Response(200, text=bls)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await cal.refresh(client, now=now)
    return asyncio.run(go())


class _GdeltDown:
    sent = 0

    async def get(self, *_args, **_kwargs):
        raise httpx.ConnectError("down")


def _rss(articles: int):
    async def rss(theme, now):
        return {"id": theme.id, "label": theme.label, "articles_24h": articles, "spike": 1.0,
                "spike_kind": "raw-share", "tone": None, "headlines": [], "why": theme.why,
                "source": "google-news", "fetched_at": now.isoformat()}
    return rss


def _macro_pass(cache: macro_news.Cache, now: datetime, articles: int) -> int:
    return asyncio.run(cache.refresh(gdelt=_GdeltDown(), now=now, rss=_rss(articles)))


def _earnings_pass(e: earnings.Earnings, handler) -> int:
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await e.refresh(client, today=date(2026, 11, 9), gap=0, waits=())
    return asyncio.run(go())


def _nasdaq_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=DAY if request.url.params["date"] == "2026-11-18" else {"data": {"rows": []}})


# --- who follows what -------------------------------------------------------------------------

def test_api_and_analyst_follow_the_collectors_three_snapshots_all_and_the_collector_none():
    assert {s.name for s in _followed("api")} == {s.name for s in _followed("analyst")} == JOBS
    # all (the desk before the split) and the collector run the passes themselves: nothing to follow
    assert _followed("all") == [] and _followed("collector") == []


# --- the collector writes, another process reads ------------------------------------------------

@pytest.mark.parametrize("role", ["api", "analyst"])
def test_the_collectors_economic_calendar_and_its_next_pass_reach_another_process(role, other_process):
    collector = events_calendar.Calendar()
    assert _calendar_pass(collector, NOW) == 2
    other_process()
    snapshots = _followed(role)
    assert events_calendar.CACHE.events() == []          # what the split api showed before #996

    assert _follow(snapshots) == []
    mine = events_calendar.CACHE
    assert mine.events() == collector.events() and mine.refreshed_at == NOW.isoformat() and mine.error is None
    assert mine.view(NOW)["events"]                                              # GET /api/events/calendar
    assert mine.blackout(datetime(2026, 11, 6, 4, 0, tzinfo=UTC)).id == "jobs"    # the playbook's blackout
    assert "MARKET-WIDE" in events_calendar.prompt_note(now=NOW)                 # a run's note
    assert set(gap_fill.calendar_days(mine.events()).values()) == {"fomc", "macro"}   # GET /api/gaps causes

    # the collector's next pass: a new release, and the Fed page refused (its last events kept)
    later = NOW + timedelta(hours=6)
    eci = datetime(2026, 11, 20, 13, 30, tzinfo=UTC)
    assert _calendar_pass(collector, later, bls=_ics(("Employment Cost Index", eci)), fed=503) == 1
    assert _follow(snapshots) == []
    assert mine.events() == collector.events() and "eci" in {e.id for e in mine.events()}
    assert {e.id for e in mine.events()} >= {"fomc"}                   # the collector kept the Fed's last events
    assert mine.refreshed_at == later.isoformat() and mine.error == collector.error and "fed" in mine.error


@pytest.mark.parametrize("role", ["api", "analyst"])
def test_the_collectors_macro_news_pass_and_its_next_one_reach_another_process(role, other_process):
    collector = macro_news.Cache()
    assert _macro_pass(collector, NOW, articles=3)
    other_process()
    snapshots = _followed(role)

    assert _follow(snapshots) == []
    mine = macro_news.CACHE
    assert mine.refreshed_at == NOW.isoformat() and mine.status() == "ok"
    assert {k: v["articles_24h"] for k, v in mine.themes.items()} == {k: 3 for k in collector.themes}
    assert not mine.restored            # the collector's current pass, not a snapshot from before a restart
    assert mine.brief(now=NOW)["refreshed_at"] == NOW.isoformat()                # GET /api/macro/brief

    # half an hour later the collector passes again: the other process moves with it (it stayed at its start)
    later = NOW + timedelta(minutes=30)
    assert _macro_pass(collector, later, articles=9)
    assert _follow(snapshots) == []
    assert mine.refreshed_at == later.isoformat() == mine.brief(now=later)["refreshed_at"]
    assert {v["articles_24h"] for v in mine.themes.values()} == {9}


@pytest.mark.parametrize("role", ["api", "analyst"])
def test_the_collectors_earnings_pass_and_its_status_reach_another_process(role, other_process):
    collector = earnings.Earnings(yahoo=lambda _t: None)
    _earnings_pass(collector, _nasdaq_ok)
    other_process()
    snapshots = _followed(role)

    assert _follow(snapshots) == []
    mine = earnings.CACHE
    assert [r["ticker"] for r in mine.for_days(*NOV, set())["2026-11-18"]] == ["NVDA"]
    assert mine.source_status() == collector.source_status()

    # the collector's next pass is refused: the other process shows the same stale rows and the same message
    _earnings_pass(collector, lambda request: httpx.Response(403, text="Access Denied"))
    assert _follow(snapshots) == []
    assert mine.source_status() == collector.source_status() and mine.source_status()["stale"]
    assert mine.for_days(*NOV, set()) == collector.for_days(*NOV, set())       # its stale marks too (pass_at)
    assert mine.pass_at == collector.pass_at and mine.refused and mine.failed_sources == ["nasdaq"]


# --- each role's start: what it loads, what it follows, which jobs it runs --------------------

@pytest.mark.parametrize("role", ["all", "api", "collector", "analyst"])
def test_each_role_starts_on_the_snapshots_it_should(db_url, other_process, monkeypatch, role):
    from datetime import timedelta as td

    from fastapi.testclient import TestClient

    from desk import jobs, prewarm
    from desk.api import create_app
    from desk.auth import Auth
    from desk.db import make_engine, migrate
    from desk.runner import make_fake_runner
    from desk.settings import Settings

    async def idle(self, name, step, retry, failed=None):    # a job is started, its passes (the network) never run
        self.get(name).enabled = self.get(name).looped = True
        await asyncio.Event().wait()

    async def no_prewarm(*_args, **_kwargs):
        return None

    monkeypatch.setattr(jobs.Jobs, "run_forever", idle)
    monkeypatch.setattr(prewarm, "run", no_prewarm)
    # what the collector saved before this process starts (the real clock: the views below read it)
    now = datetime.now(UTC)
    assert _calendar_pass(events_calendar.Calendar(), now, fed_page=PAST_FOMC,
                          bls=_ics(("Consumer Price Index", now + timedelta(days=3))))
    assert _macro_pass(macro_news.Cache(), now, articles=2)
    _earnings_pass(earnings.Earnings(yahoo=lambda _t: None), _nasdaq_ok)
    other_process()
    if role in ("collector", "analyst"):
        migrate(db_url)                  # the api prepares the shared database
    # the feeds switch on, as in production
    app = create_app(Settings(database_url=db_url, fake_runs=False, fake_delay=0, macro_interval_sec=3600, role=role),
                     runner=make_fake_runner(0))
    engine = make_engine(db_url)
    Auth(engine, td(hours=1), td(hours=1)).create_user("owner", "correct horse battery")
    engine.dispose()
    with TestClient(app, base_url="https://testserver") as client:
        client.post("/api/auth/login", json={"username": "owner", "password": "correct horse battery"})
        # read before the first request
        calendar = [e["id"] for e in client.get("/api/events/calendar").json()["events"]]
        brief = client.get("/api/macro/brief").json()
        state = {j["name"]: j["state"] for j in client.get("/api/status").json()["jobs"]}
    on = {name for name in ("snapshots", "snapshots_analyst", "calendar", "news") if state[name] != "off"}
    # all as before #996: no economic calendar until its own first pass; the collector starts on its last file
    assert calendar == ([] if role == "all" else ["cpi"])
    assert brief["refreshed_at"] == now.isoformat()
    assert [r["ticker"] for r in earnings.CACHE.for_days(*NOV, set())["2026-11-18"]] == ["NVDA"]
    # a saved pass loaded where the pass runs (from before a restart); followed elsewhere (the collector's now)
    assert macro_news.CACHE.restored is (role in ("all", "collector"))
    assert on == {"all": {"calendar", "news"}, "collector": {"calendar", "news"}, "api": {"snapshots"},
                  "analyst": {"snapshots_analyst"}}[role]


# --- fail-soft, and only a changed file is read ----------------------------------------------

def test_a_missing_or_broken_snapshot_keeps_what_the_process_had_and_says_so_every_pass(other_process, monkeypatch):
    collector = events_calendar.Calendar()
    _calendar_pass(collector, NOW)
    other_process()
    snapshots = _followed("api")
    _follow(snapshots)
    had = events_calendar.CACHE.events()
    assert had

    path = events_calendar.CACHE_FILE
    path.write_text("{not json", encoding="utf-8")
    taken = []
    take = events_calendar.CACHE.take
    monkeypatch.setattr(events_calendar.CACHE, "take", lambda data: taken.append(1) or take(data))
    first = _follow(snapshots)
    assert len(first) == 1 and first[0].startswith("economic_calendar: broken")
    assert events_calendar.CACHE.events() == had
    assert _follow(snapshots) == first           # every pass while it lasts (the job's line stays error) ...
    path.write_text('{"by_source": "not a dict"}', encoding="utf-8")
    assert _follow(snapshots) == _follow(snapshots) == ["economic_calendar: not a saved pass"]
    assert taken == [1]                          # ... but a file is read and taken once, until it changes
    assert events_calendar.CACHE.events() == had

    real_read = feed_snapshots.Path.read_text

    def refused(self, *args, **kwargs):          # a file that cannot be read now: tried again next pass
        if self == path:
            raise PermissionError("busy")
        return real_read(self, *args, **kwargs)

    _calendar_pass(collector, NOW + timedelta(hours=6))
    monkeypatch.setattr(feed_snapshots.Path, "read_text", refused)
    assert _follow(snapshots) == ["economic_calendar: unreadable: PermissionError"]
    monkeypatch.setattr(feed_snapshots.Path, "read_text", real_read)
    assert _follow(snapshots) == [] and events_calendar.CACHE.refreshed_at == (NOW + timedelta(hours=6)).isoformat()
    path.unlink()
    assert _follow(snapshots) == [] and events_calendar.CACHE.events() == collector.events()   # nothing saved: kept


def test_an_unchanged_snapshot_is_not_taken_again(other_process, monkeypatch):
    collector = events_calendar.Calendar()
    _calendar_pass(collector, NOW)
    other_process()
    snapshots = _followed("api")
    taken = []
    take = events_calendar.CACHE.take
    monkeypatch.setattr(events_calendar.CACHE, "take", lambda data: taken.append(1) or take(data))
    _follow(snapshots)
    _follow(snapshots)
    assert taken == [1]
    _calendar_pass(collector, NOW + timedelta(hours=6))
    _follow(snapshots)
    assert taken == [1, 1]


def test_saved_files_round_trip_refuse_a_bad_one_and_say_when_they_were_not_written(tmp_path):
    # the collector's start: its last saved calendar (a broken file, or one with no events, is refused)
    cal = events_calendar.Calendar()
    assert _calendar_pass(cal, NOW) == 2 and cal.saved
    restarted = events_calendar.Calendar()
    assert restarted.load() and restarted.events() == cal.events() and restarted.refreshed_at == NOW.isoformat()
    for bad in ("{not json", '{"by_source": {"fed": []}}', "[1, 2]"):
        events_calendar.CACHE_FILE.write_text(bad, encoding="utf-8")
        assert not restarted.load() and restarted.events() == cal.events()     # kept as it was
    # a file that cannot be written: the pass says so (the collector's job line shows it)
    (tmp_path / "taken").mkdir()
    blocked = events_calendar.Calendar(cache_file=tmp_path / "taken")
    assert _calendar_pass(blocked, NOW) == 2 and not blocked.saved

    # macro news: a failed pass with a new error is saved too, and its error and GDELT pause reach the follower
    news = macro_news.Cache()
    assert _macro_pass(news, NOW, articles=2)
    news.gdelt_resume_at = NOW + timedelta(minutes=15)

    async def down(theme, now):
        raise httpx.ConnectError("down")

    assert asyncio.run(news.refresh(gdelt=_GdeltDown(), now=NOW + timedelta(minutes=30), rss=down)) == 0
    assert news.error and news.saved
    follower = macro_news.Cache()
    assert follower.take(json.loads(macro_news.CACHE_FILE.read_text(encoding="utf-8")))
    assert follower.error == news.error and follower.gdelt_resume_at == NOW + timedelta(minutes=15)
    assert follower.refreshed_at == NOW.isoformat() and follower.themes == news.themes and not follower.restored
