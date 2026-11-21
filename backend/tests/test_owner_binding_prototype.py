"""Synthetic database model of the explicit owner design. Never used by the app."""

from uuid import uuid4

import pytest
from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    delete,
    event,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError

from desk.run_access import can_access_run

_SINGLETON = 1


@pytest.fixture
def owner_db():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def enforce_foreign_keys(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    metadata = MetaData()
    users = Table("fixture_users", metadata, Column("id", Integer, primary_key=True))
    binding = Table(
        "fixture_owner_binding", metadata,
        Column("id", Integer, CheckConstraint("id = 1"), primary_key=True),
        Column("user_id", ForeignKey("fixture_users.id", ondelete="SET NULL")),
        Column("generation", String(32), nullable=False),
    )
    metadata.create_all(engine)
    try:
        with engine.begin() as conn:
            conn.execute(users.insert(), [{"id": 1}, {"id": 2}, {"id": 3}])
            conn.execute(binding.insert().values(id=_SINGLETON, generation=uuid4().hex))
            yield conn, users, binding
    finally:
        engine.dispose()


def _snapshot(conn, binding):
    return conn.execute(select(binding.c.user_id, binding.c.generation).where(binding.c.id == _SINGLETON)).one()


def _bind_verified(conn, binding, *, verified_user_id, expected_generation):
    """Model the database write after a separate authoritative owner check."""
    if type(verified_user_id) is not int or verified_user_id < 1:
        return False
    statement = update(binding).where(
        binding.c.id == _SINGLETON,
        binding.c.user_id.is_(None),
        binding.c.generation == expected_generation,
    ).values(user_id=verified_user_id, generation=uuid4().hex)
    return conn.execute(statement).rowcount == 1


def test_explicit_binding_retains_existing_owner_and_keeps_shared_runs_visible(owner_db):
    conn, _, binding = owner_db
    legacy = {"is_shareable": None}
    public = {"is_shareable": True}
    empty = _snapshot(conn, binding)
    assert empty.user_id is None
    assert not can_access_run(legacy, viewer_id=1, owner_id=empty.user_id)
    assert can_access_run(public, viewer_id=2, owner_id=empty.user_id)
    assert not _bind_verified(conn, binding, verified_user_id=None, expected_generation=empty.generation)
    assert _bind_verified(conn, binding, verified_user_id=1, expected_generation=empty.generation)
    current = _snapshot(conn, binding)
    assert current.user_id == 1
    assert can_access_run(legacy, viewer_id=1, owner_id=current.user_id)
    assert not can_access_run(legacy, viewer_id=2, owner_id=current.user_id)
    assert can_access_run(public, viewer_id=2, owner_id=current.user_id)
    assert not _bind_verified(conn, binding, verified_user_id=2, expected_generation=current.generation)


def test_deletion_never_promotes_a_remaining_or_reinserted_account(owner_db):
    conn, users, binding = owner_db
    assert _bind_verified(conn, binding, verified_user_id=1, expected_generation=_snapshot(conn, binding).generation)
    conn.execute(delete(users).where(users.c.id == 1))
    assert _snapshot(conn, binding).user_id is None
    assert not can_access_run({"is_shareable": False}, viewer_id=2, owner_id=_snapshot(conn, binding).user_id)

    conn.execute(users.insert().values(id=1))
    assert _snapshot(conn, binding).user_id is None
    assert not can_access_run({"is_shareable": None}, viewer_id=1, owner_id=_snapshot(conn, binding).user_id)
    assert can_access_run({"is_shareable": True}, viewer_id=1, owner_id=_snapshot(conn, binding).user_id)


def test_verified_recovery_rotates_generation_and_rejects_stale_approval(owner_db):
    conn, users, binding = owner_db
    initial = _snapshot(conn, binding)
    assert _bind_verified(conn, binding, verified_user_id=1, expected_generation=initial.generation)
    old = _snapshot(conn, binding)
    conn.execute(delete(users).where(users.c.id == 1))
    conn.execute(users.insert().values(id=1))
    suspended = _snapshot(conn, binding)
    assert suspended.user_id is None
    assert not _bind_verified(conn, binding, verified_user_id=1, expected_generation=initial.generation)
    assert _bind_verified(conn, binding, verified_user_id=1, expected_generation=suspended.generation)
    restored = _snapshot(conn, binding)
    assert restored.user_id == old.user_id
    assert restored.generation != old.generation
    owner_at_generation = select(binding.c.user_id).where(binding.c.user_id == 1)
    assert conn.scalar(owner_at_generation.where(binding.c.generation == old.generation)) is None
    assert conn.scalar(owner_at_generation.where(binding.c.generation == restored.generation)) == 1


def test_binding_a_missing_account_leaves_the_owner_unset(owner_db):
    conn, _, binding = owner_db
    empty = _snapshot(conn, binding)
    with pytest.raises(IntegrityError), conn.begin_nested():
        _bind_verified(conn, binding, verified_user_id=999, expected_generation=empty.generation)
    assert _snapshot(conn, binding) == empty
