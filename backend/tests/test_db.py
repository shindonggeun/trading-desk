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
    # what was saved comes back as saved; #311's level sides are worked out on read
    assert run["status"] == "done" and run["plan"] == {"rating": "Hold", "levels_side": None, "levels_note": None,
                                                       "side_conflict": False, "trigger_warnings": []}
    assert run["options"] == {"analysts": ["market"]}
    assert run["created_at"].endswith("+00:00")
    assert store.run_status(run_id) == "done" and store.run_status("nope") is None

    store.create_run("AAPL", "2026-11-04", {})
    assert store.fail_unfinished("restart") == 1
    engine.dispose()


def test_0007_renames_old_board_favorites(db_url):
    """#238: leveraged ETFs by ticker, ^NDX ("weekend Nasdaq 100") as NQ=F unless already starred."""
    from alembic import command
    from sqlalchemy import text

    from desk.db import alembic_config

    command.upgrade(alembic_config(db_url), "0006")
    engine = make_engine(db_url)
    with engine.begin() as conn:
        for uid in (1, 2):
            conn.execute(text("INSERT INTO users (id, username, password_hash, created_at, password_changed_at) "
                              "VALUES (:id, :u, 'x', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"), {"id": uid, "u": f"u{uid}"})
        rows = [(1, "SOXL", "반도체 3배", 0), (1, "^NDX", "위켄드 나스닥100", 1), (1, "NVDA", "엔비디아", 2),
                (2, "^NDX", "위켄드 나스닥100", 0), (2, "NQ=F", "나스닥 선물", 1), (2, "TQQQ", "내 TQQQ", 2)]
        for uid, ticker, name, pos in rows:
            conn.execute(text("INSERT INTO favorites (user_id, ticker, name, position, created_at) "
                              "VALUES (:u, :t, :n, :p, CURRENT_TIMESTAMP)"), {"u": uid, "t": ticker, "n": name, "p": pos})
    migrate(db_url)
    with engine.connect() as conn:
        got = sorted(tuple(r) for r in conn.execute(text("SELECT user_id, ticker, name FROM favorites")))
    engine.dispose()
    assert got == [(1, "NQ=F", "나스닥100 선물"), (1, "NVDA", "엔비디아"), (1, "SOXL", "SOXL"),
                   (2, "NQ=F", "나스닥 선물"), (2, "TQQQ", "내 TQQQ")]
