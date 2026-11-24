"""Deterministic #929 checks. The only delivered alert goes to an in-process sink."""

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest

from desk import line_signals
from desk import line_watch_bars as bars
from desk.db import make_engine, migrate


def _stamp(value):
    return datetime.fromisoformat(value)


def _row(at, opening=100.0, close=101.0):
    return {"at": at.isoformat(), "open": opening, "high": max(opening, close) + 1,
            "low": min(opening, close) - 1, "close": close, "volume": 5}


def _result(rows, fetched):
    return {"bars": rows, "source": "yahoo", "fetched_at": fetched.isoformat()}


@pytest.mark.parametrize("ticker,market", [
    ("NQ=F", "CME"), ("^KS11", "KRX"), ("005930.KS", "KRX"), ("000660.KS", "KRX"),
    ("SOXL", "US"), ("KORU", "US"),
])
def test_support_examples_list_all_eight_frames(ticker, market):
    assert line_signals.allowed_support()[ticker] == {
        "market": market, "supported": list(line_signals.TIMEFRAMES), "unsupported": {},
    }


@pytest.mark.parametrize("tf,source_tf", [
    ("5m", "5m"), ("10m", "5m"), ("15m", "15m"), ("60m", "60m"), ("120m", "60m"),
    ("240m", "60m"), ("1d", "1d"), ("1w", "1d"),
])
def test_nq_frame_uses_the_declared_source_and_a_completed_exact_bucket(tf, source_tf):
    # Last CME session in the week. Every interval has an expected bar before 17:00 ET.
    at = _stamp("2026-11-20T22:10:25+00:00")
    plan = bars.plan("CME", tf, at)
    assert plan.expected is not None and plan.expected.end == _stamp("2026-11-20T22:00:00+00:00")
    assert bars.source_request(tf)[0] == source_tf
    if tf in ("1d", "1w"):
        rows = [_row(_stamp(f"2026-11-{d:02d}T05:00:00+00:00")) for d in (*range(9, 14), *range(16, 21))]
    else:
        source_width = timedelta(minutes=bars.MINUTES[source_tf])
        rows = [_row(window.start + i * source_width) for window in (plan.prior, plan.expected)
                for i in range((window.end - window.start) // source_width)]
    got = bars.sample("CME", tf, plan, _result(rows, at), at)
    assert got.reason is None and got.source == "yahoo" and got.fetched_at == at
    assert got.bars[-1]["close"] == 101


def test_ten_minutes_needs_both_completed_members_in_one_session():
    at = _stamp("2026-11-17T23:20:25+00:00")  # next CME session, with the feed's 10-minute delay
    plan = bars.plan("CME", "10m", at)
    first = _row(_stamp("2026-11-17T23:00:00+00:00"), 100, 101)
    second = _row(_stamp("2026-11-17T23:05:00+00:00"), 101, 104)
    previous = [_row(_stamp(f"2026-11-17T21:{m}:00+00:00"), 95, 95) for m in (50, 55)]
    future = _row(_stamp("2026-11-17T23:10:00+00:00"), 105, 106)

    got = bars.sample("CME", "10m", plan, _result([*previous, first, second, future], at), at)
    assert got.reason is None
    assert len(got.bars) == 2
    assert got.bars[-1] == {"at": first["at"], "open": 100, "high": 105, "low": 99, "close": 104, "volume": 10}
    for partial in ([*previous, second, future], [*previous, first, future], [*previous, first, second, second],
                    [previous[1], first, second]):
        # Nor can detection bridge a hole in the last pair from the previous session.
        assert bars.sample("CME", "10m", plan, _result(partial, at), at).reason == "bar_missing"
    delay = _result([first, second], at - timedelta(seconds=30))
    assert bars.sample("CME", "10m", plan, delay, at).reason == "source_delayed"
    late = at + timedelta(minutes=21)
    assert bars.sample("CME", "10m", plan, _result([*previous, first, second], late), late).reason == "stale_bar"


@pytest.mark.parametrize("tf,minutes", [("5m", 5), ("10m", 10)])
def test_nq_delayed_source_is_read_for_the_same_bucket_after_its_lag(tf, minutes):
    start = _stamp("2026-11-17T23:00:00+00:00")
    close = start + timedelta(minutes=minutes)
    lag = timedelta(minutes=10)
    early = close + lag - timedelta(seconds=1)
    waiting = bars.plan("CME", tf, early)
    assert waiting.expected is None and waiting.reason == "source_delayed"
    now = close + lag + timedelta(seconds=25)
    plan = bars.plan("CME", tf, now)
    assert plan.expected == bars.Window(start, close)
    rows = [_row(window.start + timedelta(minutes=offset)) for window in (plan.prior, plan.expected)
            for offset in range(0, minutes, 5)]
    assert bars.sample("CME", tf, plan, _result(rows, early), now).reason == "source_delayed"
    got = bars.sample("CME", tf, plan, _result(rows, now), now)
    assert got.reason is None and got.bars[-1]["at"] == start.isoformat() and got.lag_minutes == 10


def test_weekly_waits_for_the_expected_close_and_every_session():
    before = _stamp("2026-11-20T21:59:00+00:00")
    assert bars.plan("CME", "1w", before).expected is None
    now = _stamp("2026-11-20T22:10:25+00:00")
    plan = bars.plan("CME", "1w", now)
    rows = [_row(_stamp(f"2026-11-{d}T05:00:00+00:00")) for d in range(16, 21)]
    rows = rows[:2] + rows[3:]
    assert bars.sample("CME", "1w", plan, _result(rows, now), now).reason == "bar_missing"
    assert bars.plan("CME", "1w", now + timedelta(hours=6)).reason == "stale_bar"


def test_different_markets_respect_half_day_holiday_and_late_open():
    us = _stamp("2026-11-27T18:15:25+00:00")  # Friday 13:00 ET + conservative source delay
    weekly = bars.plan("US", "1w", us)
    assert weekly.expected is not None and weekly.expected.end == _stamp("2026-11-27T18:00:00+00:00")
    rows = [_row(_stamp(f"2026-11-{d}T05:00:00+00:00")) for d in (*range(16, 21), 23, 24, 25, 27)]
    assert len(bars.sample("US", "1w", weekly, _result(rows, us), us).bars) == 2
    assert bars.plan("CME", "10m", us).reason == "session_unverified"
    assert bars.plan("CME", "1w", us).reason == "session_unverified"
    assert bars.plan("US", "1d", _stamp("2026-11-26T21:00:25+00:00")).reason == "stale_bar"
    csat = next(day for day in bars.calendar.CSAT_DAYS if day.year == 2025)
    close = datetime.combine(csat, bars.calendar.KR_CSAT_CLOSE, bars.calendar.KST).astimezone(UTC)
    daily = bars.plan("KRX", "1d", close + timedelta(minutes=20, seconds=25))
    assert daily.expected is not None and daily.expected.end == close
    assert daily.expected.start.hour == 1   # 10:00 KST, not the ordinary open
    assert daily.expected.end.date() == date(2025, 11, 13)


@pytest.fixture
def watch_engine(db_url):
    migrate(db_url)
    engine = make_engine(db_url)
    yield engine
    engine.dispose()


def test_confirmed_resistance_is_recorded_once_after_restart(watch_engine, monkeypatch):
    sent = []
    now = [_stamp("2026-11-17T23:20:25+00:00")]
    watch = line_signals.LineWatch(watch_engine, clock=lambda: now[0],
                                   send_notice=lambda *a, **kw: sent.append(kw["payload"]))
    original = watch.settings()
    assert original["rules"] is None and original["tickers"] == ["NQ=F"]
    saved = watch.save({"enabled": True, "rules": [
        {"ticker": "NQ=F", "timeframes": ["10m"], "side": "resistance", "enabled": True},
        {"ticker": "SOXL", "timeframes": ["1w"], "enabled": False},
    ]})
    assert saved["tickers"] == original["tickers"] and saved["timeframes"] == original["timeframes"]
    assert watch.save({"events": {"retest": False}})["rules"] == saved["rules"]
    checked = []

    def detect(rows, tf, *, confirmed):
        checked.append((tf, confirmed))
        return [
            {"event": "touch", "rule": "touch_support", "role": "channel_lower", "line_id": "low",
             "line": "지지", "at": rows[-1]["at"], "price": 101, "line_price": 100},
            {"event": "touch", "rule": "touch_resistance", "role": "channel_upper", "line_id": "high",
             "line": "저항", "at": rows[-1]["at"], "price": 101, "line_price": 102},
        ]

    monkeypatch.setattr(line_signals, "detect", detect)
    asked = []

    class Prices:
        async def candles(self, ticker, tf, count):
            asked.append((ticker, tf, count))
            anchor = now[0].replace(hour=0, minute=0, second=0)
            previous = [anchor + timedelta(hours=21, minutes=m) for m in (50, 55)]
            current = [anchor + timedelta(hours=23, minutes=m) for m in (0, 5)]
            return _result([_row(stamp) for stamp in previous + current], now[0])

    assert asyncio.run(line_signals.run_pass(watch, Prices(), None)) == 1
    assert asked == [("NQ=F", "5m", 800)]
    assert checked == [("10m", True)] and len(sent) == 1
    payload = sent[0]
    assert "닿았어요" in payload["title"] and "넘었어요" not in payload["title"]
    assert payload["closed_at"] == "2026-11-17T23:10:00+00:00"
    assert payload["source"] == "yahoo" and payload["fetched_at"] == now[0].isoformat()
    assert payload["lag_minutes"] == 10 and "지연 여유 10분" in payload["body"]
    assert "관측가" in payload["body"]
    assert "5분봉" in payload["body"] and payload["url"] == "/#/charts?t=NQ%3DF&iv=5m"

    restarted = line_signals.LineWatch(watch_engine, clock=lambda: now[0],
                                      send_notice=lambda *a, **kw: sent.append(kw["payload"]))
    assert asyncio.run(line_signals.run_pass(restarted, Prices(), None)) == 0
    recent, = restarted.recent()
    assert recent["rule"] == "touch_resistance"
    assert _stamp(recent["recorded_at"]).tzinfo is UTC
    assert len(sent) == 1
    now[0] += timedelta(days=1)
    assert asyncio.run(line_signals.run_pass(restarted, Prices(), None)) == 1
    assert len(sent) == 2
    assert restarted.save({"rules": None})["rules"] is None


def test_rules_normalize_aliases_and_reject_unsupported_markets():
    rule = {"ticker": "NQ=F", "enabled": True, "timeframes": ["2h", "120m"], "side": "support"}
    assert line_signals.clean({"rules": [rule]})["rules"][0]["timeframes"] == ["120m"]
    assert line_signals.clean({"rules": []})["rules"] == []
    for malformed in ([rule, rule], [{**rule, "side": "boths"}], [{**rule, "ticker": "BTC-USD"}],
                      [{**rule, "timeframes": []}]):
        with pytest.raises(ValueError):
            line_signals.clean({"rules": malformed})
