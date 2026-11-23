from __future__ import annotations

import asyncio
from datetime import UTC, datetime, time, timedelta
from threading import Event

from desk import market_calendar
from desk.market_outlook import INDEX, VIX, MarketOutlook, Observation

NOW = datetime(2026, 11, 23, 17, tzinfo=UTC)  # 월요일 뉴욕 장중. 마지막 마감 봉은 금요일이다.
LAST_CLOSE = datetime(2026, 11, 20, 21, tzinfo=UTC)
VIX_CLOSE = LAST_CLOSE + timedelta(minutes=15)
COUNTS = 80
DOWN = [150 - n * .8 for n in range(COUNTS)]
REBOUND = DOWN[:-5] + [DOWN[-6] + n * 1.05 for n in range(1, 6)]
RECOVERY = DOWN[:-25] + [DOWN[-26] + n * 2.2 for n in range(1, 26)]
VIX_UP = [21, 22, 22, 24, 26, 27]
VIX_DOWN = [31, 30, 28, 25, 25, 23]


def _bars(values, last=LAST_CLOSE):
    """Prices.candles의 UTC에 담긴 현지 자정 일봉을 흉내 내며 휴일·주말은 제외한다."""
    day = market_calendar.local_today("us", last)
    tail = []
    for close in reversed(values):
        stamp = datetime.combine(day, time.min, market_calendar.ET).astimezone(UTC)
        tail.append({"at": stamp.isoformat(), "close": close})
        day = market_calendar.previous_open("us", day)
    return tail[::-1]


def _candles(index, vix, **snapshots):
    async def read(symbol, interval, count):
        assert interval == "1d"
        assert symbol in (INDEX, VIX)
        values = index if symbol == INDEX else vix
        snapshot = {"source": "synthetic_yahoo", "fetched_at": NOW.isoformat(), "bars": _bars(values),
                    **snapshots.get(symbol, {})}
        return {**snapshot, "bars": snapshot["bars"][-count:]}
    return read


def _measure(value, source="fixture", observed=LAST_CLOSE, direction="positive"):
    async def read():
        return Observation(value, source, observed, direction, observed + timedelta(minutes=10))
    return read


def _read(candles=None, **kwargs):
    return asyncio.run(MarketOutlook(candles=candles, clock=lambda: NOW, **kwargs).read())


def _evidence(response):
    return {row["id"]: row for row in response["regime"]["evidence"]}


def test_downtrend_uses_closed_index_and_volatility_with_provenance():
    result = _read(_candles(DOWN, VIX_UP))
    regime = result["regime"]
    evidence = _evidence(result)
    assert result["market"] == "us"
    assert result["as_of"] == NOW.isoformat()
    assert regime["state"] == "downtrend"
    assert "index_trend" in regime["supporting"]
    assert "volatility" in regime["supporting"]
    assert regime["next_checks"]
    assert evidence["index_trend"]["observed_at"] == LAST_CLOSE.isoformat()
    assert evidence["index_trend"]["fetched_at"] == NOW.isoformat()
    assert evidence["index_trend"]["source"] == "synthetic_yahoo"
    assert evidence["index_trend"]["age_days"] == 3  # 주말을 지나도 마지막 완료 세션이므로 최신이다.
    assert evidence["volatility"]["observed_at"] == VIX_CLOSE.isoformat()
    assert evidence["index_drawdown"]["value"] < -5
    assert evidence["breadth"]["status"] == evidence["credit_spread"]["status"] == "disabled"
    assert evidence["breadth"]["value"] is None


def test_short_rebound_does_not_confirm_broader_recovery():
    result = _read(_candles(REBOUND, VIX_DOWN))
    regime = result["regime"]
    assert regime["state"] == "rebound_candidate"
    assert regime["supporting"] == ["index_rebound", "volatility"]
    assert "index_drawdown" in regime["opposing"]


def test_wider_recovery_requires_recent_pullback_and_independent_confirmation():
    breadth = _measure(64, direction="positive")
    recovery = _read(_candles(RECOVERY, VIX_DOWN), breadth=breadth)
    assert recovery["regime"]["state"] == "recovery_watch"
    assert "breadth" in recovery["regime"]["supporting"]
    assert _evidence(recovery)["breadth"]["unit"] == "%"
    assert _read(_candles(RECOVERY, VIX_DOWN))["regime"]["state"] == "undetermined"
    renewed_drop = RECOVERY[:-1] + [RECOVERY[-1] - 18]
    result = _read(_candles(renewed_drop, VIX_DOWN), breadth=breadth)
    assert _evidence(result)["index_trend"]["direction"] == "positive"
    assert _evidence(result)["index_rebound"]["direction"] == "negative"
    assert result["regime"]["state"] == "undetermined"
    ordinary_rise = [100 + n for n in range(COUNTS)]
    assert _read(_candles(ordinary_rise, VIX_DOWN), breadth=breadth)["regime"]["state"] == "undetermined"


def test_conflicting_or_old_measurements_do_not_confirm_recovery():
    breadth = _measure(62, "breadth_fixture")
    credit = _measure(345, "credit_fixture", direction="negative")
    result = _read(_candles(RECOVERY, VIX_DOWN), breadth=breadth, credit=credit)
    assert result["regime"]["state"] == "undetermined"
    assert "breadth" in result["regime"]["supporting"]
    assert "credit_spread" in result["regime"]["opposing"]
    assert _evidence(result)["credit_spread"]["value"] == 345

    old = _read(_candles(RECOVERY, VIX_DOWN), breadth=_measure(67, observed=LAST_CLOSE - timedelta(days=6)))
    assert old["regime"]["state"] == "undetermined"
    assert "breadth" not in old["regime"]["supporting"]
    evidence = _evidence(old)
    assert evidence["breadth"]["status"] == "stale"
    assert evidence["breadth"]["reason"] == "publication_lag"
    assert evidence["breadth"]["value"] == 67


def test_unfinished_and_future_bars_do_not_replace_last_confirmed_session():
    future = NOW + timedelta(days=1)
    augmented = _bars(DOWN) + _bars([10_000], NOW) + _bars([20_000], future)
    vix_bars = _bars(VIX_UP) + _bars([99], NOW)
    result = _read(_candles(DOWN, VIX_UP, **{INDEX: {"bars": augmented}, VIX: {"bars": vix_bars}}))
    assert result["regime"]["state"] == "downtrend"
    evidence = _evidence(result)
    assert evidence["index_trend"]["details"]["close"] == round(DOWN[-1], 2)
    assert evidence["index_trend"]["observed_at"] == LAST_CLOSE.isoformat()
    assert evidence["volatility"]["value"] == 27
    assert evidence["volatility"]["details"]["change5"] == 6
    assert evidence["volatility"]["observed_at"] == VIX_CLOSE.isoformat()


def test_cached_intraday_sample_stays_unfinished_after_close():
    after_close = NOW + timedelta(hours=6)
    augmented = _bars(DOWN) + _bars([10_000], NOW)
    candles = _candles(DOWN, VIX_UP, **{INDEX: {"bars": augmented, "fetched_at": NOW.isoformat()}})
    result = asyncio.run(MarketOutlook(candles=candles, clock=lambda: after_close).read())
    assert result["regime"]["state"] == "undetermined"
    evidence = _evidence(result)["index_trend"]
    assert evidence["status"] == "stale"
    assert evidence["reason"] == "latest_session_missing"
    assert evidence["observed_at"] == LAST_CLOSE.isoformat()
    assert evidence["details"]["close"] == round(DOWN[-1], 2)


def test_partial_history_and_source_failures_do_not_gain_evidence():
    async def candles(symbol, _interval, _count):
        if symbol == VIX:
            raise OSError("upstream secret")
        return {"source": "fixture", "bars": _bars(DOWN[-11:]) + _bars([float("nan")], NOW)}

    result = _read(candles, breadth=_measure(60, observed=NOW + timedelta(minutes=1)))
    assert result["regime"]["state"] == "undetermined"
    assert "upstream secret" not in repr(result)
    evidence = _evidence(result)
    assert evidence["volatility"]["status"] == "error"
    assert evidence["index_trend"]["reason"] == "insufficient_history"
    assert evidence["index_rebound"]["status"] == "available"
    assert evidence["breadth"]["reason"] == "invalid_observation"


def test_trading_gap_does_not_compress_lookback_windows():
    bars = _bars(DOWN)
    result = _read(_candles(DOWN, VIX_UP, **{INDEX: {"bars": bars[:-3] + bars[-2:]}}))
    assert result["regime"]["state"] == "undetermined"
    for indicator in ("index_trend", "index_drawdown", "index_rebound"):
        assert _evidence(result)[indicator]["reason"] == "insufficient_history"


def test_view_conflict_and_new_sec_filing_do_not_change_regime():
    opinions = [{"expert": "a", "stance": "bullish"}, {"expert": "b", "stance": "bearish"}]
    captures = []
    quarters = iter(("2026Q2", "2026Q3"))

    def views(as_of):
        captures.append(as_of)
        return opinions

    def filing():
        return {"status": "available", "source": "SEC", "items": [{"quarter": next(quarters)}]}

    old = _read(_candles(DOWN, VIX_UP), filing=filing, views=views)
    new = _read(_candles(DOWN, VIX_UP), filing=filing, views=views)
    assert old["views"] == new["views"] == opinions
    assert captures == [market_calendar.local_today("us", NOW)] * 2
    assert old["filings"] == [{"quarter": "2026Q2"}]
    assert new["filings"] == [{"quarter": "2026Q3"}]
    assert new["sources"]["filings"] == {"status": "available", "source": "SEC"}
    assert old["regime"] == new["regime"]


def test_timeouts_are_per_source_and_concurrent_requests_share_the_filing_read():
    releases = Event()
    calls = []
    opinions = [{"expert": "a", "stance": "bullish"}]
    candles = _candles(DOWN, VIX_UP)

    def filing():
        calls.append("filing")
        releases.wait(2)
        return {"status": "available", "items": []}

    async def partly_slow(symbol, interval, count):
        if symbol == VIX:
            await asyncio.sleep(2)
        return await candles(symbol, interval, count)

    async def read_twice():
        service = MarketOutlook(
            candles=partly_slow, clock=lambda: NOW, filing=filing, views=lambda _: opinions, timeout=.2,
        )
        try:
            return await asyncio.gather(service.read(), service.read())
        finally:
            releases.set()

    results = asyncio.run(read_twice())
    assert calls == ["filing"]
    for result in results:
        assert result["sources"]["filings"] == {"status": "error", "reason": "timeout"}
        assert result["views"] == opinions
        assert result["regime"]["state"] == "downtrend"
        assert _evidence(result)["volatility"]["reason"] == "timeout"
