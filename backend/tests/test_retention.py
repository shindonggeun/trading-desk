from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from desk.api import create_app, purge_expired_runs
from desk.db import events, make_engine, migrate, now, runs
from desk.runner import make_fake_runner
from desk.settings import Settings
from desk.store import Store


def _store(db_url) -> Store:
    migrate(db_url)
    return Store(make_engine(db_url))


def _run(store: Store, age_days: int, status: str) -> str:
    run_id = store.create_run("NVDA", "2026-01-02", {})
    store.append_event(run_id, {"type": "status", "content": status})
    with store.engine.begin() as conn:
        conn.execute(update(runs).where(runs.c.id == run_id)
                     .values(status=status, created_at=now() - timedelta(days=age_days)))
    return run_id


def _event_count(store: Store, run_id: str) -> int:
    with store.engine.connect() as conn:
        return conn.execute(select(func.count()).where(events.c.run_id == run_id)).scalar_one()


def test_old_finished_runs_go_with_their_events_and_the_rest_stay(db_url):
    store = _store(db_url)
    old_done = _run(store, 91, "done")
    old_failed = _run(store, 200, "failed")
    old_running = _run(store, 120, "running")
    old_queued = _run(store, 120, "queued")
    recent = _run(store, 89, "done")

    assert purge_expired_runs(store, 90) == 2

    for gone in (old_done, old_failed):
        assert store.get_run(gone) is None
        assert _event_count(store, gone) == 0
    for kept in (old_running, old_queued, recent):
        assert store.get_run(kept) is not None
        assert _event_count(store, kept) == 1
    assert purge_expired_runs(store, 90) == 0
    store.engine.dispose()


def test_zero_retention_keeps_everything(db_url):
    store = _store(db_url)
    ancient = _run(store, 3650, "done")
    assert purge_expired_runs(store, 0) == 0
    assert store.get_run(ancient) is not None
    store.engine.dispose()


def test_app_start_sweeps_expired_runs(db_url):
    store = _store(db_url)
    old = _run(store, 100, "done")
    recent = _run(store, 1, "done")
    settings = Settings(database_url=db_url, fake_runs=True, fake_delay=0, retention_days=90)
    with TestClient(create_app(settings, runner=make_fake_runner(0)), base_url="https://testserver"):
        pass
    assert store.get_run(old) is None
    assert store.get_run(recent) is not None
    store.engine.dispose()
