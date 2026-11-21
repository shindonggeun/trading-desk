from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, update
from sqlalchemy.exc import SQLAlchemyError

from desk import cli
from desk.account_owner import AccountOwnerStore
from desk.db import account_owner_binding, make_engine, migrate, now, users

_FAILURE = "계좌 소유자 설정을 확인하지 못했습니다.\n"


@pytest.fixture
def owner_store(db_url, monkeypatch):
    migrate(db_url)
    engine = make_engine(db_url)
    try:
        with engine.begin() as conn:
            for user_id in (1, 2):
                conn.execute(users.insert().values(
                    id=user_id, username=f"synthetic-{user_id}", password_hash="unused",
                    created_at=now(), password_changed_at=now(),
                ))
        monkeypatch.setattr(cli, "load", lambda: SimpleNamespace(database_url=db_url))
        yield AccountOwnerStore(engine)
    finally:
        engine.dispose()


def _bind_args(generation, user_id="2"):
    return ["bind-account-owner", "--user-id", user_id, "--generation", generation, "--confirm"]


def test_cli_reports_vacancy_and_binds_only_the_explicit_existing_id(owner_store, capsys):
    vacant = owner_store.binding()
    assert vacant is not None and vacant.user_id is None
    assert cli.main(["account-owner-status"]) == 0
    assert capsys.readouterr().out == f"계좌 소유자 슬롯이 비어 있습니다. 세대 {vacant.generation}\n"
    assert cli.main(_bind_args(vacant.generation)) == 0
    assert capsys.readouterr().out == "계좌 소유자 바인딩을 저장했습니다.\n"
    bound = owner_store.binding()
    assert bound is not None and bound.user_id == 2 and bound.generation != vacant.generation
    assert cli.main(["account-owner-status"]) == 0
    assert capsys.readouterr().out == "계좌 소유자가 이미 연결되어 있습니다.\n"
    assert cli.main(_bind_args(bound.generation, "1")) == 1
    assert capsys.readouterr().err == _FAILURE
    assert owner_store.binding() == bound


def test_cli_requires_explicit_confirmation_and_does_not_echo_an_invalid_account(owner_store, capsys):
    vacant = owner_store.binding()
    assert vacant is not None
    with pytest.raises(SystemExit) as missing_confirmation:
        cli.main(_bind_args(vacant.generation)[:-1])
    assert missing_confirmation.value.code == 2
    assert "--confirm" in capsys.readouterr().err
    with pytest.raises(SystemExit) as invalid_account:
        cli.main(_bind_args(vacant.generation, "synthetic-private@example.invalid"))
    assert invalid_account.value.code == 2
    message = capsys.readouterr().err
    assert "계정 ID" in message and "synthetic-private" not in message
    assert owner_store.binding() == vacant


@pytest.mark.parametrize("change", ["deleted", "occupied", "stale"])
def test_cli_reports_the_same_error_when_the_slot_changes_before_the_write(owner_store, monkeypatch, capsys, change):
    vacant = owner_store.binding()
    assert vacant is not None
    original = AccountOwnerStore.bind_verified

    def change_after_read(store, *, verified_user_id, expected_vacancy):
        if change == "occupied":
            assert original(store, verified_user_id=1, expected_vacancy=expected_vacancy) is not None
        else:
            with store.engine.begin() as conn:
                if change == "deleted":
                    conn.execute(delete(users).where(users.c.id == verified_user_id))
                else:
                    conn.execute(update(account_owner_binding).values(generation=uuid4().hex))
        return original(store, verified_user_id=verified_user_id, expected_vacancy=expected_vacancy)

    monkeypatch.setattr(AccountOwnerStore, "bind_verified", change_after_read)
    assert cli.main(_bind_args(vacant.generation)) == 1
    output = capsys.readouterr()
    assert output.out == "" and output.err == _FAILURE
    current = owner_store.binding()
    assert current is not None and current.user_id == (1 if change == "occupied" else None)


def test_cli_contains_db_error_details(owner_store, monkeypatch, capsys, caplog):
    vacant = owner_store.binding()
    assert vacant is not None

    def fail(*_args, **_kwargs):
        raise SQLAlchemyError("synthetic-sensitive-connection-data")

    monkeypatch.setattr(AccountOwnerStore, "bind_verified", fail)
    assert cli.main(_bind_args(vacant.generation)) == 1
    output = capsys.readouterr()
    assert output.out == "" and output.err == _FAILURE
    assert "synthetic-sensitive-connection-data" not in caplog.text
    assert owner_store.binding() == vacant


def test_cli_does_not_create_or_migrate_a_missing_database(tmp_path, monkeypatch, capsys):
    database = tmp_path / "missing" / "desk.db"
    monkeypatch.setattr(cli, "load", lambda: SimpleNamespace(database_url=f"sqlite:///{database.as_posix()}"))
    assert cli.main(["account-owner-status"]) == 1
    assert capsys.readouterr().err == _FAILURE
    assert not database.parent.exists()
