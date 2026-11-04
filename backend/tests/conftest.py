"""Every test gets an empty database: SQLite in tmp_path by default, or the
PostgreSQL in DESK_TEST_DATABASE_URL (CI) wiped to nothing first."""

import os

import pytest
from sqlalchemy import text

from desk.db import make_engine, metadata


@pytest.fixture
def db_url(tmp_path):
    url = os.getenv("DESK_TEST_DATABASE_URL")
    if not url:
        return f"sqlite:///{(tmp_path / 'desk.db').as_posix()}"
    engine = make_engine(url)
    with engine.begin() as conn:
        metadata.drop_all(conn)
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    engine.dispose()
    return url
