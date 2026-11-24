from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from desk.line_alert_replay import Alert, Bar, Levels, Reference, Series, compare

SERIES = Series("NQ1!", "CME continuous as exported", "1w")
MONDAY = datetime(2026, 1, 5, tzinfo=UTC)


def week(n, low=98, high=102):
    at = MONDAY + timedelta(weeks=n)
    end = at + timedelta(days=4, hours=22)
    return Bar(n, at, end, end + timedelta(minutes=2), low, high, 100)


ANCHOR = week(0, 80, 125)
EXTERNAL = Levels(105, 95)
ALERT = Alert("export:1", SERIES, ANCHOR.at, ANCHOR.available_at, "touch_support", EXTERNAL)
DESK = Reference(SERIES, ANCHOR.at, ALERT.rule, ALERT.observed_at, Levels(103, 96))
BASE = replace(DESK, levels=Levels(108, 97))


def replay(bars, alerts=(ALERT,), desk=(DESK,), baseline=(BASE,), *, as_of=None, horizon=2):
    return compare(SERIES, bars, list(alerts), list(desk), list(baseline),
                   as_of=as_of or week(4).available_at, horizon=horizon)


def test_same_bar_comparison_separates_first_outcomes_and_ignores_alert_bar():
    up = week(1, 98, 106)
    down = week(2, 94, 102)
    row, = replay([ANCHOR, up, down])
    assert row["arms"] == {
        "external": {"status": "target_first", "bars": 1, "bar_at": up.at},
        "desk": {"status": "target_first", "bars": 1, "bar_at": up.at},
        "baseline": {"status": "invalidation_first", "bars": 2, "bar_at": down.at},
    }


def test_down_side_and_both_thresholds_on_one_bar_cannot_be_ordered():
    alert = replace(ALERT, rule="break_down", levels=Levels(95, 105))
    down = week(1, 94, 106)
    row, = replay([ANCHOR, down], alerts=(alert,))
    assert row["arms"]["external"] == {"status": "ambiguous", "bars": 1, "bar_at": down.at}
    assert row["arms"]["desk"]["status"] == "reference_unavailable_at_alert"


@pytest.mark.parametrize("changed", [
    {"series": replace(SERIES, symbol="NQ=F")},
    {"series": replace(SERIES, product="front month")},
    {"series": replace(SERIES, timeframe="1d")},
    {"bar_at": ANCHOR.at + timedelta(minutes=1)},
])
def test_symbol_product_timeframe_and_exact_bar_must_match(changed):
    row, = replay([ANCHOR], (replace(ALERT, **changed),))
    assert row["status"] == "unmatched"


def test_duplicates_keep_first_observed_and_conflicting_ids_fail():
    retry = replace(ALERT, message_id="export:2", observed_at=ALERT.observed_at + timedelta(hours=1))
    kst = timezone(timedelta(hours=9))
    retry = replace(retry, bar_at=retry.bar_at.astimezone(kst))
    row, = replay([ANCHOR, week(1)], (retry, ALERT, ALERT))
    assert row["message_id"] == ALERT.message_id
    with pytest.raises(ValueError, match="conflicting alert message ID"):
        replay([ANCHOR], (ALERT, replace(ALERT, rule="break_up")))


def test_late_reference_and_partial_bars_do_not_leak_into_result():
    up = week(1, 98, 110)
    later_basis = replace(DESK, available_at=ALERT.observed_at + timedelta(seconds=1))
    row, = replay([ANCHOR, up], desk=(later_basis,), as_of=up.end)
    assert row["arms"]["external"] == {"status": "incomplete", "bars": 0}
    assert row["arms"]["desk"]["status"] == "reference_unavailable_at_alert"
    next_row, = replay([ANCHOR, up], desk=(later_basis,), as_of=up.available_at)
    assert next_row["arms"]["external"]["status"] == "target_first"
    assert next_row["arms"]["desk"]["status"] == "reference_unavailable_at_alert"


def test_before_availability_and_delayed_old_bar_are_distinct():
    early = replace(ALERT, observed_at=ANCHOR.end)
    late = replace(ALERT, observed_at=week(1).end)
    assert replay([ANCHOR], (early,))[0]["status"] == "unavailable_at_alert"
    assert replay([ANCHOR, week(1)], (late,))[0]["status"] == "stale_alert"


def test_overlapping_bar_and_gap_do_not_count_as_post_alert_evidence():
    overlap = week(1, 85, 120)
    later = replace(ALERT, observed_at=overlap.at + timedelta(minutes=1))
    row, = replay([ANCHOR, overlap, week(2)], (later,))
    assert row["arms"]["external"] == {"status": "incomplete", "bars": 1}
    row, = replay([ANCHOR, week(2, 85, 120)])
    assert row["arms"]["external"] == {"status": "incomplete", "bars": 0}


def test_missing_levels_and_fixed_horizon():
    beyond_horizon = week(3, 80, 120)
    rows = replay([ANCHOR, week(1), week(2), beyond_horizon], (ALERT, replace(
        ALERT, message_id="export:2", levels=None,
    )))
    assert rows[0]["arms"]["external"] == {"status": "neither", "bars": 2}
    assert rows[1]["arms"]["external"] == {"status": "levels_missing"}


def test_rejects_naive_times_and_inconsistent_source_ranges():
    with pytest.raises(ValueError, match="UTC offsets"):
        replay([replace(ANCHOR, available_at=ANCHOR.available_at.replace(tzinfo=None))])
    with pytest.raises(ValueError, match="invalid finalized bar"):
        replay([replace(ANCHOR, high=90)])


def test_dst_repeated_local_bar_starts_are_distinct_utc_instants():
    ny = ZoneInfo("America/New_York")
    first_one = datetime(2026, 11, 1, 1, tzinfo=ny, fold=0)
    second_one = first_one.replace(fold=1)
    two = first_one.replace(hour=2)
    series = replace(SERIES, timeframe="60m")
    first = Bar(0, first_one, second_one, second_one, 99, 101, 100)
    second = Bar(1, second_one, two, two, 98, 106, 100)
    alert = replace(ALERT, series=series, bar_at=first_one, observed_at=second_one)
    row, = compare(series, [first, second], [alert], [], [], as_of=two, horizon=1)
    assert row["bar_at"] == first_one.astimezone(UTC)
    assert row["arms"]["external"] == {
        "status": "target_first", "bars": 1, "bar_at": second_one.astimezone(UTC),
    }

    observed = first_one.replace(minute=20)
    late = Reference(series, first_one, ALERT.rule, second_one.replace(minute=10), EXTERNAL)
    row, = compare(series, [replace(first, end=observed, available_at=observed), second],
                   [replace(alert, observed_at=observed)], [late], [], as_of=two, horizon=1)
    assert row["arms"]["desk"]["status"] == "reference_unavailable_at_alert"
