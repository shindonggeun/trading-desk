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


@pytest.fixture(autouse=True)
def no_yahoo_network(monkeypatch):
    """Tests never reach Yahoo: a PriceSource without injected Yahoo functions fails over to
    these, so a real run's support/resistance lookup ends quickly instead of hitting the net."""
    from desk import prices, sr

    def offline(*_args):
        raise ConnectionError("no network in tests")

    monkeypatch.setattr(prices, "yahoo_quote", offline)
    monkeypatch.setattr(prices, "yahoo_candles", offline)
    sr._CACHE.clear()
