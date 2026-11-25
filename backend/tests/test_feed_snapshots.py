"""#996 the collector refreshes the economic calendar, the macro news and the earnings calendar; the api and the
analyst - other processes when the backend is split (#688) - follow the snapshot files it saves (desk.feed_snapshots).
#1004 the news digest (the headlines' Korean reads) and the sector news too.

A process is simulated by its own cache objects: the collector's are built here, the api's / analyst's are the module
caches swapped for fresh ones (a memory that has read nothing), both on the same files (conftest: tmp_path)."""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from desk import (
    earnings,
    events_calendar,
    feed_snapshots,
    gap_fill,
    macro_news,
    news_digest,
    roles,
    sector_news,
)
from tests.test_earnings_resilience import DAY
from tests.test_events_calendar import BLS_ICS, FOMC_HTML, NOW
from tests.test_sector_news import AFFAIR
from tests.test_sector_news import NOW as NEWS_NOW
from tests.test_sector_news import _rss as _sector_feed

NOV = (date(2026, 11, 1), date(2026, 11, 30))
# the Fed page ten years back: every meeting long past, so a view on the real clock lists only the BLS rows given
PAST_FOMC = FOMC_HTML.replace("2026 FOMC", "2016 FOMC").replace("2027 FOMC", "2017 FOMC")
JOBS = {"economic_calendar", "macro_news", "earnings", "sector_news", "news_digest"}


@pytest.fixture
def other_process(monkeypatch):
    """Start the api's (or the analyst's) memory: module caches that have read nothing yet."""
    def start():
        monkeypatch.setattr(events_calendar, "CACHE", events_calendar.Calendar())
        monkeypatch.setattr(macro_news, "CACHE", macro_news.Cache())
        monkeypatch.setattr(earnings, "CACHE", earnings.Earnings())
        monkeypatch.setattr(sector_news, "CACHE", sector_news.Cache())       # #1004
        monkeypatch.setattr(news_digest, "DIGEST", news_digest.Digest())
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

def test_api_and_analyst_follow_the_collectors_snapshots_all_and_the_collector_none():
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


# --- #1004 the news digest (the headlines' Korean reads) and the sector news ---------------------------

FED = [("Powell signals patience as the Federal Reserve holds rates", "https://a.example/1"),
       ("Treasury yields slip before the FOMC minutes", "https://a.example/2")]
CUT = ("Fed rate cut bets fade as Powell speaks", "https://a.example/3")
HOT = _sector_feed([(t, 1 + i) for i, t in enumerate(AFFAIR)] + [(f"Old {i}", 60 + i) for i in range(10)])
QUIET = _sector_feed([("Quiet", 50)] * 12)


class _NewsClock(datetime):
    """The views' clock (GET /api/macro/brief and /api/sector-news take no time): the collector's pass time."""
    @classmethod
    def now(cls, tz=None):
        return NEWS_NOW.astimezone(tz) if tz else NEWS_NOW.replace(tzinfo=None)


def _news_pass(cache: macro_news.Cache, now: datetime, heads: list[tuple[str, str]]) -> int:
    """A macro pass whose fed theme carries ``heads`` (title, url), newest first, an hour apart."""
    async def rss(theme, at):
        row = await _rss(3)(theme, at)
        if theme.id == "fed":
            row["headlines"] = [{"title": t, "url": u, "domain": "a", "seen": (at - timedelta(hours=1 + i)).isoformat()}
                                for i, (t, u) in enumerate(heads)]
        return row
    return asyncio.run(cache.refresh(gdelt=_GdeltDown(), now=now, rss=rss))


def _sector_pass(cache: sector_news.Cache, now: datetime) -> int:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=HOT if "Jensen" in request.url.params["q"] else QUIET)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await cache.refresh(client, now=now, gap=0)
    return asyncio.run(go())


def _collector_news(now: datetime = NEWS_NOW):
    """The collector's news pass (desk.api refresh_macro): the themes and their reads, the sector issues and theirs."""
    news, digest, sectors = macro_news.Cache(), news_digest.Digest(news_digest.fake_digest), sector_news.Cache()
    assert _news_pass(news, now, FED)
    assert asyncio.run(digest.refresh(news.themes)) == 2
    assert _sector_pass(sectors, now) and list(sectors.as_themes()) == ["nvidia"]
    assert asyncio.run(digest.refresh(sectors.as_themes())) == news_digest.PER_THEME
    return news, digest, sectors


def _json(value):
    return json.loads(json.dumps(value))


@pytest.mark.parametrize("role", ["api", "analyst"])
def test_the_collectors_news_digest_and_sector_news_reach_another_process(role, other_process):
    news, digest, sectors = _collector_news()
    other_process()
    snapshots = _followed(role)
    assert news_digest.DIGEST.reads == {} and sector_news.CACHE.topics == {}     # what the split api showed before

    assert _follow(snapshots) == []
    # GET /api/macro/brief: each headline's Korean title, each theme's read and the digest's state, as the collector's
    brief = news_digest.DIGEST.annotate(macro_news.CACHE.brief(now=NEWS_NOW))
    assert brief == digest.annotate(news.brief(now=NEWS_NOW))
    fed = next(t for t in brief["themes"] if t["id"] == "fed")
    assert [h["title_ko"] for h in fed["headlines"]] == [f"[번역 예시] {t}" for t, _url in FED]
    assert fed["digest"]["read"] == 2 and brief["digest"]["refreshed_at"] == digest.refreshed_at is not None
    assert list(news_digest.DIGEST.reads.items()) == list(digest.reads.items())   # oldest first, as it evicts
    # GET /api/sector-news (and the earnings preview's check points): the same issue with the same reads
    view = sector_news.CACHE.view(now=NEWS_NOW)
    assert view == sectors.view(now=NEWS_NOW, digest=digest)
    (issue,) = next(s for s in view["sectors"] if s["id"] == "semis")["issues"]
    assert issue["id"] == "nvidia" and issue["headlines"][0]["title_ko"].startswith("[번역 예시]")
    assert view["refreshed_at"] == NEWS_NOW.isoformat()
    # a run's macro note (the analyst's): the sector issue with its read
    assert sector_news.CACHE.note_lines("NVDA", NEWS_NOW) == sectors.note_lines("NVDA", NEWS_NOW, digest=digest)
    assert "Read as positive" in macro_news.prompt_note("NVDA", now=NEWS_NOW)

    # half an hour later the collector passes again with a new headline and reads it: the other process moves too
    later = NEWS_NOW + timedelta(minutes=30)
    assert _news_pass(news, later, [CUT, *FED]) and asyncio.run(digest.refresh(news.themes)) == 1
    assert _follow(snapshots) == []
    assert news_digest.DIGEST.annotate(macro_news.CACHE.brief(now=later)) == digest.annotate(news.brief(now=later))
    assert news_digest.DIGEST.reads[CUT[1]]["title_ko"] == f"[번역 예시] {CUT[0]}"


@pytest.mark.parametrize("role", ["all", "api", "collector", "analyst"])
def test_each_role_serves_the_collectors_news_digest_and_sector_news_it_should(db_url, other_process, monkeypatch,
                                                                                 role):
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
    monkeypatch.setattr(macro_news, "datetime", _NewsClock)
    monkeypatch.setattr(sector_news, "datetime", _NewsClock)
    # what the collector saved before this process starts, and what it shows itself
    news, digest, sectors = _collector_news()
    shown = _json({"brief": digest.annotate(news.brief(now=NEWS_NOW)),
                   "sectors": sectors.view(now=NEWS_NOW, digest=digest)})
    other_process()
    if role in ("collector", "analyst"):
        migrate(db_url)                  # the api prepares the shared database
    app = create_app(Settings(database_url=db_url, fake_runs=False, fake_delay=0, macro_interval_sec=3600, role=role),
                     runner=make_fake_runner(0))
    engine = make_engine(db_url)
    Auth(engine, td(hours=1), td(hours=1)).create_user("owner", "correct horse battery")
    engine.dispose()
    with TestClient(app, base_url="https://testserver") as client:
        client.post("/api/auth/login", json={"username": "owner", "password": "correct horse battery"})
        served = {"brief": client.get("/api/macro/brief").json(), "sectors": client.get("/api/sector-news").json()}
    if role == "all":
        # as before #1004: one process, the reads and the sector issues wait for its own first pass
        assert all(h["title_ko"] is None for t in served["brief"]["themes"] for h in t["headlines"])
        assert served["brief"]["digest"]["refreshed_at"] is None
        assert not any(s["issues"] or s["topics"] for s in served["sectors"]["sectors"])
        assert any(t["headlines"] for t in served["brief"]["themes"])      # the macro snapshot itself loaded (#428)
    else:
        # split: the collector starts on its saved files, the api and the analyst follow them before the first request
        assert served == shown
        assert served["brief"]["digest"]["refreshed_at"] and served["sectors"]["sectors"][0]["issues"]


def test_a_broken_oversized_or_partly_bad_news_file_keeps_what_the_process_had(other_process, monkeypatch):
    _news, _digest, _sectors = _collector_news()
    other_process()
    snapshots = _followed("api")
    assert _follow(snapshots) == []
    had_reads, had_topics = list(news_digest.DIGEST.reads.items()), sector_news.CACHE.topics
    assert had_reads and had_topics

    news_digest.CACHE_FILE.write_text("{not json", encoding="utf-8")
    sector_news.CACHE_FILE.write_text('{"topics": ["not", "a", "dict"]}', encoding="utf-8")
    first = _follow(snapshots)
    assert sorted(first) == ["news_digest: broken: JSONDecodeError", "sector_news: not a saved pass"]
    assert _follow(snapshots) == first                   # every pass while it lasts (the job's line stays error)
    assert list(news_digest.DIGEST.reads.items()) == had_reads and sector_news.CACHE.topics == had_topics

    # a file past its cap is refused unread: a runaway file never reaches the api's memory
    with monkeypatch.context() as small:
        small.setattr(news_digest, "SAVE_MAX_BYTES", 200)
        capped = _followed("api")
    news_digest.CACHE_FILE.write_text(json.dumps({"reads": [], "refreshed_at": None, "error": None, "pad": "x" * 300}),
                                      encoding="utf-8")
    (why,) = [f for f in _follow(capped) if f.startswith("news_digest")]
    assert why.startswith("news_digest: too large") and list(news_digest.DIGEST.reads.items()) == had_reads

    # an entry off the schema is dropped, never guessed into a read; the good ones are taken
    url, read = had_reads[0]
    bad = [["https://bad.example/1", {**read, "impact": "huge"}], ["https://bad.example/2", "not a read"],
           [3, read], ["https://bad.example/3", {**read, "sentiment": {"pos": True, "neg": 0, "neu": 0}}],
           ["https://bad.example/4", {**read, "title_ko": ""}], [url, read]]
    news_digest.CACHE_FILE.write_text(json.dumps({"reads": bad, "refreshed_at": "x", "error": None}), encoding="utf-8")
    sector_rows = _json(had_topics)
    sector_rows["nvidia"]["headlines"].append("not a headline")
    sector_rows["unknown_topic"] = {**sector_rows["nvidia"], "id": "unknown_topic"}
    sector_rows["tsmc"] = {**sector_rows["tsmc"], "articles_24h": "many"}
    quiet = next(k for k, row in sector_rows.items() if k not in ("nvidia", "tsmc") and not row["issue"])
    sector_rows[quiet] = {k: v for k, v in sector_rows[quiet].items() if k not in ("spike", "tag")}   # an older row
    sector_news.CACHE_FILE.write_text(json.dumps({"topics": sector_rows, "refreshed_at": "y", "error": None}),
                                      encoding="utf-8")
    assert _follow(snapshots) == []
    assert list(news_digest.DIGEST.reads.items()) == [(url, read)]
    assert set(sector_news.CACHE.topics) == set(had_topics) - {"tsmc"}
    assert sector_news.CACHE.topics["nvidia"] == had_topics["nvidia"]
    assert sector_news.CACHE.topics[quiet]["spike"] is None and sector_news.CACHE.topics[quiet]["tag"] is None
    assert sector_news.CACHE.view(now=NEWS_NOW)["sectors"]                 # the page still answers
    assert sector_news.CACHE.note_lines("NVDA", NEWS_NOW)

    # nothing saved (the files gone): kept
    news_digest.CACHE_FILE.unlink()
    sector_news.CACHE_FILE.unlink()
    assert _follow(snapshots) == [] and list(news_digest.DIGEST.reads) == [url] and "nvidia" in sector_news.CACHE.topics


def test_the_news_files_round_trip_stay_under_their_caps_and_say_when_they_were_not_written(tmp_path, monkeypatch):
    many = {f"t{n}": {"id": f"t{n}", "headlines": [{"title": f"Headline {n}-{k}", "url": f"https://c.example/{n}/{k}"}
                                                  for k in range(3)]} for n in range(10)}
    d = news_digest.Digest(news_digest.fake_digest)
    assert asyncio.run(d.refresh(many)) == 30 and d.saved
    data = json.loads(news_digest.CACHE_FILE.read_text(encoding="utf-8"))
    assert set(data) == {"reads", "refreshed_at", "error"}            # the reads and the pass's state - nothing else
    follower = news_digest.Digest()
    assert follower.take(data) and list(follower.reads.items()) == list(d.reads.items())
    assert follower.refreshed_at == d.refreshed_at and follower.error is None
    # the collector's start (split): its last saved reads, so a restart neither asks again nor empties the api
    restarted = news_digest.Digest()
    assert restarted.load() and list(restarted.reads.items()) == list(d.reads.items()) and not restarted.load()

    # a failed pass whose error is new is saved too: the api shows the same state
    def boom(_prompt):
        raise RuntimeError("down")

    d.ask = boom
    more = {"late": {"id": "late", "headlines": [{"title": "Late headline", "url": "https://c.example/late"}]}}
    assert asyncio.run(d.refresh(more)) == 0 and d.error == "RuntimeError" and d.saved
    assert follower.take(json.loads(news_digest.CACHE_FILE.read_text(encoding="utf-8")))
    assert follower.error == "RuntimeError" and len(follower.reads) == 30
    # a collector with no reads yet (a restart whose load failed) that fails: the api keeps its reads, shows the error
    assert follower.take({"reads": [], "refreshed_at": None, "error": "TimeoutError"})
    assert follower.error == "TimeoutError" and len(follower.reads) == 30 and follower.refreshed_at == d.refreshed_at

    # a cap that holds only some of them: the file stays under it, the newest are kept (the oldest go first, as KEEP)
    one = len(json.dumps(list(d.reads.items())[-1], ensure_ascii=False).encode("utf-8"))
    monkeypatch.setattr(news_digest, "SAVE_MAX_BYTES", one * 10)
    assert d.save()
    assert news_digest.CACHE_FILE.stat().st_size <= one * 10
    capped = news_digest.Digest()
    assert capped.take(json.loads(news_digest.CACHE_FILE.read_text(encoding="utf-8")))
    assert 0 < len(capped.reads) < 30 and list(capped.reads.items()) == list(d.reads.items())[-len(capped.reads):]
    # more reads in a file than this process keeps: the newest KEEP
    monkeypatch.setattr(news_digest, "KEEP", 5)
    kept = news_digest.Digest()
    assert kept.take(data) and list(kept.reads.items()) == list(d.reads.items())[-5:]
    # a URL past URL_MAX is never written
    d.reads["https://c.example/" + "x" * news_digest.URL_MAX] = next(iter(d.reads.values()))
    monkeypatch.setattr(news_digest, "SAVE_MAX_BYTES", 10_000_000)
    assert d.save() and "x" * news_digest.URL_MAX not in news_digest.CACHE_FILE.read_text(encoding="utf-8")
    # a file that cannot be written: the pass says so (the collector's job line shows it)
    (tmp_path / "taken").mkdir()
    blocked = news_digest.Digest(news_digest.fake_digest, cache_file=tmp_path / "taken")
    assert asyncio.run(blocked.refresh(many)) == 30 and not blocked.saved
    blocked.cache_file = tmp_path / "free.json"          # the next pass writes again, even with nothing new read
    assert asyncio.run(blocked.refresh({})) == 0 and blocked.saved and (tmp_path / "free.json").exists()

    # the sector news: the same round trip, the collector's start, the cap and a failed write
    s = sector_news.Cache()
    assert _sector_pass(s, NEWS_NOW) and s.saved
    data = json.loads(sector_news.CACHE_FILE.read_text(encoding="utf-8"))
    assert set(data) == {"topics", "refreshed_at", "error"}
    follower = sector_news.Cache()
    assert follower.take(data) and follower.topics == s.topics and follower.refreshed_at == s.refreshed_at
    restarted = sector_news.Cache()
    assert restarted.load() and restarted.topics == s.topics and not restarted.load()
    monkeypatch.setattr(sector_news, "SAVE_MAX_BYTES", 100)
    assert not s.save()                                  # refused, not cut: a topic is whole or not at all
    monkeypatch.setattr(sector_news, "SAVE_MAX_BYTES", 10_000_000)
    (tmp_path / "taken2").mkdir()
    blocked = sector_news.Cache(cache_file=tmp_path / "taken2")
    assert _sector_pass(blocked, NEWS_NOW) and not blocked.saved
