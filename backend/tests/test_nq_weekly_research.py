import math
from datetime import UTC, date, datetime, timedelta

import pytest

from desk import channels
from desk import nq_weekly_research as nq


def _bar(monday: str, close: float = 100, high: float = 110, low: float = 98) -> dict:
    return {"at": monday, "open": close, "close": close, "high": high, "low": low}


def _weeks(n: int, first: date = date(2018, 6, 4)) -> list[dict]:
    return [_bar((first + timedelta(weeks=i)).isoformat()) for i in range(n)]


@pytest.mark.parametrize(
    ("monday", "before", "at_close"),
    [("2026-11-16T05:00:00+00:00", "2026-11-20T21:59:59+00:00", "2026-11-20T22:00:00+00:00"),
     ("2026-06-15T04:00:00+00:00", "2026-06-19T20:59:59+00:00", "2026-06-19T21:00:00+00:00")],
)
def test_friday_close_uses_new_york_offset(monday, before, at_close):
    rows = [_bar(monday)]
    assert nq.report(rows, datetime.fromisoformat(before))["coverage"]["completed_weeks"] == 0
    coverage = nq.report(rows, datetime.fromisoformat(at_close))["coverage"]
    assert coverage["completed_weeks"] == 1
    assert coverage["last_completed_week"] == monday[:10]


def test_rejects_missing_and_duplicate_weeks_and_bad_completed_ohlc():
    rows = _weeks(3)
    as_of = datetime(2020, 1, 7, tzinfo=UTC)
    with pytest.raises(ValueError, match="missing weekly bar"):
        nq.report([rows[0], rows[2]], as_of)
    with pytest.raises(ValueError, match="unique and in ascending order"):
        nq.report([rows[0], rows[0]], as_of)
    with pytest.raises(ValueError, match="unique and in ascending order"):
        nq.report([rows[1], rows[0]], as_of)
    with pytest.raises(ValueError, match="positive finite"):
        nq.report([dict(rows[0], close=float("nan"))], as_of)
    with pytest.raises(ValueError, match="OHLC is inconsistent"):
        nq.report([dict(rows[0], close=120)], as_of)
    with pytest.raises(ValueError, match="New York Monday"):
        nq.report([dict(rows[0], at="2018-06-03")], as_of)
    with pytest.raises(ValueError, match="UTC offset"):
        nq.report(rows, datetime(2020, 1, 7))


def test_forming_week_ohlc_is_never_used():
    rows = [_bar("2026-11-16"), {"at": "2026-11-23"}]
    as_of = datetime.fromisoformat("2026-11-27T16:59:59-05:00")
    assert nq.report(rows, as_of)["coverage"]["last_completed_week"] == "2026-11-16"
    with pytest.raises(ValueError, match="positive finite"):
        nq.report(rows, datetime.fromisoformat("2026-11-27T17:00:00-05:00"))


def test_fibonacci_uses_log_extension_from_a_confirmed_higher_c():
    closes = [96, 97, 98, 108, 120, 115, 110, 105, 100, 95, 90, 85, 80, 85, 90, 100, 105, 108,
              115, 125, 140, 135, 130, 129, 128, 127, 126]
    rows = [_bar(row["at"], close, close + 1, close - 1) for row, close in zip(_weeks(len(closes)), closes)]
    assert nq._fib(rows[:23]) == (None, [])
    level, anchors = nq._fib(rows[:24])
    assert anchors == [rows[i]["at"] for i in (4, 12, 20)]
    assert level == pytest.approx(141 * (79 / 121) ** 2.618)


def test_weekly_channel_projects_outer_lower_and_ignores_a_broken_higher_rank(monkeypatch):
    rows = _weeks(55)
    broken = channels.Channel("upper", 1, 8, 6, 0, 0.1, [], [], 0, 100)
    intact = channels.Channel("upper", 3, 18, 4.7, 0.001, 0.2, [], [], 0, 80)

    def ranked(prefix, k, unit):
        assert prefix is rows
        assert k == 3
        assert unit == pytest.approx(0.12)
        return [broken, intact]

    monkeypatch.setattr(channels, "find_channels", ranked)
    level, anchors = nq._outer_channel(rows, 12)
    assert level == pytest.approx(math.exp(4.7 + (55 - 3) * 0.001 - 2 * 0.2))
    assert anchors == [rows[i]["at"] for i in (3, 18)]


def test_confluence_needs_two_price_contacts_or_one_with_rsi():
    assert not nq._confluent(0, True)
    assert not nq._confluent(1, False)
    assert nq._confluent(2, False)
    assert nq._confluent(1, True)


def test_bounce_failure_and_later_weekly_recovery_are_independent():
    future = [_bar("2020-01-06", close=120, high=123, low=89), _bar("2020-01-13", close=120, high=121),
              _bar("2020-01-20", close=122, high=124)]
    assert nq._reaction(future[:2], 115, 90) == ("invalidated", future[0]["at"])
    assert nq._recovery(future, [True, False, True], 115, 90) == ("expired", future[-1]["at"])
    assert nq._recovery(future, [False, True, True], 115, 90) == ("success", future[-1]["at"])
    assert nq._adverse_pct(future, 100) == pytest.approx(11)


def test_methods_are_frozen_at_signal_and_complete_windows_stay_in_period():
    rows = _weeks(104)
    as_of = datetime(2021, 1, 1, tzinfo=UTC)
    original = nq.report(rows, as_of)
    assert original["coverage"]["complete_windows"] == {
        "2016–2019": 16, "2020–2022": 9, "2023–2026": 0, "all": 25,
    }

    baseline = original["segments"]["all"]["correction"]["baseline"]
    assert baseline["bounce"]["sample_count"] == baseline["recovery"]["sample_count"] == 25
    assert baseline["bounce"]["false_signals"] == 25
    assert baseline["bounce"]["max_extra_adverse_pct"] == 2
    assert original["segments"]["all"]["severe"]["baseline"]["bounce"]["hit_rate_pct"] is None

    changed = [dict(row, low=10) if nq.WARMUP < i <= nq.WARMUP + 2 else row for i, row in enumerate(rows)]
    first = original["events"][0]
    revised = nq.report(changed, as_of)["events"][0]
    assert {k: v for k, v in first.items() if k not in nq.HORIZONS} == {
        k: v for k, v in revised.items() if k not in nq.HORIZONS
    }
    assert first["bounce"]["outcome"] == "expired"
    assert revised["bounce"]["outcome"] == "invalidated"
    assert revised["bounce"]["adverse_pct"] == pytest.approx(90)

    shortened = nq.report(rows[:nq.WARMUP + nq.RECOVERY_WEEKS + 1], as_of)
    assert shortened["events"] == [first]
