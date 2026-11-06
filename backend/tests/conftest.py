"""Every test gets an empty database: SQLite in tmp_path by default, or the
PostgreSQL in DESK_TEST_DATABASE_URL (CI) wiped to nothing first."""

import os

import pytest

# The world news-flow refresh (#115) would call GDELT / Google News from any app started with
# fake_runs=False; tests never do.
os.environ.setdefault("DESK_MACRO_INTERVAL_SEC", "0")
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
    from desk import related
    for name in ("yahoo_daily", "yahoo_news", "yahoo_holdings"):
        monkeypatch.setattr(related, name, offline)
    # nor an LLM: the leverage analyst (#82) is stubbed unless a test injects its own
    from desk import api
    monkeypatch.setattr(api, "llm_analyst", offline)
    monkeypatch.setattr(api, "llm_summariser", offline)   # nor the closing summary (#94)
    sr._CACHE.clear()
