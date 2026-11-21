"""Role-specific startup uses the same schema head in SQLite and PostgreSQL."""

import traceback

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from desk import api, db
from desk.runner import make_fake_runner
from desk.settings import Settings


def _app(url, role):
    return api.create_app(Settings(database_url=url, role=role, fake_runs=True, macro_interval_sec=0),
                          runner=make_fake_runner(0))


@pytest.mark.parametrize("role", ["collector", "analyst"])
def test_a_worker_waits_for_api_to_prepare_the_database(db_url, role, monkeypatch):
    with pytest.raises(RuntimeError, match="DB 스키마"):
        _app(db_url, role)

    with TestClient(_app(db_url, "api")) as client:
        assert client.get("/api/health").status_code == 200

    def no_upgrade(_url):
        pytest.fail("worker must never run upgrade")

    monkeypatch.setattr(api, "migrate", no_upgrade)
    monkeypatch.setattr(command, "upgrade", lambda *_: pytest.fail("worker must never call Alembic upgrade"))
    with TestClient(_app(db_url, role)) as client:
        assert client.get("/api/health").status_code == 200


@pytest.mark.parametrize("role", ["all", "api"])
def test_api_and_all_still_upgrade_on_startup(db_url, role):
    with TestClient(_app(db_url, role)) as client:
        assert client.get("/api/health").status_code == 200
    engine = db.make_engine(db_url)
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    finally:
        engine.dispose()


def test_worker_start_does_not_create_a_missing_sqlite_file(tmp_path):
    path = tmp_path / "not-created" / "desk.db"
    with pytest.raises(RuntimeError, match="DB 스키마"):
        _app(f"sqlite:///{path.as_posix()}", "analyst")
    assert not path.parent.exists()


@pytest.mark.parametrize("state", ["behind", "ahead", "extra_head"])
def test_worker_rejects_mismatched_schema_without_changing_it(db_url, state):
    if state == "behind":
        command.upgrade(db.alembic_config(db_url), "0001")
    else:
        db.migrate(db_url)
    engine = db.make_engine(db_url)
    versions = text("SELECT version_num FROM alembic_version ORDER BY version_num")
    try:
        with engine.begin() as conn:
            if state == "ahead":
                conn.execute(text("UPDATE alembic_version SET version_num = 'future_test'"))
            elif state == "extra_head":
                conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('unexpected_test')"))
            previous = conn.execute(versions).scalars().all()
        with pytest.raises(RuntimeError, match="DB 스키마가 이미지와 일치하지"):
            _app(db_url, "collector")
        with engine.connect() as conn:
            assert conn.execute(versions).scalars().all() == previous
    finally:
        engine.dispose()


def test_connection_error_does_not_render_the_db_url_or_driver_message(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise OperationalError(None, None, RuntimeError("driver-detail-test"))

    monkeypatch.setattr(db, "create_engine", unavailable)
    fixture_url = "postgresql+psycopg://tester:password-test@db.example.test/fixture"
    with pytest.raises(RuntimeError, match="DB 스키마를 읽을 수 없습니다") as caught:
        db.require_schema_at_head(fixture_url)
    rendered = "".join(traceback.format_exception(caught.value))
    assert "password-test" not in rendered
    assert "driver-detail-test" not in rendered
