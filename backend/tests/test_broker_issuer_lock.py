import os
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine

from desk import broker_issuer_lock, run_queue


def test_broker_lock_has_its_own_key():
    assert broker_issuer_lock.LOCK_KEY != run_queue.LOCK_KEY


@pytest.fixture
def pg_mock(monkeypatch):
    engine = Mock()
    engine.dialect.name = "postgresql"
    conn = Mock()
    conn.execute.return_value.fetchone.return_value = (True,)
    connect = Mock(return_value=conn)
    monkeypatch.setattr(broker_issuer_lock.run_events, "_connect", connect)
    return broker_issuer_lock.BrokerIssuerLock(engine), conn, connect


def test_contended_lock_closes_candidate_connection(pg_mock):
    owner, conn, _ = pg_mock
    conn.execute.return_value.fetchone.return_value = (False,)
    assert not owner.acquire()
    assert not owner.check()
    conn.execute.assert_called_once_with("SELECT pg_try_advisory_lock(%s)", (broker_issuer_lock.LOCK_KEY,))
    conn.close.assert_called_once()


@pytest.mark.parametrize("fail_connect", [True, False])
def test_connection_or_acquisition_failure_disables_ownership(pg_mock, fail_connect):
    owner, conn, connect = pg_mock
    operation = connect if fail_connect else conn.execute
    operation.side_effect = OSError("connection failure")
    assert not owner.acquire()
    assert not owner.check()
    assert not owner.acquire()
    connect.assert_called_once()
    assert conn.close.call_count == (0 if fail_connect else 1)


def test_connection_loss_is_terminal_for_owner(pg_mock):
    owner, conn, connect = pg_mock
    assert owner.acquire()
    assert owner.check()
    conn.execute.side_effect = OSError("connection lost")
    assert not owner.check()
    assert not owner.acquire()
    owner.release()
    conn.close.assert_called_once()
    connect.assert_called_once()


def test_release_unlocks_and_closes_only_once(pg_mock):
    owner, conn, connect = pg_mock
    assert owner.acquire()
    assert owner.acquire()
    owner.release()
    owner.release()
    assert not owner.check() and not owner.acquire()
    assert conn.execute.call_args_list[-1].args == (
        "SELECT pg_advisory_unlock(%s)", (broker_issuer_lock.LOCK_KEY,),
    )
    assert conn.execute.call_count == 3
    conn.close.assert_called_once()
    connect.assert_called_once()


def test_sqlite_preserves_local_single_process_mode():
    with create_engine("sqlite://").connect() as conn:
        owner = broker_issuer_lock.BrokerIssuerLock(conn.engine)
        assert not owner.check()
        assert owner.acquire() and owner.check()
        owner.release()
        assert not owner.check() and not owner.acquire()


def test_postgresql_sessions_contend_and_release():
    url = os.getenv("DESK_TEST_DATABASE_URL")
    if not url:
        pytest.skip("PostgreSQL test database not configured")
    engine = create_engine(url)
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL test database required")
    first = broker_issuer_lock.BrokerIssuerLock(engine)
    second = broker_issuer_lock.BrokerIssuerLock(engine)
    try:
        assert first.acquire()
        assert not second.acquire()
        assert first.check()
        first.release()
        assert second.acquire() and second.check()
    finally:
        first.release()
        second.release()
        engine.dispose()


@pytest.mark.parametrize("role", ["all", "collector"])
def test_app_startup_denies_contending_minter(db_url, monkeypatch, role):
    from desk import api, db
    from tests.test_api import _client

    if role == "collector":
        db.migrate(db_url)  # the API already prepared the shared database
    owner = Mock()
    owner.acquire.return_value = False
    monkeypatch.setattr(api, "BrokerIssuerLock", Mock(return_value=owner))
    with pytest.raises(RuntimeError, match="broker issuer lock unavailable"), _client(db_url, role=role):
        pass
    owner.acquire.assert_called_once()
    owner.release.assert_called_once()


@pytest.mark.parametrize("role", ["all", "collector", "api", "analyst"])
def test_app_only_holds_issuer_lock_for_minter_lifespan(db_url, monkeypatch, role):
    from desk import api, db
    from tests.test_api import _client

    if role in ("collector", "analyst"):
        db.migrate(db_url)  # the API already prepared the shared database
    owner = Mock()
    owner.acquire.return_value = True
    factory = Mock(return_value=owner)
    monkeypatch.setattr(api, "BrokerIssuerLock", factory)
    with _client(db_url, role=role):
        assert owner.acquire.call_count == int(role in ("all", "collector"))
        owner.release.assert_not_called()
    assert owner.release.call_count == int(role in ("all", "collector"))
