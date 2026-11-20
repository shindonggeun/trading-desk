"""#688 the api queues a run, the analyst claims and runs it (desk.run_queue).

Two apps on one database stand for two processes. The claim itself runs on SQLite too, but SQLite has no row
locks: two claims racing for one run (``FOR UPDATE SKIP LOCKED``) and the one-analyst advisory lock are tested
on PostgreSQL only (CI, DESK_TEST_DATABASE_URL)."""

import asyncio
import threading
import time
from contextlib import contextmanager
from datetime import timedelta
from secrets import randbits

import pytest
from fastapi.testclient import TestClient

from desk import run_events, run_queue
from desk.api import create_app
from desk.auth import Auth
from desk.db import make_engine, migrate
from desk.runner import make_fake_runner
from desk.settings import Settings
from desk.store import Store
from tests.test_api import OWNER, PASSWORD, _client, _wait_done


@pytest.fixture(autouse=True)
def isolate_queue_lock(monkeypatch):
    # xdist reuses each worker's database. A session from a previous test can outlive the table reset.
    # Holders within this test still compete for the same PostgreSQL advisory lock.
    monkeypatch.setattr(run_queue, "LOCK_KEY", randbits(63))


@contextmanager
def _process(db_url, role, **overrides):
    """Another process of the desk on the same database: no login of its own."""
    settings = Settings(database_url=db_url, fake_runs=True, fake_delay=0, role=role, **overrides)
    with TestClient(create_app(settings, runner=make_fake_runner(0)), base_url="https://testserver") as client:
        yield client


def _status(db_url, run_id):
    engine = make_engine(db_url)
    try:
        return Store(engine).get_run(run_id)["status"]
    finally:
        engine.dispose()


def test_the_api_only_queues_and_says_it_waits_for_the_analyst(db_url):
    with _client(db_url, role="api") as api:
        run_id = api.post("/api/runs", json={"ticker": "NVDA"}).json()["id"]
        time.sleep(1.5)
        assert api.get(f"/api/runs/{run_id}").json()["status"] == "queued"
        first = api.get(f"/api/runs/{run_id}/events").json()[0]
    assert first["content"] == "queued" and first["data"] == {"waiting_for": "analyst"}


def test_all_still_runs_a_run_straight_away_without_the_queue(db_url):
    with _client(db_url) as client:
        run_id = client.post("/api/runs", json={"ticker": "NVDA"}).json()["id"]
        assert _wait_done(client, run_id)["status"] == "done"
        first = client.get(f"/api/runs/{run_id}/events").json()[0]
        jobs = {j["name"]: j["state"] for j in client.get("/api/status").json()["jobs"]}
    assert first["content"] == "queued" and first.get("data") is None
    assert jobs["run_queue"] == "off"


def test_the_analyst_runs_what_the_api_queued(db_url):
    with _client(db_url, role="api") as api:
        run_id = api.post("/api/runs", json={"ticker": "NVDA"}).json()["id"]
        with _process(db_url, "analyst"):
            run = _wait_done(api, run_id)
        kinds = [e["content"] for e in api.get(f"/api/runs/{run_id}/events").json() if e["type"] == "status"]
    assert run["status"] == "done" and run["plan"]
    assert kinds[:2] == ["queued", "running"]


def test_restarts_leave_the_runs_of_another_process_alone(db_url):
    migrate(db_url)
    engine = make_engine(db_url)
    store = Store(engine)
    running = store.create_run("NVDA", "2026-11-20", {"ticker": "NVDA", "trade_date": "2026-11-20"})
    store.mark_running(running)
    queued = store.create_run("AAPL", "2026-11-20", {"ticker": "AAPL", "trade_date": "2026-11-20"})
    engine.dispose()

    for role in ("api", "collector"):                # never touch the analyst's runs
        with _process(db_url, role):
            pass
        assert (_status(db_url, running), _status(db_url, queued)) == ("running", "queued")

    with _process(db_url, "analyst"):                # the previous analyst's running run died with it
        deadline = time.monotonic() + 30
        while _status(db_url, queued) not in ("done", "failed") and time.monotonic() < deadline:
            time.sleep(0.05)
    assert _status(db_url, running) == "failed"
    assert _status(db_url, queued) == "done"          # the queued one was claimed and run
    engine = make_engine(db_url)
    try:
        assert Store(engine).get_run(running)["error"] == run_queue.RESTART_REASON   # Korean, like #431's
    finally:
        engine.dispose()


def test_a_queued_run_whose_options_make_no_request_fails_instead_of_hanging(db_url):
    migrate(db_url)
    engine = make_engine(db_url)
    store = Store(engine)
    no_ticker = store.create_run("NVDA", "2026-11-20", {"trade_date": "2026-11-20"})
    not_an_object = store.create_run("AAPL", "2026-11-20", ["AAPL"])
    with _process(db_url, "analyst"):
        deadline = time.monotonic() + 30
        while "queued" in {_status(db_url, r) for r in (no_ticker, not_an_object)} and time.monotonic() < deadline:
            time.sleep(0.05)
    try:
        for run_id in (no_ticker, not_an_object):
            run = store.get_run(run_id)
            assert (run["status"], run["error"]) == ("failed", run_queue.UNREADABLE)
            assert [e["content"] for e in store.events_after(run_id)][-1] == "failed"   # the stream ends
    finally:
        engine.dispose()


def test_the_analyst_claims_only_as_many_as_its_gate_lets_run(db_url):
    migrate(db_url)
    engine = make_engine(db_url)
    store = Store(engine)
    for ticker in ("NVDA", "AAPL"):
        store.create_run(ticker, "2026-11-20", {"ticker": ticker, "trade_date": "2026-11-20"})
    manager = _Manager(room=1)
    queue = run_queue.RunQueue(store, manager, run_queue.Holder(engine))
    try:
        assert asyncio.run(queue.take()) == 1 and len(manager.started) == 1   # one slot, one claim
        assert [r["status"] for r in store.list_runs()].count("queued") == 1
    finally:
        asyncio.run(queue.close())
        engine.dispose()


def test_a_newer_option_the_analyst_does_not_know_is_dropped_and_reused_is_never_read_back():
    request = run_queue._request({"ticker": "NVDA", "trade_date": "2026-11-20", "from_the_future": 1,
                                  "reused": {"news": "a report nobody wrote"}})
    assert request.ticker == "NVDA" and request.reused is None


class _Manager:
    def __init__(self, room=1):
        self.started, self.on_free, self.slots = [], None, room

    def room(self):
        return len(self.started) < self.slots

    def run_ids(self):
        return set()

    def start(self, run_id, request):
        self.started.append(request.ticker)


def test_a_run_ending_claims_the_next_at_once_and_a_stopped_queue_claims_nothing(db_url):
    migrate(db_url)
    engine = make_engine(db_url)
    store = Store(engine)
    for ticker in ("NVDA", "AAPL", "MSFT"):
        store.create_run(ticker, "2026-11-20", {"ticker": ticker, "trade_date": "2026-11-20"})
    manager = _Manager(room=1)
    queue = run_queue.RunQueue(store, manager, run_queue.Holder(engine))

    async def scenario():
        try:
            assert await queue.take() == 1
            manager.slots = 2                         # the first run ended: room again
            manager.on_free()                         # -> kick
            async with asyncio.timeout(10):
                while len(manager.started) < 2:
                    await asyncio.sleep(0.01)
        finally:
            await queue.close()
        manager.slots = 3
        assert await queue.take() == 0 and manager.on_free is None

    try:
        asyncio.run(scenario())
        assert len(manager.started) == 2
        assert [r["status"] for r in store.list_runs()].count("queued") == 1
    finally:
        engine.dispose()


def test_a_claim_put_back_is_queued_again(db_url):
    migrate(db_url)
    engine = make_engine(db_url)
    store = Store(engine)
    run_id = store.create_run("NVDA", "2026-11-20", {"ticker": "NVDA", "trade_date": "2026-11-20"})
    try:
        assert store.claim_queued()[0] == run_id and store.get_run(run_id)["status"] == "running"
        store.requeue(run_id)
        assert store.get_run(run_id)["status"] == "queued" and store.claim_queued()[0] == run_id
    finally:
        engine.dispose()


def test_a_queued_run_has_its_first_event_from_the_start(db_url):
    migrate(db_url)
    engine = make_engine(db_url)
    store = Store(engine)
    try:
        run_id = store.create_run("NVDA", "2026-11-20", {}, first_event={"type": "status", "content": "queued"})
        assert [(e["seq"], e["content"]) for e in store.events_after(run_id)] == [(1, "queued")]
    finally:
        engine.dispose()


# --- PostgreSQL only: row locks and the one-analyst lock -----------------------------------------------------


@pytest.fixture
def pg(db_url):
    migrate(db_url)
    engine = make_engine(db_url)
    if not run_events.shared(engine):
        engine.dispose()
        pytest.skip("PostgreSQL only (CI sets DESK_TEST_DATABASE_URL)")
    yield engine
    engine.dispose()


def test_claims_racing_never_take_the_same_run(pg, db_url):
    store = Store(pg)
    ids = {store.create_run(f"T{i}", "2026-11-20", {"ticker": f"T{i}", "trade_date": "2026-11-20"}) for i in range(20)}
    others = [make_engine(db_url) for _ in range(4)]
    taken: list[str] = []
    lock, go = threading.Lock(), threading.Barrier(len(others))

    def claim_all(engine):
        own = Store(engine)
        go.wait()
        while (claimed := own.claim_queued()) is not None:
            with lock:
                taken.append(claimed[0])

    threads = [threading.Thread(target=claim_all, args=(e,)) for e in others]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    for e in others:
        e.dispose()
    assert sorted(taken) == sorted(ids)              # every run once, none twice


def test_a_second_analyst_claims_nothing_while_the_first_holds_the_queue(pg):
    store = Store(pg)
    run_id = store.create_run("NVDA", "2026-11-20", {"ticker": "NVDA", "trade_date": "2026-11-20"})
    first, second = run_queue.Holder(pg), run_queue.Holder(pg)
    try:
        assert first.hold() and first.hold()          # held, and still held
        assert not second.hold()

        queue = run_queue.RunQueue(store, _Manager(room=5), second)
        assert asyncio.run(queue.take()) == 0 and queue.blocked
        assert store.get_run(run_id)["status"] == "queued"
        first.release()                               # the first analyst stopped: unlocked at once
        assert second.hold()
    finally:
        first.release()
        second.release()


def test_status_shows_a_blocked_second_analyst(pg, db_url):
    holder = run_queue.Holder(pg)
    try:
        assert holder.hold()                          # the first analyst
        with _process(db_url, "analyst") as second:
            Auth(pg, timedelta(hours=1), timedelta(hours=1)).create_user(OWNER, PASSWORD)
            second.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                jobs = {j["name"]: j for j in second.get("/api/status").json()["jobs"]}
                if jobs["run_queue"]["state"] == "error":
                    break
                time.sleep(0.1)
        assert jobs["run_queue"]["state"] == "error"
        assert jobs["run_queue"]["last_error"]
    finally:
        holder.release()


def test_an_all_process_beside_the_queue_holder_leaves_its_runs_alone(pg, db_url):
    store = Store(pg)
    run_id = store.create_run("NVDA", "2026-11-20", {"ticker": "NVDA", "trade_date": "2026-11-20"})
    store.mark_running(run_id)
    holder = run_queue.Holder(pg)                     # an analyst running it
    try:
        assert holder.hold()
        with _process(db_url, "all"):
            pass
        assert store.get_run(run_id)["status"] == "running"
    finally:
        holder.release()
