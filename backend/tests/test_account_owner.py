from io import StringIO

import pytest
from alembic import command
from sqlalchemy import delete, func, inspect, select

from desk.account_owner import AccountOwnerStore
from desk.db import account_owner_binding, alembic_config, make_engine, migrate, now, users
from desk.run_access import can_access_run
from desk.store import Store


def _insert_user(conn, user_id):
    conn.execute(users.insert().values(
        id=user_id, username=f"synthetic-{user_id}", password_hash="unused",
        created_at=now(), password_changed_at=now(),
    ))


@pytest.fixture
def owner_store(db_url):
    migrate(db_url)
    engine = make_engine(db_url)
    try:
        with engine.begin() as conn:
            for user_id in (1, 2, 3):
                _insert_user(conn, user_id)
        yield AccountOwnerStore(engine)
    finally:
        engine.dispose()


def test_migration_leaves_an_existing_account_unbound_and_keeps_legacy_records(db_url):
    config = alembic_config(db_url)
    command.upgrade(config, "0026")
    engine = make_engine(db_url)
    try:
        with engine.begin() as conn:
            _insert_user(conn, 1)
            _insert_user(conn, 2)
        run_id = Store(engine).create_run("TEST", "2026-11-21", {"use_portfolio": False})
        Store(engine).append_event(run_id, {"type": "status", "content": "queued"})

        command.upgrade(config, "0027")
        store = AccountOwnerStore(engine)
        initial = store.binding()
        assert initial is not None and initial.user_id is None
        assert not store.is_current(user_id=1, generation=initial.generation)
        assert can_access_run({"is_shareable": True}, viewer_id=2, owner_id=initial.user_id)
        assert not can_access_run({"is_shareable": None}, viewer_id=1, owner_id=initial.user_id)

        command.downgrade(config, "0026")
        assert not inspect(engine).has_table("account_owner_binding")
        with engine.connect() as conn:
            assert conn.scalar(select(func.count()).select_from(users)) == 2
        assert Store(engine).get_run(run_id)["options"] == {"use_portfolio": False}
        assert Store(engine).events_after(run_id)[0]["content"] == "queued"

        command.upgrade(config, "0027")
        latest = store.binding()
        assert latest is not None and latest.user_id is None and latest.generation != initial.generation
    finally:
        engine.dispose()


def test_binding_is_explicit_and_an_occupied_slot_cannot_be_reassigned(owner_store):
    initial = owner_store.binding()
    assert initial is not None and initial.user_id is None
    bound = owner_store.bind_verified(verified_user_id=2, expected_vacancy=initial)
    assert bound is not None and bound.user_id == 2
    assert owner_store.is_current(user_id=2, generation=bound.generation)
    assert not owner_store.is_current(user_id=1, generation=bound.generation)
    assert not owner_store.is_current(user_id=2, generation=initial.generation)
    assert owner_store.bind_verified(verified_user_id=3, expected_vacancy=bound) is None
    assert owner_store.bind_verified(verified_user_id=3, expected_vacancy=initial) is None
    assert owner_store.binding() == bound


def test_deletion_and_id_reuse_do_not_rebind_but_verified_recovery_rotates_generation(owner_store):
    initial = owner_store.binding()
    assert initial is not None
    old = owner_store.bind_verified(verified_user_id=1, expected_vacancy=initial)
    assert old is not None
    with owner_store.engine.begin() as conn:
        conn.execute(delete(users).where(users.c.id == 1))
        _insert_user(conn, 1)
    vacant = owner_store.binding()
    assert vacant is not None and vacant.user_id is None
    for user_id in (1, 2, 3):
        assert not owner_store.is_current(user_id=user_id, generation=old.generation)

    assert owner_store.bind_verified(verified_user_id=1, expected_vacancy=initial) is None
    assert owner_store.bind_verified(verified_user_id=1, expected_vacancy=old) is None
    restored = owner_store.bind_verified(verified_user_id=1, expected_vacancy=vacant)
    assert restored is not None and restored.user_id == 1 and restored.generation != old.generation
    assert not owner_store.is_current(user_id=1, generation=old.generation)
    assert owner_store.is_current(user_id=1, generation=restored.generation)


@pytest.mark.parametrize("user_id", [True, 0, 999])
def test_invalid_or_missing_account_cannot_bind(owner_store, user_id):
    initial = owner_store.binding()
    assert initial is not None
    assert owner_store.bind_verified(verified_user_id=user_id, expected_vacancy=initial) is None
    assert owner_store.binding() == initial


def test_missing_binding_record_does_not_create_ownership(owner_store):
    initial = owner_store.binding()
    assert initial is not None
    with owner_store.engine.begin() as conn:
        conn.execute(delete(account_owner_binding))
    assert owner_store.binding() is None
    assert owner_store.bind_verified(verified_user_id=1, expected_vacancy=initial) is None
    assert not owner_store.is_current(user_id=1, generation=initial.generation)


def test_owner_binding_revision_emits_postgresql_ddl_without_connecting():
    config = alembic_config("postgresql+psycopg://fixture.invalid/fixture")
    config.output_buffer = StringIO()
    command.upgrade(config, "0026:0027", sql=True)
    upgrade = config.output_buffer.getvalue()
    assert "CREATE TABLE account_owner_binding" in upgrade
    assert "REFERENCES users (id) ON DELETE SET NULL" in upgrade
    assert "INSERT INTO account_owner_binding (id, user_id, generation) VALUES (1, NULL," in upgrade
    assert "SELECT" not in upgrade

    config.output_buffer = StringIO()
    command.downgrade(config, "0027:0026", sql=True)
    assert "DROP TABLE account_owner_binding" in config.output_buffer.getvalue()
