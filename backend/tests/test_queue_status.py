"""#688 a queued run says where it stands (``queue``): runs ahead of it, runs running, and whether an analyst
holds the queue. The analyst's lock is PostgreSQL only (CI); SQLite answers None there."""

import pytest

from desk import run_events, run_queue
from desk.db import make_engine, migrate
from tests.test_api import _client, _wait_done


def test_queued_runs_say_how_many_are_ahead_and_running(db_url):
    with _client(db_url, role="api") as api:            # nothing claims: they stay queued
        first = api.post("/api/runs", json={"ticker": "NVDA"}).json()
        second = api.post("/api/runs", json={"ticker": "AAPL"}).json()
        one = api.get(f"/api/runs/{second['id']}").json()
        items = {r["id"]: r for r in api.get("/api/runs").json()["items"]}
    expected = None if db_url.startswith("sqlite") else "down"     # no analyst holds the queue
    assert first["queue"] == {"ahead": 0, "running": 0, "analyst": expected}
    assert one["queue"] == {"ahead": 1, "running": 0, "analyst": expected}
    assert items[first["id"]]["queue"]["ahead"] == 0 and items[second["id"]]["queue"]["ahead"] == 1


def test_a_run_that_is_not_queued_has_no_queue_and_all_says_nothing_of_an_analyst(db_url):
    with _client(db_url) as client:
        run_id = client.post("/api/runs", json={"ticker": "NVDA"}).json()["id"]
        run = _wait_done(client, run_id)
        listed = client.get("/api/runs").json()["items"]
    assert "queue" not in run and all("queue" not in r for r in listed)


def test_the_api_sees_whether_an_analyst_holds_the_queue(db_url):
    migrate(db_url)
    engine = make_engine(db_url)
    if not run_events.shared(engine):
        engine.dispose()
        pytest.skip("PostgreSQL only (CI sets DESK_TEST_DATABASE_URL)")
    holder = run_queue.Holder(engine)
    try:
        assert run_queue.held(engine) is False
        assert holder.hold()                             # an analyst
        assert run_queue.held(engine) is True
        with _client(db_url, role="api") as api:
            run = api.post("/api/runs", json={"ticker": "NVDA"}).json()
        assert run["queue"]["analyst"] == "up"
        holder.release()
        assert run_queue.held(engine) is False
    finally:
        holder.release()
        engine.dispose()


def test_sqlite_has_no_queue_lock_to_look_at(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'd.db').as_posix()}")
    try:
        assert run_queue.held(engine) is None
    finally:
        engine.dispose()
