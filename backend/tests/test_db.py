from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from desk.db import make_engine, metadata, migrate
from desk.store import Store


def test_migrations_build_exactly_the_declared_schema(db_url):
    migrate(db_url)
    migrate(db_url)  # idempotent: a restart must not fail
    engine = make_engine(db_url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), metadata)
    engine.dispose()
    assert diff == [], "db.py and the Alembic revisions disagree — add a revision"


def test_store_keeps_order_json_and_utc_times(db_url):
    migrate(db_url)
    engine = make_engine(db_url)
    store = Store(engine)
    run_id = store.create_run("NVDA", "2026-11-04", {"analysts": ["market"]})
    first = store.append_event(run_id, {"type": "status", "content": "queued"})
    store.append_event(run_id, {"type": "plan", "data": {"entry_price": 182.5, "note": "한글"}})

    replay = store.events_after(run_id, 0)
    assert [e["seq"] for e in replay] == [1, 2]
    assert replay[0]["data"] is None
    assert replay[1]["data"] == {"entry_price": 182.5, "note": "한글"}
    assert replay[0]["ts"] == first["ts"] and first["ts"].endswith("+00:00")
    assert store.events_after(run_id, 1)[0]["type"] == "plan"

    store.mark_done(run_id, {"rating": "Hold"}, None)
    run = store.get_run(run_id)
    assert run["status"] == "done" and run["plan"] == {"rating": "Hold"}
    assert run["options"] == {"analysts": ["market"]}
    assert run["created_at"].endswith("+00:00")
    assert store.run_status(run_id) == "done" and store.run_status("nope") is None

    store.create_run("AAPL", "2026-11-04", {})
    assert store.fail_unfinished("restart") == 1
    engine.dispose()
