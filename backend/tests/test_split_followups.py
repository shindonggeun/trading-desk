"""#1005 what a split backend (#688) kept in one process's memory, read from the database where it is needed:
the favorites the earnings pass asks for (the collector runs the pass, the api's GET /api/calendar asked in its own
memory) and the playbooks' live record (the analyst scores, the api's playbooks calibrate).

A process is an app started with its DESK_ROLE on the shared database; the jobs under test run their real passes
(desk.jobs.Jobs.run_forever), every other job is started but idle - no network."""

import asyncio
import logging
import time
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from desk import calibration, earnings, events_calendar, jobs, outcome, prewarm
from desk.api import create_app
from desk.auth import Auth
from desk.db import make_engine, migrate, users
from desk.favorites import Favorites
from desk.runner import make_fake_runner
from desk.settings import Settings
from desk.store import Store

PASSWORD = "correct horse battery"
WAIT_SEC = 15.0
KR_WATCHED = "035900.KQ"       # a Korean favorite outside earnings.DEFAULT_KR
US_WATCHED = "SNOW"            # a US favorite outside earnings.MAJOR_US
CELL = ("swing", "long", "dip_catch", "crash", 70)


def _run_only(monkeypatch, *names: str) -> None:
    """The jobs ``names`` run their real loop (the first pass at the start); the others start and idle."""
    real = jobs.Jobs.run_forever

    async def only(self, name, step, retry, failed=None):
        if name in names:
            await real(self, name, step, retry, failed)
            return
        self.get(name).enabled = self.get(name).looped = True
        await asyncio.Event().wait()

    async def no_prewarm(*_args, **_kwargs):
        return None

    monkeypatch.setattr(jobs.Jobs, "run_forever", only)
    monkeypatch.setattr(prewarm, "run", no_prewarm)


def _accounts(db_url: str, *favorites: list[str]) -> None:
    """The shared database the api prepares, with one account per list of starred tickers."""
    migrate(db_url)
    engine = make_engine(db_url)
    auth = Auth(engine, timedelta(hours=1), timedelta(hours=1))
    starred = Favorites(engine, lambda _t: None)
    for i, tickers in enumerate(favorites or ([],)):
        name = "owner" if i == 0 else f"guest{i}"
        auth.create_user(name, PASSWORD)
        with engine.connect() as conn:
            user_id = conn.scalar(select(users.c.id).where(users.c.username == name))
        for t in tickers:
            starred.add(user_id, t)
    engine.dispose()


def _app(db_url: str, role: str, **settings):
    return create_app(Settings(database_url=db_url, fake_delay=0, role=role, **settings), runner=make_fake_runner(0))


def _status(client) -> dict[str, dict]:
    return {j["name"]: j for j in client.get("/api/status").json()["jobs"]}


def _first_pass(client, name: str) -> dict:
    """The job's line once its first pass has ended (ok or not)."""
    deadline = time.monotonic() + WAIT_SEC
    while time.monotonic() < deadline:
        job = _status(client)[name]
        if job["state"] in ("fresh", "error"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"{name}: no pass ended in {WAIT_SEC:.0f}s")


def _login(client) -> None:
    assert client.post("/api/auth/login", json={"username": "owner", "password": PASSWORD}).status_code == 204


# --- the collector's earnings pass asks for the favorites in the database --------------------------

@pytest.fixture
def earnings_sources(monkeypatch):
    """The earnings pass on stand-in sources: Nasdaq refused a moment ago (not asked: no network, no waits),
    Yahoo answers every ticker it is asked for with a row; the economic calendar reads nothing."""
    asked: list[str] = []

    def yahoo(ticker: str) -> dict:
        asked.append(ticker)
        return {"ticker": ticker, "date": date.today().isoformat()}

    cache = earnings.Earnings(yahoo=yahoo)
    cache.refused_at = datetime.now(UTC).isoformat(timespec="seconds")
    monkeypatch.setattr(earnings, "CACHE", cache)
    calendar = events_calendar.Calendar()

    async def no_calendar(*_args, **_kwargs) -> int:
        return 0

    monkeypatch.setattr(calendar, "refresh", no_calendar)
    monkeypatch.setattr(events_calendar, "CACHE", calendar)
    return asked


@pytest.mark.parametrize("role", ["collector", "all"])
def test_the_earnings_pass_asks_for_every_accounts_favorites_read_from_the_database(
        db_url, monkeypatch, earnings_sources, role):
    _accounts(db_url, [KR_WATCHED, "^KS11"], [US_WATCHED])
    _run_only(monkeypatch, "calendar")
    # the feeds switch on, as in production; no page has asked for the month calendar in this process
    with TestClient(_app(db_url, role, fake_runs=False, macro_interval_sec=3600), base_url="https://testserver") as client:
        _login(client)
        _first_pass(client, "calendar")
        part = _status(client)["earnings"]
    assert part["state"] == "fresh"
    # the default Korean list and the big US names as before, plus both accounts' favorites (an index is no report)
    assert set(earnings.DEFAULT_KR) <= set(earnings_sources)
    assert KR_WATCHED in earnings_sources and US_WATCHED in earnings_sources and "^KS11" not in earnings_sources
    assert KR_WATCHED in earnings.CACHE.kr and US_WATCHED in earnings.CACHE.details
    assert KR_WATCHED in earnings.CACHE.want_kr and US_WATCHED in earnings.CACHE.want_us


def test_a_failed_favorites_read_still_runs_the_earnings_pass_and_only_warns(
        db_url, monkeypatch, earnings_sources, caplog):
    _accounts(db_url, [KR_WATCHED])

    def down(self, limit):
        raise OperationalError("SELECT favorites", {}, ConnectionError("database down"))

    monkeypatch.setattr(Favorites, "tickers", down)
    _run_only(monkeypatch, "calendar")
    with caplog.at_level(logging.WARNING, logger="desk.api"), \
            TestClient(_app(db_url, "collector", fake_runs=False, macro_interval_sec=3600),
                       base_url="https://testserver") as client:
        _login(client)
        _first_pass(client, "calendar")
        part = _status(client)["earnings"]
    # the pass ran on what it had (the default list) and its line is not an error: a warning in the log only
    assert part["state"] == "fresh"
    assert set(earnings.DEFAULT_KR) <= set(earnings_sources) and KR_WATCHED not in earnings_sources
    warned = [r for r in caplog.records if r.levelno == logging.WARNING and "favorites" in r.getMessage()]
    assert warned and "database down" not in warned[0].getMessage()      # the error's type only


# --- the api reads the live record the analyst's scoring wrote -----------------------------------

def _scored_runs(db_url: str, won: int, lost: int) -> None:
    store = Store(make_engine(db_url))
    plan = {"horizon": "swing", "playbooks": {"regime": {"id": "crash"}, "long": {"setup": "dip_catch", "score": 70},
                                              "short": {"setup": "none"}}}
    today = date.today().isoformat()
    for i in range(won + lost):
        hit = i < won
        run_id = store.create_run("NVDA", today, {})
        store.mark_done(run_id, plan, None)
        store.set_outcome(run_id, {"status": "t2" if hit else "stop", "playbooks": {
            "long": {"status": "t2" if hit else "stop", "took_t1": hit, "r_size": 1.5 if hit else -1.0},
            "short": {"status": "flat"}}})
    store.engine.dispose()


def test_the_apis_playbooks_calibrate_on_the_live_record_the_analyst_scored(db_url, monkeypatch):
    _accounts(db_url)
    _scored_runs(db_url, won=30, lost=10)
    monkeypatch.setattr(calibration, "TABLE", calibration.Table.load())

    async def nothing_new(*_args, **_kwargs) -> int:      # the outcomes are in the database already
        return 0

    monkeypatch.setattr(outcome, "sweep", nothing_new)
    _run_only(monkeypatch, "scoring", "calibration_live")
    # the analyst (desk.api score_plans): its scoring reads the live record at boot
    with TestClient(_app(db_url, "analyst", fake_runs=False, score_interval_sec=3600),
                    base_url="https://testserver") as client:
        _login(client)
        assert _first_pass(client, "scoring")["state"] == "fresh"
    analyst = calibration.TABLE.look(*CELL)
    assert analyst["live_n"] == 40
    # the api's own memory: the backtest prior only, until it reads the record itself
    monkeypatch.setattr(calibration, "TABLE", calibration.Table.load())
    assert calibration.TABLE.look(*CELL)["live_n"] == 0
    with TestClient(_app(db_url, "api", fake_runs=False, score_interval_sec=3600),
                    base_url="https://testserver") as client:
        _login(client)
        job = _first_pass(client, "calibration_live")
    assert job["state"] == "fresh" and job["interval_sec"] == 3600      # on the scoring's beat
    assert calibration.TABLE.look(*CELL) == analyst


@pytest.mark.parametrize(("role", "on"), [
    ("all", {"scoring"}), ("api", {"calibration_live"}), ("collector", set()), ("analyst", {"scoring"})])
def test_the_live_record_is_read_back_only_where_the_scoring_does_not_run(db_url, monkeypatch, role, on):
    _accounts(db_url)
    _run_only(monkeypatch)
    with TestClient(_app(db_url, role, fake_runs=False, score_interval_sec=3600),
                    base_url="https://testserver") as client:
        _login(client)
        state = _status(client)
    assert {n for n in ("scoring", "calibration_live") if state[n]["state"] != "off"} == on


def test_no_live_record_to_read_back_when_scoring_is_off(db_url, monkeypatch):
    # scripted runs score nothing (all would have no live record either), and DESK_SCORE_INTERVAL_SEC=0 is off
    _accounts(db_url)
    _run_only(monkeypatch)
    for settings in ({"fake_runs": True, "score_interval_sec": 3600}, {"fake_runs": False, "score_interval_sec": 0}):
        with TestClient(_app(db_url, "api", **settings), base_url="https://testserver") as client:
            _login(client)
            assert _status(client)["calibration_live"]["state"] == "off"
