"""#993 the sector ETFs' day's full read in the shared tier (desk.shared_cache versioned keys): a restart - a deploy
rotates every build key - reads only the last few bars on top of it, and anything off (another day, a re-adjusted
history, a value of another shape, Redis down) is today's cold read."""

import json
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import fakeredis
import numpy as np
import pytest

from desk import day_sectors as ds
from desk import shared_cache as sc
from desk.broker.base import Bar
from tests.test_day_sectors import _flat, _kst, _world

SECRET = "test-cache-key-0123456789abcdef"     # DESK_REDIS_CACHE_KEY: the same for every process
DAY = date(2026, 11, 23)
TICKERS = [ds.US.benchmark, ds.KR.benchmark, ds.US.sectors[0].ticker]


@pytest.fixture
def server():
    server = fakeredis.FakeServer()
    yield server
    sc.use(None, None)


@pytest.fixture(scope="module")
def world():
    return _world()


def _process(server, monkeypatch, build: str) -> None:
    """A fresh process on the same Redis and key: its own clients and stats, this build's fingerprint."""
    monkeypatch.setattr(sc, "BUILD", build)
    sc.use(fakeredis.FakeRedis(server=server, decode_responses=True),
           fakeredis.FakeAsyncRedis(server=server, decode_responses=True), SECRET)


def _source(bars_of, asked: list):
    def candles(t, interval, n):
        asked.append((t, n))
        return bars_of(t, n)
    return candles


def test_a_restart_builds_on_the_days_shared_full_read(server, world, monkeypatch, caplog):
    caplog.set_level("INFO", logger=ds.log.name)
    now = _kst(date(2026, 11, 18), 9)                     # the US 11/17 session is in: Korea's picks are ready
    tickers = set(ds.symbols(ds.KR))
    first_asked, second_asked = [], []

    _process(server, monkeypatch, "build-a")
    first = ds.Cache().read("kr", _source(lambda t, n: world[t][-n:], first_asked), now)
    assert first["ready"] and {n for _, n in first_asked} == {ds.HISTORY_BARS} and len(first_asked) == len(tickers)
    assert sc.stats()["writes"] == len(tickers) and sc.stats()["skipped"] == 0   # every value round-trips JSON

    _process(server, monkeypatch, "build-b")              # a deploy: every build key rotated
    cache = ds.Cache()
    second = cache.read("kr", _source(lambda t, n: world[t][-n:], second_asked), now)
    assert {n for _, n in second_asked} == {ds.RECENT_BARS} and len(second_asked) == len(tickers)   # no full read
    assert second == first
    assert sc.stats()["hits"] == len(tickers) and sc.stats()["writes"] == 0   # a merge is not written again
    rebuild = [m for m in caplog.messages if m.startswith("day sectors: rebuild")][-1]
    assert "full_calls=0 " in rebuild and f"recent_calls={len(tickers)} " in rebuild
    assert rebuild.endswith(f"restored={len(tickers)}")

    second_asked.clear()
    cache.read("us", _source(lambda t, n: world[t][-n:], second_asked), now)
    assert second_asked == []                             # the other market's build in the pass: the kept bars


def test_another_kst_day_reads_the_full_history(server, monkeypatch):
    _process(server, monkeypatch, "build-a")
    ds.Cache()._read_bars(TICKERS, _source(lambda t, n: _flat(n, date(2026, 11, 20)), []), _kst(DAY, 23, 50))
    _process(server, monkeypatch, "build-b")
    asked = []
    _, complete, reads = ds.Cache()._read_bars(
        TICKERS, _source(lambda t, n: _flat(n, date(2026, 11, 23)), asked), _kst(DAY + timedelta(days=1), 0, 10))
    assert sorted(asked) == sorted((t, ds.HISTORY_BARS) for t in TICKERS) and complete
    assert not any(r.restored for r in reads) and sc.stats()["writes"] == len(TICKERS)   # the new day's read


def test_a_readjusted_history_is_read_again_and_shared_again(server, monkeypatch):
    """Yahoo re-adjusted the closes since the shared read (a distribution): laid over it, a fake move."""
    old = _flat(30, date(2026, 11, 20))
    new = _flat(30, date(2026, 11, 20), close=97.0)
    _process(server, monkeypatch, "build-a")
    ds.Cache()._read_bars(TICKERS, _source(lambda t, n: old[-n:], []), _kst(DAY, 9))
    _process(server, monkeypatch, "build-b")
    asked = []
    bars, complete, _ = ds.Cache()._read_bars(TICKERS, _source(lambda t, n: new[-n:], asked), _kst(DAY, 10))
    assert sorted(asked) == sorted([(t, ds.RECENT_BARS) for t in TICKERS] + [(t, ds.HISTORY_BARS) for t in TICKERS])
    assert all(bars[t] == new for t in TICKERS) and complete
    _process(server, monkeypatch, "build-c")              # the next process starts from the re-adjusted read
    asked.clear()
    bars, _, _ = ds.Cache()._read_bars(TICKERS, _source(lambda t, n: new[-n:], asked), _kst(DAY, 11))
    assert {n for _, n in asked} == {ds.RECENT_BARS} and all([b.close for b in bars[t]] == [97.0] * 30
                                                             for t in TICKERS)


def test_an_empty_recent_read_on_shared_bars_reads_all_as_a_cold_start(server, monkeypatch):
    old = _flat(30, date(2026, 11, 20))
    _process(server, monkeypatch, "build-a")
    ds.Cache()._read_bars(TICKERS, _source(lambda t, n: old[-n:], []), _kst(DAY, 9))
    _process(server, monkeypatch, "build-b")
    asked = []
    cache = ds.Cache()
    bars, complete, _ = cache._read_bars(
        TICKERS, _source(lambda t, n: old[-n:] if n == ds.HISTORY_BARS else [], asked), _kst(DAY, 10))
    assert sorted(asked) == sorted([(t, ds.RECENT_BARS) for t in TICKERS] + [(t, ds.HISTORY_BARS) for t in TICKERS])
    assert complete and all(bars[t] == old for t in TICKERS)

    _process(server, monkeypatch, "build-c")              # both empty: the shared bars, asked again soon
    bars, complete, reads = ds.Cache()._read_bars(TICKERS, _source(lambda t, n: [], []), _kst(DAY, 11))
    assert not complete and {r.status for r in reads} == {"empty"}
    assert all([b.close for b in bars[t]] == [b.close for b in old] for t in TICKERS)   # a cold start: none


def test_a_shared_read_that_fails_to_refresh_keeps_the_shared_bars_without_counting_them_fresh(server, monkeypatch):
    old = _flat(30, date(2026, 11, 20))
    _process(server, monkeypatch, "build-a")
    ds.Cache()._read_bars(TICKERS, _source(lambda t, n: old[-n:], []), _kst(DAY, 9))
    _process(server, monkeypatch, "build-b")

    def down(t, interval, n):
        raise TimeoutError()

    cache = ds.Cache()
    bars, complete, reads = cache._read_bars(TICKERS, down, _kst(DAY, 10))
    assert not complete and {r.status for r in reads} == {"error"} and set(bars) == set(TICKERS)
    assert cache._bars == {}                              # not this process's own read: never a "fresh" hit


def test_a_down_redis_leaves_the_cold_read_as_it_was(monkeypatch):
    calls = []

    class Down:
        def pipeline(self, transaction=False):
            calls.append("get")
            raise ConnectionError("refused")

        def set(self, *args, **kwargs):
            calls.append("set")
            raise ConnectionError("refused")

    tickers = list(dict.fromkeys(ds.symbols(ds.KR)))
    plain = []
    ds.Cache()._read_bars(tickers, _source(lambda t, n: _flat(min(n, 30), date(2026, 11, 20)), plain), _kst(DAY, 9))
    sc.use(Down(), None, SECRET)
    try:
        asked = []
        started = time.monotonic()
        bars, complete, _ = ds.Cache()._read_bars(
            tickers, _source(lambda t, n: _flat(min(n, 30), date(2026, 11, 20)), asked), _kst(DAY, 9))
        assert time.monotonic() - started < 1 and complete and set(bars) == set(tickers)
        assert sorted(asked) == sorted(plain)             # every ETF's full read, as with no shared tier
        # one failed read marks it down (the ETFs read side by side may each have asked first); no write tries
        assert 1 <= sc.stats()["errors"] <= ds.FETCH_PARALLEL and calls == ["get"] * sc.stats()["errors"]
    finally:
        sc.use(None, None)


def _yahoo_like(n: int) -> list:
    """n daily bars as yfinance gives them (bar times in UTC to the second, closes with float noise)."""
    rng = np.random.default_rng(3)
    closes = 100 * np.cumprod(1 + rng.normal(0, 0.012, n))
    first = date(2017, 1, 3)
    return [Bar(datetime.combine(first + timedelta(days=i), datetime.min.time(), UTC).replace(hour=5)
                .isoformat(timespec="seconds"), c, c, c, c, 0.0) for i, c in enumerate(closes.tolist())]


def test_the_shared_value_round_trips_and_fits(server, monkeypatch):
    bars = _yahoo_like(ds.HISTORY_BARS)
    value = ds.shared_value(DAY, bars)
    text = json.dumps(value, separators=(",", ":"))
    assert json.loads(text) == value and len(text) < sc.MAX_VALUE_BYTES // 3   # ~110 KB of the 512 KB cap
    back = ds.shared_bars(json.loads(text), DAY)
    assert [(b.at, b.close) for b in back] == [(b.at, b.close) for b in bars]
    assert ds._merge(back, bars[-ds.RECENT_BARS:]) == back[:-ds.RECENT_BARS] + bars[-ds.RECENT_BARS:]
    _process(server, monkeypatch, "build-a")
    ds.Cache()._share("SPY", DAY, bars)
    assert sc.stats()["writes"] == 1 and sc.stats()["skipped"] == 0
    assert sc.get_versioned(ds.SHARED_BARS, ds.SHARED_BARS_VERSION, "SPY")[0] == value


def test_a_full_read_too_big_to_share_stays_here_with_one_line(server, monkeypatch, caplog):
    caplog.set_level("INFO", logger=ds.log.name)
    _process(server, monkeypatch, "build-a")
    monkeypatch.setattr(sc, "MAX_VALUE_BYTES", 2_000)
    cache = ds.Cache()
    bars, _, _ = cache._read_bars(TICKERS[:1], _source(lambda t, n: _flat(min(n, 60), date(2026, 11, 20)), []),
                                  _kst(DAY, 9))
    cache._share(TICKERS[0], DAY, bars[TICKERS[0]])
    assert sc.stats()["writes"] == 0 and sc.stats()["skipped"] == 0
    assert sum("not shared" in m for m in caplog.messages) == 1


@pytest.mark.parametrize("close", [Decimal("100.5"), float("nan")])
def test_a_full_read_json_cannot_hold_is_kept_here_and_the_build_goes_on(server, monkeypatch, close):
    _process(server, monkeypatch, "build-a")
    odd = [Bar(b.at, b.open, b.high, b.low, close if i == 29 else b.close, 0.0)
           for i, b in enumerate(_flat(30, date(2026, 11, 20)))]
    bars, complete, _ = ds.Cache()._read_bars(TICKERS[:1], lambda t, i, n: odd, _kst(DAY, 9))
    assert complete and bars[TICKERS[0]] is odd and sc.stats()["writes"] == 0


@pytest.mark.parametrize("value", [
    None, [], "x", {"day": "2026-11-22", "at": ["2026-11-20T05:00:00+00:00"], "close": [1.0]},   # another day
    {"day": "2026-11-23", "at": [], "close": []},
    {"day": "2026-11-23", "at": ["2026-11-20T05:00:00+00:00"], "close": [1.0, 2.0]},
    {"day": "2026-11-23", "at": ["2026-11-20T05:00:00+00:00", "2026-11-19T05:00:00+00:00"], "close": [1.0, 2.0]},
    {"day": "2026-11-23", "at": ["2026-11-20T05:00:00+00:00"], "close": ["1.0"]},
    {"day": "2026-11-23", "at": ["2026-11-20T05:00:00+00:00"], "close": [True]},
    {"day": "2026-11-23", "at": ["not a time"], "close": [1.0]},
    {"day": "2026-11-23", "at": [20261120], "close": [1.0]},
    {"day": "2026-11-23", "at": ["2026-11-20T05:00:00+00:00"], "close": [10 ** 400]},
    {"day": "2026-11-23", "close": [1.0]},
])
def test_a_shared_value_of_another_shape_is_a_miss(value):
    assert ds.shared_bars(value, DAY) is None


def test_a_shared_value_of_another_shape_reads_the_full_history(server, monkeypatch):
    _process(server, monkeypatch, "build-a")
    for t in TICKERS:
        sc.put_versioned(ds.SHARED_BARS, ds.SHARED_BARS_VERSION, t, {"day": DAY.isoformat(), "bars": [1, 2]}, 600)
    asked = []
    _, complete, reads = ds.Cache()._read_bars(
        TICKERS, _source(lambda t, n: _flat(min(n, 30), date(2026, 11, 20)), asked), _kst(DAY, 9))
    assert sorted(asked) == sorted((t, ds.HISTORY_BARS) for t in TICKERS) and complete
    assert sc.stats()["hits"] == len(TICKERS) and not any(r.restored for r in reads)
