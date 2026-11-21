from io import StringIO

import pytest
from alembic import command
from sqlalchemy import Boolean, MetaData, Table, column, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import dialect as postgresql_dialect
from sqlalchemy.exc import IntegrityError, StatementError

from desk.db import alembic_config, make_engine, migrate, now, runs
from desk.run_access import can_access_run, shared_run_rows, visibility_for_new_run, visible_run_rows
from desk.store import Store


@pytest.mark.parametrize(
    ("use_portfolio", "sources", "expected"),
    [
        (False, (), True),
        (False, (True, True), True),
        (False, None, None),
        (False, "", None),
        (False, [], None),
        (False, (True, False), None),
        (False, (True, None), None),
        (False, ("true",), None),
        (False, (1,), None),
        (True, (True,), False),
        (True, None, False),
        (0, (), None),
        ("false", (), None),
        (None, (), None),
    ],
)
def test_only_a_verified_new_nonportfolio_run_can_be_written_as_shareable(use_portfolio, sources, expected):
    assert visibility_for_new_run(use_portfolio, source_visibility=sources) is expected


@pytest.mark.parametrize(
    ("run", "viewer_id", "owner_id", "expected"),
    [
        ({"is_shareable": True}, 2, 1, True),
        ({"is_shareable": False}, 2, 1, False),
        ({"is_shareable": None, "options": {"use_portfolio": False}}, 2, 1, False),
        ({"options": {"use_portfolio": False}}, 2, 1, False),
        ({"is_shareable": "true"}, 2, 1, False),
        ({"is_shareable": 1}, 2, 1, False),
        ({"is_shareable": False}, 1, 1, True),
        ({"is_shareable": None}, 1, 1, True),
        ({}, 1, 1, True),
        ({"is_shareable": None}, 1, None, False),
        ({"is_shareable": False}, 1, True, False),
        ({"is_shareable": True}, None, 1, False),
        ({"is_shareable": True}, True, 1, False),
        ({"is_shareable": True}, 0, 1, False),
        (None, 1, 1, False),
    ],
)
def test_visibility_uses_the_column_and_current_account(run, viewer_id, owner_id, expected):
    assert can_access_run(run, viewer_id=viewer_id, owner_id=owner_id) is expected


def _insert(conn, runs, run_id, options, **metadata):
    conn.execute(insert(runs).values(
        id=run_id, ticker="TEST", trade_date="2026-11-20", status="done", options=options,
        created_at=now(), **metadata,
    ))


def test_visibility_migration_preserves_legacy_rows_and_filters_lists_and_totals(db_url):
    config = alembic_config(db_url)
    command.upgrade(config, "0025")
    engine = make_engine(db_url)
    try:
        runs = Table("runs", MetaData(), autoload_with=engine)
        events = Table("events", MetaData(), autoload_with=engine)
        with engine.begin() as conn:
            _insert(conn, runs, "legacy-false", {"use_portfolio": False})
            _insert(conn, runs, "legacy-unmarked", {})
            conn.execute(insert(events).values(
                run_id="legacy-false", seq=1, ts=now(), type="status", content="done",
            ))

        command.upgrade(config, "0026")
        runs = Table("runs", MetaData(), autoload_with=engine)
        with engine.begin() as conn:
            assert dict(conn.execute(select(runs.c.id, runs.c.is_shareable)).all()) == {
                "legacy-false": None, "legacy-unmarked": None,
            }
            for run_id, flag, sources in [
                ("new-shareable", False, ()),
                ("new-account", True, (True,)),
                ("new-unknown", False, (None,)),
            ]:
                _insert(conn, runs, run_id, {"use_portfolio": flag},
                        is_shareable=visibility_for_new_run(flag, source_visibility=sources))
            source_ids = select(runs.c.id).where(shared_run_rows(runs.c.is_shareable))
            assert conn.scalars(source_ids).all() == ["new-shareable"]
            rows = conn.execute(select(runs)).mappings().all()
            for viewer_id, owner_id in [(2, 1), (1, 1), (None, 1), (1, None)]:
                predicate = visible_run_rows(runs.c.is_shareable, viewer_id=viewer_id, owner_id=owner_id)
                visible = sorted(r["id"] for r in rows if can_access_run(r, viewer_id=viewer_id, owner_id=owner_id))
                page = select(runs.c.id).where(predicate).order_by(runs.c.id).limit(1)
                count = select(func.count()).select_from(runs).where(predicate)
                assert conn.scalars(page).all() == visible[:1]
                assert conn.scalar(count) == len(visible)

        if engine.dialect.name == "sqlite":
            with pytest.raises(IntegrityError), engine.begin() as conn:
                conn.execute(text("UPDATE runs SET is_shareable = 2 WHERE id = 'new-account'"))

        command.downgrade(config, "0025")
        runs = Table("runs", MetaData(), autoload_with=engine)
        assert "is_shareable" not in runs.c
        with engine.connect() as conn:
            assert conn.scalar(select(func.count()).select_from(runs)) == 5
            assert conn.scalar(select(events.c.content).where(events.c.run_id == "legacy-false")) == "done"

        command.upgrade(config, "0026")
        runs = Table("runs", MetaData(), autoload_with=engine)
        with engine.connect() as conn:
            assert conn.scalars(select(runs.c.is_shareable)).all() == [None] * 5
    finally:
        engine.dispose()


def test_visibility_revision_emits_postgresql_ddl_without_connecting():
    config = alembic_config("postgresql+psycopg://fixture.invalid/fixture")
    config.output_buffer = StringIO()
    command.upgrade(config, "0025:0026", sql=True)
    upgrade_sql = config.output_buffer.getvalue()
    assert "ADD COLUMN is_shareable BOOLEAN" in upgrade_sql
    assert "CHECK (is_shareable IN (FALSE, TRUE))" in upgrade_sql
    assert "UPDATE runs" not in upgrade_sql

    config.output_buffer = StringIO()
    command.downgrade(config, "0026:0025", sql=True)
    assert "DROP COLUMN is_shareable" in config.output_buffer.getvalue()

    marker = column("is_shareable", Boolean())
    statement = select(marker).where(visible_run_rows(marker, viewer_id=2, owner_id=1))
    assert "WHERE is_shareable = true" in str(statement.compile(dialect=postgresql_dialect()))


def test_a_completed_run_stores_visibility_only_when_both_writers_establish_provenance(db_url):
    migrate(db_url)
    engine = make_engine(db_url)
    store = Store(engine)
    date = "2026-11-20"
    plan = {"rating": "Hold"}
    try:
        legacy = store.create_run("TEST", date, {"use_portfolio": False, "_run_visibility_writer": "1"})
        old_worker = store.create_run("TEST", date, {"use_portfolio": False}, guarded_writer=True)
        public = store.create_run("TEST", date, {"use_portfolio": False}, guarded_writer=True)
        account = store.create_run("TEST", date, {"use_portfolio": True}, guarded_writer=True)
        unknown = store.create_run("TEST", date, {"use_portfolio": False}, guarded_writer=True)
        mismatch = store.create_run("TEST", date, {"use_portfolio": True}, guarded_writer=True)
        failed = store.create_run("TEST", date, {"use_portfolio": False}, guarded_writer=True)

        # Old binaries update the result without writing the new column.
        with engine.begin() as conn:
            conn.execute(update(runs).where(runs.c.id == old_worker).values(
                status="done", plan=plan, finished_at=now(),
            ))
        store.mark_done(legacy, plan, None, executed_use_portfolio=False, source_visibility=())
        store.mark_done(public, plan, None, executed_use_portfolio=False, source_visibility=())
        store.mark_done(account, plan, None, executed_use_portfolio=True, source_visibility=())
        store.mark_done(unknown, plan, None, executed_use_portfolio=False, source_visibility=(None,))
        store.mark_done(mismatch, plan, None, executed_use_portfolio=False, source_visibility=())
        with pytest.raises(StatementError):
            store.mark_done(failed, {"invalid": object()}, None, executed_use_portfolio=False, source_visibility=())

        with engine.connect() as conn:
            result = {
                row.id: (row.status, row.plan, row.is_shareable)
                for row in conn.execute(select(runs.c.id, runs.c.status, runs.c.plan, runs.c.is_shareable))
            }
        assert result == {
            legacy: ("done", plan, None),
            old_worker: ("done", plan, None),
            public: ("done", plan, True),
            account: ("done", plan, False),
            unknown: ("done", plan, None),
            mismatch: ("done", plan, None),
            failed: ("queued", None, None),
        }
        assert store.get_run(public)["options"] == {"use_portfolio": False}
        assert "is_shareable" not in store.get_run(public)

        for run_id in (legacy, old_worker, public, account, unknown):
            store.append_event(run_id, {"type": "message", "agent": "market", "content": "synthetic"})
        with engine.connect() as conn:
            first = conn.scalar(select(func.min(runs.c.created_at)))
        reports = store.recent_reports("TEST", date, ["market"], first)
        assert [(report["run_id"], report["is_shareable"]) for report in reports] == [(public, True)]
    finally:
        engine.dispose()
