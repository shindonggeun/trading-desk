from datetime import UTC, date, datetime, timedelta

import pytest

from desk import nq_weekly_signal as signal
from desk import weekly_chart_evidence as chart

FIRST = date(2025, 1, 6)
LAST = 60


def _rules(**changes):
    return {**signal.DEFAULT_SETTINGS, "price_conditions": ["bollinger"], **changes}


def _monday(index):
    return FIRST + timedelta(weeks=index)


def _scheduled_confirmation(index):
    return signal._confirmed_at(_monday(index))


def _rows():
    closes = [*range(100, 125), *range(123, 75, -1)]
    return [{"at": _monday(i).isoformat(), "open": close, "high": close + 1, "low": close - 1, "close": close}
            for i, close in enumerate(closes)]


def _source(rows, fetched, source="yahoo"):
    return {"bars": rows, "source": source, "fetched_at": fetched.isoformat()}


@pytest.mark.parametrize("changes", [
    {"enabled": 1}, {"rsi_limit": True}, {"rsi_limit": "30"}, {"rsi_limit": float("nan")},
    {"rsi_limit": float("inf")}, {"rsi_limit": 101}, {"rsi_limit": -1}, {"price_conditions": []},
    {"price_conditions": ("bollinger",)}, {"price_conditions": [["bollinger"]]},
    {"price_conditions": ["bollinger", "bollinger"]}, {"price_conditions": ["irrelevant"]},
    {"min_hits": 0}, {"min_hits": 2}, {"min_hits": True}, {"min_hits": 1.0}, {"arbitrary": "extra"},
])
def test_strict_rule_validation(changes):
    with pytest.raises(ValueError):
        signal.validate_settings(_rules(**changes))


def test_rule_order_is_canonical_and_alerts_default_off():
    checked = signal.validate_settings(_rules(price_conditions=["fib", "bollinger"], min_hits=2))
    assert checked == {"enabled": False, "rsi_limit": 30.0, "price_conditions": ["bollinger", "fib"], "min_hits": 2}
    assert signal.validate_settings(_rules(rsi_limit=0))["rsi_limit"] == 0
    assert signal.validate_settings(_rules(rsi_limit=100))["rsi_limit"] == 100


def test_match_reuses_closed_chart_evidence_and_ignores_forming_bar_prices():
    now = _scheduled_confirmation(LAST)
    rows = _rows()[:LAST + 2]
    closed = _source(rows, now)
    baseline = chart.build(closed, None, now)
    observed = signal.observe(closed, _rules(), now)
    assert observed["status"] == "matched"
    assert observed["rollover"] == "unknown"
    assert observed["week"] == {"start": _monday(LAST).isoformat(), "closed_at": baseline["week"]["closedAt"],
                                "confirmed_at": now.isoformat(), "fetched_at": now.isoformat(), "source": "yahoo"}
    assert observed["forming_week"] == {
        "start": _monday(LAST + 1).isoformat(), "source_present": True, "status": "pending",
    }
    assert observed["evidence"]["rsi"]["value"] == baseline["evidence"]["rsi"]["value"]
    assert observed["evidence"]["bollinger"]["value"] == baseline["evidence"]["bollinger"]["value"]
    assert observed["evidence"]["bollinger"]["actual_low"] == rows[LAST]["low"]
    assert observed["matched_conditions"] == ["bollinger"]
    assert signal.observe(_source([*rows[:-1], {"at": rows[-1]["at"], "low": -123}], now), _rules(), now) == observed


def test_current_rsi_price_minimum_and_unverifiable_inputs_have_separate_states():
    now = _scheduled_confirmation(LAST)
    rows = _rows()[:LAST + 2]
    chosen = _rules(price_conditions=["bollinger", "channel"], min_hits=2)
    unknown = signal.observe(_source(rows, now), chosen, now)
    assert (unknown["status"], unknown["reason"]) == ("unavailable", "insufficient_overlap_evidence")
    assert unknown["evidence"]["channel"]["status"] == "unknown"
    assert unknown["matched_conditions"] == ["bollinger"]
    assert signal.observe(_source(rows, now), {**chosen, "min_hits": 1}, now)["status"] == "matched"

    rows[LAST]["low"] = rows[LAST]["close"]
    rsi_only = signal.observe(_source(rows, now), _rules(), now)
    assert rsi_only["evidence"]["rsi"]["status"] == "hit"
    assert (rsi_only["status"], rsi_only["reason"]) == ("not_matched", "too_few_price_hits")
    rsi_above = signal.observe(_source(rows, now), _rules(rsi_limit=0), now)
    assert (rsi_above["status"], rsi_above["reason"]) == ("not_matched", "rsi_above_limit")


def test_confirmation_requires_next_cme_open_fresh_fetch_and_adjacent_week_label():
    now = _scheduled_confirmation(LAST)
    rows = _rows()[:LAST + 2]
    before = now - timedelta(seconds=1)
    waiting = signal.observe(_source(rows, before), _rules(), before)
    assert (waiting["status"], waiting["reason"], waiting["forming_week"]) == (
        "pending", "unconfirmed_weekly_close", None,
    )
    assert waiting["week"]["start"] == _monday(LAST).isoformat()
    assert signal.observe(_source(rows, before), _rules(), now)["status"] == "pending"
    assert signal.observe(_source(rows[:-1], now), _rules(), now)["status"] == "pending"
    stale = signal.observe(_source(rows[:-2], before), _rules(), now)
    assert stale["reason"] == "stale_weekly_bars"
    assert stale["week"]["source"] == "yahoo" and stale["week"]["fetched_at"] == before.isoformat()


def test_missing_revised_or_wrong_source_data_does_not_produce_a_live_signal():
    now = _scheduled_confirmation(LAST)
    rows = _rows()[:LAST + 2]
    options = [
        (None, "weekly_source_unavailable"),
        (_source(rows, now, "estimated"), "unsupported_weekly_source"),
        (_source(rows, now + timedelta(seconds=1)), "invalid_weekly_timestamp"),
        (_source([*rows[:10], *rows[11:]], now), "invalid_weekly_bars"),
        (_source([*rows[:10], rows[9], *rows[10:]], now), "invalid_weekly_bars"),
        (_source([*rows[:10], {**rows[10], "high": 0}, *rows[11:]], now), "invalid_weekly_bars"),
    ]
    for weekly, reason in options:
        got = signal.view(weekly, _rules(), now)
        assert got["current"]["status"] == "unavailable"
        assert got["current"]["reason"] == reason
        assert got["markers"] == []
        assert got["study"]["status"] == "unavailable"


def test_replay_keeps_matching_markers_but_measures_only_new_edges():
    now = _scheduled_confirmation(LAST)
    rows = _rows()[:LAST + 2]
    rows[44]["low"] = 50
    replay = signal.view(_source(rows, now), _rules(), now, weeks=104)
    assert len(replay["markers"]) > 1
    edges = [marker for marker in replay["markers"] if marker["edge"]]
    first = edges[0]
    assert first["week"]["start"] == rows[41]["at"]
    assert replay["study"]["coverage"]["matched_weeks"] == len(replay["markers"])
    assert replay["study"]["coverage"]["edge_sample_count"] == len(edges)
    assert replay["study"]["coverage"]["last_week"] == rows[LAST]["at"]
    assert replay["study"]["coverage"]["first_week"] == rows[0]["at"]
    for horizon in (4, 12):
        follow_up = first["follow_up"][f"{horizon}w"]
        assert follow_up["status"] == "observed"
        assert follow_up["through_week"] == rows[41 + horizon]["at"]
        assert follow_up["return_pct"] == pytest.approx(100 * (rows[41 + horizon]["close"] / rows[41]["close"] - 1))
        assert follow_up["max_additional_decline_pct"] == pytest.approx(100 * (1 - 50 / rows[41]["close"]))
        measure = replay["study"]["horizons"][f"{horizon}w"]
        assert measure["sample_count"] == len(edges)
        assert measure["observed_count"] + measure["unverified_count"] == len(edges)
        assert measure["severe_additional_decline_count"] >= 1

    cutoff = _scheduled_confirmation(41)
    earlier = signal.view(_source(rows[:43], cutoff), _rules(), cutoff)
    found = earlier["markers"][-1]
    assert found["evidence"] == first["evidence"]
    assert found["edge"] is True
    assert found["follow_up"]["4w"]["return_pct"] is None
    assert earlier["study"]["horizons"]["12w"]["unverified_count"] >= 1
    trailing = signal.view(_source(rows, now), _rules(), now, weeks=1)
    assert trailing["markers"][0]["edge"] is False
    assert trailing["study"]["horizons"]["4w"]["sample_count"] == 0


def test_unknown_week_does_not_create_a_replay_sample(monkeypatch):
    rows = _rows()[:9]
    now = _scheduled_confirmation(7)
    outcomes = ["not_matched", "unavailable", "matched", "matched", "unavailable", "matched", "not_matched", "matched"]

    def point(_bars, _bands, _rsis, index, _settings):
        return {"status": outcomes[index], "reason": None, "evidence": {},
                "matched_conditions": [], "min_hits": 1}

    monkeypatch.setattr(signal, "_point", point)
    got = signal.view(_source(rows, now), _rules(), now)
    assert [m["week"]["start"] for m in got["markers"] if m["edge"]] == [rows[7]["at"]]
    assert got["study"]["coverage"]["unverified_weeks"] == 2


def test_view_uses_a_bounded_window():
    with pytest.raises(ValueError):
        signal.view(None, _rules(), datetime(2026, 4, 15, tzinfo=UTC), weeks=signal.MAX_HISTORY_WEEKS + 1)
