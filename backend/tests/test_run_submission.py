from io import StringIO

import pytest
from alembic import command
from sqlalchemy import MetaData, Table, delete, event, func, insert, inspect, select
from sqlalchemy.exc import IntegrityError

from desk.account_owner import AccountOwnerStore
from desk.broker.mock import MockBroker
from desk.db import alembic_config, events, make_engine, migrate, now, run_submissions, runs, users
from desk.runner import Hub, RunManager, RunRequest, make_fake_runner
from desk.store import Store

_QUEUED = {"type": "status", "agent": "system", "content": "queued"}
_DATE = "2026-11-21"


def _insert_user(conn, user_id):
    conn.execute(users.insert().values(
        id=user_id, username=f"synthetic-{user_id}", password_hash="unused",
        created_at=now(), password_changed_at=now(),
    ))


def test_migration_chain_preserves_legacy_and_new_runs_without_changing_the_owner_binding(db_url):
    config = alembic_config(db_url)
    command.upgrade(config, "0026")
    engine = make_engine(db_url)
    try:
        legacy = Table("runs", MetaData(), autoload_with=engine)
        old_visibility = {"legacy-unclassified": None, "legacy-shared": True}
        with engine.begin() as conn:
            _insert_user(conn, 2)
            for run_id, visibility in old_visibility.items():
                conn.execute(insert(legacy).values(
                    id=run_id, ticker="TEST", trade_date=_DATE, status="done",
                    options={"use_portfolio": False}, created_at=now(), is_shareable=visibility,
                ))
                conn.execute(events.insert().values(run_id=run_id, seq=1, ts=now(), **_QUEUED))

        command.upgrade(config, "0027")
        assert not inspect(engine).has_table("run_submissions")
        owners = AccountOwnerStore(engine)
        vacant = owners.binding()
        assert vacant is not None and vacant.user_id is None
        bound = owners.bind_verified(verified_user_id=2, expected_vacancy=vacant)
        assert bound is not None

        command.upgrade(config, "0028")
        assert owners.binding() == bound
        with engine.connect() as conn:
            assert dict(conn.execute(select(runs.c.id, runs.c.is_shareable)).all()) == old_visibility
            assert conn.scalar(select(func.count()).select_from(run_submissions)) == 0
        store = Store(engine)
        submitted = store.create_run(
            "TEST", _DATE, {"use_portfolio": False}, first_event=_QUEUED,
            guarded_writer=True, authenticated_submitter_id=2,
        )
        with engine.connect() as conn:
            proof = conn.execute(select(
                run_submissions.c.submitted_by_user_id, run_submissions.c.non_account_submission,
            ).where(run_submissions.c.run_id == submitted)).one()
            assert tuple(proof) == (2, True)

        command.upgrade(config, "0029")
        with engine.connect() as conn:
            assert conn.execute(select(run_submissions.c.owner_generation).where(
                run_submissions.c.run_id == submitted,
            )).one() == (None,)
        account = store.create_run(
            "TEST", _DATE, {"use_portfolio": True}, first_event=_QUEUED,
            guarded_writer=True, authenticated_submitter_id=2, owner_generation=bound.generation,
        )
        with engine.connect() as conn:
            proof = conn.execute(select(run_submissions).where(run_submissions.c.run_id == account)).one()
            assert proof.non_account_submission is False and proof.owner_generation == bound.generation

        command.downgrade(config, "0028")
        assert "owner_generation" not in {column["name"] for column in inspect(engine).get_columns("run_submissions")}
        with engine.connect() as conn:
            retained = conn.execute(select(run_submissions.c.run_id, run_submissions.c.non_account_submission)).all()
            assert dict(retained) == {
                submitted: True, account: False,
            }
        command.downgrade(config, "0027")
        assert not inspect(engine).has_table("run_submissions")
        assert owners.binding() == bound
        with engine.connect() as conn:
            assert dict(conn.execute(select(runs.c.id, runs.c.is_shareable)).all()) == {
                **old_visibility, submitted: None, account: None,
            }
        for run_id in (*old_visibility, submitted, account):
            assert [row["content"] for row in store.events_after(run_id)] == ["queued"]
    finally:
        engine.dispose()


def test_only_server_bound_submissions_record_proof_and_deletion_severs_the_link(db_url):
    migrate(db_url)
    engine = make_engine(db_url)
    store = Store(engine)
    manager = RunManager(store, Hub(), make_fake_runner(0), MockBroker(), max_concurrent=1, direct=False)
    try:
        with engine.begin() as conn:
            _insert_user(conn, 2)
        non_account = manager.submit(RunRequest("TEST", _DATE, use_portfolio=False), authenticated_submitter_id=2)
        account = manager.submit(RunRequest("TEST", _DATE, use_portfolio=True), authenticated_submitter_id=2)
        unverified = store.create_run(
            "TEST", _DATE, {"use_portfolio": False, "submitted_by_user_id": 2, "non_account_submission": True},
            first_event=_QUEUED, guarded_writer=True,
        )
        with engine.connect() as conn:
            statement = select(
                runs.c.id, run_submissions.c.submitted_by_user_id, run_submissions.c.non_account_submission,
                runs.c.is_shareable,
            ).select_from(runs.outerjoin(run_submissions))
            rows = {
                row.id: (row.submitted_by_user_id, row.non_account_submission, row.is_shareable)
                for row in conn.execute(statement)
            }
        assert rows == {non_account: (2, True, None), account: (2, False, None), unverified: (None, None, None)}
        for run_id in rows:
            assert [event["content"] for event in store.events_after(run_id)] == ["queued"]
        assert "submitted_by_user_id" not in store.get_run(non_account)
        assert "non_account_submission" not in store.get_run(non_account)

        with engine.begin() as conn:
            conn.execute(delete(users).where(users.c.id == 2))
            _insert_user(conn, 2)
            after = conn.execute(select(run_submissions.c.submitted_by_user_id, run_submissions.c.non_account_submission)
                                 .where(run_submissions.c.run_id == non_account)).one()
            assert tuple(after) == (None, True)
            assert conn.scalar(select(func.count()).select_from(events)) == 3
            conn.execute(delete(runs).where(runs.c.id == account))
            assert conn.scalars(select(run_submissions.c.run_id)).all() == [non_account]
            assert conn.scalar(select(func.count()).select_from(events)) == 2
    finally:
        engine.dispose()


def test_queued_row_and_event_roll_back_together_on_insert_failure(db_url):
    migrate(db_url)
    engine = make_engine(db_url)
    store = Store(engine)
    try:
        with engine.begin() as conn:
            _insert_user(conn, 2)

        def abort_after_event(_conn, _cursor, statement, *_args):
            if statement.startswith("INSERT INTO events "):
                raise RuntimeError("synthetic failure after queued event write")

        event.listen(engine, "after_cursor_execute", abort_after_event)
        try:
            with pytest.raises(RuntimeError, match="synthetic failure after queued event write"):
                store.create_run(
                    "TEST", _DATE, {"use_portfolio": False}, first_event=_QUEUED,
                    guarded_writer=True, authenticated_submitter_id=2,
                )
        finally:
            event.remove(engine, "after_cursor_execute", abort_after_event)
        with engine.connect() as conn:
            assert conn.scalar(select(func.count()).select_from(runs)) == 0
            assert conn.scalar(select(func.count()).select_from(events)) == 0
            assert conn.scalar(select(func.count()).select_from(run_submissions)) == 0
    finally:
        engine.dispose()


@pytest.mark.parametrize(("submitter", "portfolio", "first_event", "guarded"), [
    (True, False, _QUEUED, True),
    (2, "false", _QUEUED, True),
    (2, False, None, True),
    (2, False, {"type": "status", "agent": "trader", "content": "queued"}, True),
    (2, False, _QUEUED, False),
])
def test_invalid_submission_proof_is_rejected_before_writing(db_url, submitter, portfolio, first_event, guarded):
    engine = make_engine(db_url)
    try:
        # No schema here: validation happens before attempting the first insert.
        with pytest.raises(ValueError, match="submission provenance requires an authenticated queued run"):
            Store(engine).create_run(
                "TEST", _DATE, {"use_portfolio": portfolio}, first_event=first_event,
                guarded_writer=guarded, authenticated_submitter_id=submitter,
            )
    finally:
        engine.dispose()


def test_referencing_an_unknown_submitter_rejects_the_submission(db_url):
    migrate(db_url)
    engine = make_engine(db_url)
    try:
        with pytest.raises(IntegrityError):
            Store(engine).create_run(
                "TEST", _DATE, {"use_portfolio": False}, first_event=_QUEUED,
                guarded_writer=True, authenticated_submitter_id=99,
            )
        with engine.connect() as conn:
            assert conn.scalar(select(func.count()).select_from(runs)) == 0
            assert conn.scalar(select(func.count()).select_from(events)) == 0
            assert conn.scalar(select(func.count()).select_from(run_submissions)) == 0
    finally:
        engine.dispose()


def test_submission_revision_emits_postgresql_ddl_in_order_without_connecting():
    config = alembic_config("postgresql+psycopg://fixture.invalid/fixture")
    config.output_buffer = StringIO()
    command.upgrade(config, "0026:0029", sql=True)
    upgrade = config.output_buffer.getvalue()
    assert upgrade.index("CREATE TABLE account_owner_binding") < upgrade.index("CREATE TABLE run_submissions")
    assert "CREATE TABLE run_submissions" in upgrade
    assert "submitted_by_user_id INTEGER" in upgrade
    assert "REFERENCES users (id) ON DELETE SET NULL" in upgrade
    assert "non_account_submission BOOLEAN" in upgrade
    assert "REFERENCES runs (id) ON DELETE CASCADE" in upgrade
    marker = "ALTER TABLE run_submissions ADD COLUMN owner_generation VARCHAR(32)"
    assert upgrade.index("CREATE TABLE run_submissions") < upgrade.index(marker)
    assert "UPDATE runs" not in upgrade and "UPDATE run_submissions" not in upgrade

    config.output_buffer = StringIO()
    command.downgrade(config, "0029:0028", sql=True)
    assert "ALTER TABLE run_submissions DROP COLUMN owner_generation" in config.output_buffer.getvalue()

    config.output_buffer = StringIO()
    command.downgrade(config, "0028:0027", sql=True)
    downgrade = config.output_buffer.getvalue()
    assert "DROP TABLE run_submissions" in downgrade
    assert "DROP TABLE account_owner_binding" not in downgrade
