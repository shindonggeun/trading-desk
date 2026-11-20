"""Every test gets an empty database: SQLite in tmp_path by default, or the
PostgreSQL in DESK_TEST_DATABASE_URL (CI) wiped to nothing first."""

import os

import pytest

# The world news-flow refresh (#115) would call GDELT / Google News from any app started with
# fake_runs=False; tests never do.
os.environ.setdefault("DESK_MACRO_INTERVAL_SEC", "0")
os.environ.setdefault("DESK_REPORT_EVERY_DAYS", "0")      # #495: no automatic report in tests
os.environ.setdefault("DESK_MORNING_BRIEF_AT", "off")    # #571: no automatic morning brief in tests
# a developer's .env must not open a KIS websocket from the tests
os.environ["DESK_KIS_APP_KEY"] = ""
os.environ["DESK_KIS_APP_SECRET"] = ""
os.environ["DESK_KRX_API_KEY"] = ""                       # #601 nor call KRX with a real key
os.environ["DESK_REDIS_SENTINELS"] = ""                   # #689 nor share cache entries through a real Redis
os.environ["DESK_REDIS_URL"] = ""
from sqlalchemy import text

from desk.db import make_engine, metadata

# Tests that take seconds in CI (``--durations``, #748), with their rough cost. The shards place these first so
# no shard gets two; a test missing here only balances less well. Every other test counts as LIGHT.
SLOW = {
    "tests/test_day_call.py::test_the_backtest_holds_out_the_last_90_days_and_the_server_reads_the_same_numbers": 29,
    "tests/test_day_call.py::test_the_cli_writes_the_model_and_the_report": 24,
    "tests/test_channels.py::test_on_a_random_walk_each_line_reads_like_its_baseline": 13,
    "tests/test_day_call.py::test_a_call_from_the_committed_model_has_the_contract_shape": 10,
    "tests/test_day_call.py::test_the_held_out_sessions_choose_nothing": 9,
    "tests/test_risk_regime.py::test_the_backtest_gates_the_plans_and_compares_on_and_off": 5,
    "tests/test_earnings_yahoo_calendar.py::test_a_403_is_covered_by_yahoos_calendar_and_nasdaq_rests": 5,
    "tests/test_news_history.py::test_the_backtest_replays_the_macro_part_only_when_given_the_files": 3,
    "tests/test_day_calls.py::test_the_backfill_makes_only_the_sessions_after_training": 3,
}
LIGHT = 0.25


def pytest_collection_modifyitems(config, items):
    """``DESK_TEST_SHARD=i/n`` (CI matrix, #748): keep the i-th (1-based) of n shards. Greedy by cost - the SLOW
    tests first, then the rest in collected order - each to the least loaded shard. In the shard the SLOW tests
    come first, the rest in collected order: ``--dist load`` hands the first tests out one per worker, so a slow
    one starts at once instead of trailing the run. Deterministic, so every xdist worker collects the same set."""
    shard = os.getenv("DESK_TEST_SHARD")
    if not shard:
        return
    i, n = (int(x) for x in shard.split("/"))
    cost = [SLOW.get(item.nodeid, LIGHT) for item in items]
    loads = [0.0] * n
    mine = set()
    for k in sorted(range(len(items)), key=lambda k: -cost[k]):      # stable: equal costs keep their order
        s = min(range(n), key=lambda j: (loads[j], j))
        loads[s] += cost[k]
        if s == i - 1:
            mine.add(k)
    config.hook.pytest_deselected(items=[item for k, item in enumerate(items) if k not in mine])
    items[:] = [items[k] for k in sorted(mine, key=lambda k: (-cost[k], k))]


@pytest.fixture(scope="session")
def pg_url():
    """DESK_TEST_DATABASE_URL, or None. Under pytest-xdist (#748, CI ``-n auto``) each worker gets its own
    database ``<name>_<worker>`` next to that one, made once, so workers never wipe each other's tables."""
    url = os.getenv("DESK_TEST_DATABASE_URL")
    worker = os.getenv("PYTEST_XDIST_WORKER")
    if not url or not worker:
        return url
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    base = make_url(url)
    name = f"{base.database}_{worker}"
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        if conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}).scalar() is None:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    return base.set(database=name).render_as_string(hide_password=False)


@pytest.fixture
def db_url(tmp_path, pg_url):
    url = pg_url
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
    # nor SEC (#148) or the positioning sources (#154): a same-day run calls both, and a slow network
    # held it past _wait_done's few seconds (a flaky test_inverse_pair)
    from desk import filings, positioning

    class Offline:
        async def recent(self, *_args):
            raise ConnectionError("no network in tests")

        get = recent

    monkeypatch.setattr(filings, "FILINGS", Offline())
    monkeypatch.setattr(positioning, "POSITIONING", Offline())
    sr._CACHE.clear()


@pytest.fixture(autouse=True)
def earnings_cache_in_tmp(monkeypatch, tmp_path):
    """#308: an earnings pass saves its rows - into the test's folder, never the developer's
    ~/.tradingagents (which the server volume mirrors)."""
    from desk import earnings, macro_news

    monkeypatch.setattr(earnings, "CACHE_FILE", tmp_path / "earnings.json")
    monkeypatch.setattr(macro_news, "CACHE_FILE", tmp_path / "macro_news.json")   # #428: the macro snapshot too
    # #338: a Nasdaq failure asks Yahoo's calendar - never the network in a test (nothing stands in)
    monkeypatch.setattr(earnings, "_yahoo_calendar", lambda first, last: {})


@pytest.fixture(autouse=True)
def notify_channels_reset():
    """#580 desk.notify keeps the app's channels module-wide: never let one test's channels (a configured
    Telegram with real httpx) reach the next."""
    yield
    from desk import notify

    notify.install([], None)
