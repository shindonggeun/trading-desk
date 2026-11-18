"""#297: converging patterns (triangles, wedges) from swing highs and lows."""

import math
from datetime import date, timedelta

from desk import patterns

K = 3


def _zigzag(top, bottom, n=120, period=12, start=date(2026, 1, 1)):
    """Bars bouncing between two lines: top(i) and bottom(i) give the swing high / low at bar i."""
    out = []
    for i in range(n):
        phase = (i % period) / period
        frac = 1 - abs(2 * phase - 1)                       # 0 at the bottom, 1 at the top
        mid = bottom(i) + (top(i) - bottom(i)) * frac
        out.append({"at": (start + timedelta(days=i)).isoformat() + "T05:00:00+00:00",
                    "open": mid, "high": mid + (top(i) - mid) * 0.0 + 0.05 * frac,
                    "low": mid - 0.05 * (1 - frac), "close": mid, "volume": 1000})
    return out


def _find(bars, atr=1.0):
    return patterns.find(bars, K, atr)


def test_the_four_shapes_are_named():
    n = 120
    sym = _find(_zigzag(lambda i: 120 - 0.12 * i, lambda i: 90 + 0.12 * i, n))
    asc = _find(_zigzag(lambda i: 120.0, lambda i: 90 + 0.2 * i, n))
    desc = _find(_zigzag(lambda i: 120 - 0.2 * i, lambda i: 90.0, n))
    rising = _find(_zigzag(lambda i: 110 + 0.1 * i, lambda i: 90 + 0.25 * i, n))
    assert sym and sym[0]["kind"] == "symmetric" and sym[0]["label"] == "대칭 삼각"
    assert asc and asc[0]["kind"] == "ascending" and asc[0]["expected"] == "up"
    assert desc and desc[0]["kind"] == "descending" and desc[0]["expected"] == "down"
    assert rising and rising[0]["kind"] == "rising_wedge" and rising[0]["expected"] == "down"
    p = sym[0]
    assert p["state"] == "converging" and p["apex"]["bars_ahead"] > 0 and p["narrowed"] <= patterns.NARROW
    assert len(p["upper"]["touches"]) >= 2 and len(p["lower"]["touches"]) >= 2
    assert p["upper"]["to"]["price"] > p["lower"]["to"]["price"]


def test_parallel_or_widening_lines_are_no_pattern():
    assert _find(_zigzag(lambda i: 120.0, lambda i: 90.0)) == []               # a range
    assert _find(_zigzag(lambda i: 110 + 0.1 * i, lambda i: 90 + 0.1 * i)) == []  # a channel
    assert _find(_zigzag(lambda i: 110 + 0.1 * i, lambda i: 100 - 0.1 * i)) == []  # widening


def test_a_close_through_the_top_in_the_last_bars_is_a_break_up():
    bars = _zigzag(lambda i: 120.0, lambda i: 90 + 0.2 * i, 120)
    for b in bars[-2:]:
        b.update(close=125.0, high=125.5)
    got = _find(bars)
    assert got and got[0]["state"] == "broke_up"


def test_too_few_bars_or_no_atr_is_nothing_and_stats_are_read_or_none(monkeypatch):
    assert _find(_zigzag(lambda i: 120.0, lambda i: 90.0, 10)) == []
    assert patterns.find(_zigzag(lambda i: 120 - 0.12 * i, lambda i: 90 + 0.12 * i), K, 0) == []
    stats = {"n": 40, "verdict": "no_edge", "edge_pct": 1.2, "valid": {"n": 12, "expected_pct": 55.0},
             "null": {"n": 300, "expected_pct": 53.8}}
    monkeypatch.setattr(patterns, "_stats_cache", {"kinds": {"1d:symmetric": stats}})
    p = _find(_zigzag(lambda i: 120 - 0.12 * i, lambda i: 90 + 0.12 * i))[0]
    note = patterns.prompt_note([p])
    assert p["stats"]["n"] == 40 and "held-out 12 cases" in note and "53.8% on the same prices shuffled" in note
    assert "no edge over chance" in note and "unless an edge is stated" in note
    monkeypatch.setattr(patterns, "_stats_cache", {"kinds": {"1d:symmetric": {**stats, "verdict": "edge",
                                                                              "edge_pct": 9.0}}})
    assert "+9.0 points: an edge over chance" in patterns.prompt_note([_find(_zigzag(lambda i: 120 - 0.12 * i, lambda i: 90 + 0.12 * i))[0]])
    assert patterns.stats_for("4h", "symmetric") is None
    monkeypatch.setattr(patterns, "_stats_cache", {})
    assert "not evidence" in patterns.prompt_note(_find(_zigzag(lambda i: 120 - 0.12 * i, lambda i: 90 + 0.12 * i)))


def test_the_backtest_follows_a_pattern_to_its_break_and_counts_it_once():
    from desk import patterns_bt

    bars = _zigzag(lambda i: 120.0, lambda i: 90 * 1.002 ** i, 140)   # straight in log price, as the lines are
    for i, b in enumerate(bars[100:], start=100):       # from bar 100 the price runs out of the top
        c = 121.0 + (i - 100) * 0.8
        b.update(open=c, high=c + 0.3, low=c - 0.3, close=c)
    rows = patterns_bt.instances(bars, "1d", K, 5, "T")
    assert len(rows) == 1                                # its variants while open are not new
    r = rows[0]
    assert r["kind"] == "ascending" and r["direction"] == "up" and r["expected"] == "up"
    assert r["move_atr"] > 0 and r["false_break"] is False


def test_the_table_splits_by_date_and_needs_enough_cases(monkeypatch):
    from desk import patterns_bt

    monkeypatch.setattr(patterns_bt, "MIN_N", 4)
    rows = [{"ticker": "T", "at": f"2024-01-{d:02d}", "kind": "ascending", "timeframe": "1d", "expected": "up",
             "direction": "up" if d % 3 else "down", "move_atr": 1.0, "false_break": d % 2 == 0} for d in range(1, 21)]
    rows += [{**rows[0], "kind": "descending"}]             # one case: no stats
    t = patterns_bt.table(rows)
    assert set(t) == {"1d:ascending"}
    s = t["1d:ascending"]
    assert s["n"] == 20 and s["train"]["n"] == 14 and s["valid"]["n"] == 6 and s["cut"] == "2024-01-15"
    assert s["expected_pct"] == round(100 * 14 / 20, 1) and s["none_pct"] == 0.0


def test_the_sr_result_carries_the_patterns():
    from desk import horizons, sr

    bars = _zigzag(lambda i: 120 - 0.12 * i, lambda i: 90 + 0.12 * i, 120)
    result = {"atr": 1.0, "levels": []}
    sr.add_patterns(result, bars, [], horizons.get("swing"))
    assert result["patterns"] and result["patterns"][0]["kind"] == "symmetric"
    assert result["patterns"][0]["timeframe"] == "1d"


def test_a_falling_wedge_a_break_down_and_the_live_price_decide_the_state():
    fw = _find(_zigzag(lambda i: 130 - 0.25 * i, lambda i: 110 - 0.1 * i))
    assert fw and fw[0]["kind"] == "falling_wedge" and fw[0]["expected"] == "up"
    bars = _zigzag(lambda i: 120 - 0.2 * i, lambda i: 90.0, 120)
    assert patterns.find(bars, K, 1.0, price=80.0)[0]["state"] == "broke_down"          # the live price
    assert patterns.find(bars, K, 1.0, price=None)[0]["state"] == "converging"


def test_the_shuffled_baseline_keeps_the_moves_and_the_verdict_needs_held_out_edge():
    from desk import patterns_bt

    bars = _zigzag(lambda i: 120 - 0.12 * i, lambda i: 90 + 0.12 * i, 120)
    sh = patterns_bt.shuffled(bars, 7)
    assert len(sh) == len(bars) and sh[0] == bars[0] and sh != bars
    moves = sorted(round(sh[i]["close"] / sh[i - 1]["close"], 9) for i in range(1, len(sh)))
    assert moves == sorted(round(bars[i]["close"] / bars[i - 1]["close"], 9) for i in range(1, len(bars)))
    assert all(b["low"] - 1e-6 <= b["close"] <= b["high"] + 1e-6 for b in sh)
    base = {"train": {"expected_pct": 60.0}, "valid": {"expected_pct": 62.0}}
    assert patterns_bt._verdict(base, {"expected_pct": 50.0}) == {"edge_pct": 12.0, "edge_needed_pct": 5.0, "verdict": "edge"}
    small = {**base, "valid": {"expected_pct": 62.0, "n": 30}}
    noisy = patterns_bt._verdict(small, {"expected_pct": 50.0, "n": 900})
    assert noisy["verdict"] == "no_edge" and noisy["edge_needed_pct"] > 12.0     # 30 cases: 12 points is noise
    assert patterns_bt._verdict(base, {"expected_pct": 59.0})["verdict"] == "no_edge"
    assert patterns_bt._verdict({**base, "train": {"expected_pct": 40.0}}, {"expected_pct": 50.0})["verdict"] == "unstable"
    assert patterns_bt._verdict(base, None)["verdict"] == "unmeasured"


def test_the_trend_note_carries_the_pattern_and_stands_alone_without_lines():
    from desk import trend

    p = _find(_zigzag(lambda i: 120 - 0.12 * i, lambda i: 90 + 0.12 * i))
    alone = trend.prompt_note({"trendlines": [], "fibonacci": None, "patterns": p})
    assert alone.startswith("Converging pattern") and "Desk-computed trendlines" not in alone
    assert trend.prompt_note({"trendlines": [], "fibonacci": None, "patterns": []}) is None


def test_add_patterns_reads_the_weekly_bars_and_keeps_a_failure_to_itself(monkeypatch):
    from desk import horizons, sr

    bars = _zigzag(lambda i: 120 - 0.12 * i, lambda i: 90 + 0.12 * i, 120)
    result = {"atr": 1.0, "levels": []}
    sr.add_patterns(result, bars, bars, horizons.get("swing"))
    assert [p["timeframe"] for p in result["patterns"]] == ["1d", "1w"]

    def boom(*a, **k):
        raise ValueError("x")

    monkeypatch.setattr(sr.patterns, "find", boom)
    sr.add_patterns(result, bars, [], horizons.get("swing"))
    assert result["patterns"] == [] and result["patterns_error"] == "ValueError: x"


# #629 when a side is usually chosen, the measured move and the odds

def _sym(n=120):
    return _zigzag(lambda i: 120 - 0.12 * i, lambda i: 90 + 0.12 * i, n)


def _intraday(days=10):
    """5-minute bars of a US session, 14:30-20:55 UTC, Monday to Friday."""
    from datetime import UTC, datetime

    out, d = [], datetime(2026, 11, 2, tzinfo=UTC)          # a Monday
    while len(out) < days * 78:
        if d.weekday() < 5:
            for m in range(78):
                t = d.replace(hour=14, minute=30) + timedelta(minutes=5 * m)
                out.append({"at": t.isoformat(), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1})
        d += timedelta(days=1)
    return out


def test_future_bar_times_follow_the_bars_own_hours_and_weekends():
    bars = _intraday()
    assert bars[-1]["at"] == "2026-11-13T20:55:00+00:00"                     # a Friday's last bar
    nxt = patterns.future_times(bars, 80)
    assert nxt[0] == "2026-11-16T14:30:00+00:00"                             # Monday's open, the weekend skipped
    assert nxt[77] == "2026-11-16T20:55:00+00:00" and nxt[78] == "2026-11-17T14:30:00+00:00"
    days = _sym(30)                                                          # every calendar day, 05:00 UTC
    assert patterns.future_times(days, 2, daily=True) == ["2026-01-31T05:00:00+00:00", "2026-02-01T05:00:00+00:00"]
    weekdays = [b for b in _sym(60) if date.fromisoformat(b["at"][:10]).weekday() < 5]
    assert all(date.fromisoformat(t[:10]).weekday() < 5 for t in patterns.future_times(weekdays, 10, daily=True))


def test_a_converging_pattern_says_when_how_far_and_how_sure(monkeypatch):
    monkeypatch.setattr(patterns, "_stats_cache", {})
    p = patterns.find(_sym(70), K, 4.0)[0]          # the apex ~55 bars ahead; a 30-point height is ~7.5 ATR of 4
    w, t = p["breakout_window"], p["targets"]
    assert w["basis"] == "textbook" and w["share"] == [0.5, 0.8] and p["odds"] is None and not w["passed"]
    assert w["bars_ahead"][0] <= w["bars_ahead"][1] <= math.ceil(p["apex"]["bars_ahead"])
    assert w["from"] <= w["to"] <= p["apex_at"]
    assert t["up"] > p["upper"]["to"]["price"] > p["lower"]["to"]["price"] > t["down"]
    assert t["height_pct"] > 0 and t["reach_pct"] is None and t["basis"] == "measured_move"
    assert p["explain"].startswith("일봉 대칭 삼각수렴 — 고점은 낮아지고 저점은 높아지는 중. 교과서로는 꼭짓점 전 ")
    assert "과거 통계는 아직 없어요." in p["explain"] and p["explain"].endswith("예측 아님.")

    stats = {"n": 41, "up_pct": 58.0, "down_pct": 42.0, "none_pct": 0.0, "false_break_pct": 30.0,
             "verdict": "no_edge", "edge_pct": 1.0, "frac_q25": 0.6, "frac_q75": 0.7, "reach_pct": 47.0,
             "by_prior": {"up": {"n": 12, "up_pct": 70.0, "down_pct": 30.0, "none_pct": 0.0}}}
    monkeypatch.setattr(patterns, "_stats_cache", {"kinds": {"1d:symmetric": stats}})
    p = patterns.find(_sym(70), K, 4.0)[0]
    assert p["breakout_window"]["basis"] == "measured" and p["breakout_window"]["share"] == [0.6, 0.7]
    o = p["odds"]
    assert (o["up_pct"], o["down_pct"], o["n"], o["small_sample"], o["prior"]) == (58.0, 42.0, 41, False, None)
    assert "보통 꼭짓점 전" in p["explain"] and "과거 위 58% · 아래 42%(표본 41)" in p["explain"]
    assert "방향 근거는 약해요" in p["explain"] and "목표까지 간 비율 47%" in p["explain"]
    by = {**stats, "by_prior": {"up": {"n": 35, "up_pct": 70.0, "down_pct": 30.0, "none_pct": 0.0},
                                "down": {"n": 35, "up_pct": 40.0, "down_pct": 60.0, "none_pct": 0.0}}}
    monkeypatch.setattr(patterns, "_stats_cache", {"kinds": {"1d:symmetric": {**by, "n": 20, "verdict": "edge"}}})
    p = _find(_sym())[0]
    prior = patterns._prior_trend(_sym(), 0)
    assert p["odds"]["prior"] in (None, "up", "down") and p["odds"]["n"] in (20, 35)
    if p["odds"]["prior"] is None:                                     # no trend before the pattern start
        assert p["odds"]["small_sample"] and "표본 적음" in p["explain"] and prior in ("none", "up", "down")
    assert "방향 근거는 약해요" not in p["explain"]
    passed = _find(_sym())[0]                                          # near the apex: the window is behind it
    assert passed["breakout_window"]["passed"] and "흔히 방향이 나오는 구간은 지났고" in passed["explain"]


def test_a_broken_pattern_names_its_target_at_the_break():
    bars = _sym()
    for b in bars[-2:]:
        b.update(close=125.0, high=125.5, open=124.0)
    p = patterns.find(bars, K, 1.0)[0]
    assert p["state"] == "broke_up"
    assert "이미 위로 깼어요" in p["explain"] and f"{p['targets']['up']:,.2f}" in p["explain"]
    # the target is the starting height added at the last bars (the break), as patterns_bt measures the reach
    up_now, low_now = p["upper"]["to"]["price"], p["lower"]["to"]["price"]
    assert p["targets"]["up"] > up_now and p["targets"]["down"] < low_now


def test_the_future_times_read_every_bar_given_not_only_the_pattern(monkeypatch):
    bars = _zigzag(lambda i: 120 - 0.06 * i, lambda i: 90 + 0.06 * i, 200)
    seen = []
    real = patterns.future_times
    monkeypatch.setattr(patterns, "future_times", lambda b, n, daily=False: seen.append(len(b)) or real(b, n, daily))
    got = patterns.find(bars, K, 1.0)
    assert got and seen == [200]                                       # not the LOOKBACK (150) the lines use


def _hours(days, first_hour):
    """60m bars of a US session (7 a day) on the given days, opening at first_hour:30 UTC."""
    from datetime import UTC, datetime

    return [{"at": datetime(d.year, d.month, d.day, first_hour + h, 30, tzinfo=UTC).isoformat()}
            for d in days for h in range(7)]


def test_a_clock_change_leaves_the_old_hours_out_and_a_short_history_fills_the_weekdays():
    edt = [date(2026, 10, d) for d in (26, 27, 28, 29, 30)]
    est = [date(2026, 11, d) for d in (2, 3, 4, 5, 6)]
    nxt = patterns.future_times(_hours(edt, 13) + _hours(est, 14), 8)
    assert nxt[0] == "2026-11-09T14:30:00+00:00" and nxt[6] == "2026-11-09T20:30:00+00:00"
    assert nxt[7] == "2026-11-10T14:30:00+00:00"                       # 7 a day, no 13:30 left over
    two = _hours([date(2026, 11, 10), date(2026, 11, 11)], 14)         # only a Tuesday and a Wednesday
    assert patterns.future_times(two, 1) == ["2026-11-12T14:30:00+00:00"]   # Thursday, not next Tuesday



def test_a_few_hours_of_one_minute_bars_carry_on_the_same_day():
    from datetime import UTC, datetime

    tue = [datetime(2026, 11, 17, 20, 10, tzinfo=UTC) + timedelta(minutes=m) for m in range(50)]     # 20:10-20:59
    wed = [datetime(2026, 11, 18, 14, 30, tzinfo=UTC) + timedelta(minutes=m) for m in range(150)]    # 14:30-16:59
    bars = [{"at": t.isoformat()} for t in tue + wed]
    nxt = patterns.future_times(bars, 3)
    assert nxt == ["2026-11-18T17:00:00+00:00", "2026-11-18T17:01:00+00:00", "2026-11-18T17:02:00+00:00"]
    later = patterns.future_times(bars, 241)                            # 17:00-20:59 (240), then Thursday's open
    assert later[239] == "2026-11-18T20:59:00+00:00" and later[240] == "2026-11-19T14:30:00+00:00"


def test_the_odds_by_the_trend_before_and_the_verdict_said_as_measured(monkeypatch):
    base = {"n": 41, "up_pct": 58.0, "down_pct": 42.0, "none_pct": 0.0, "false_break_pct": 30.0,
            "verdict": "edge", "edge_pct": 9.0,
            "by_prior": {"up": {"n": 35, "up_pct": 70.0, "down_pct": 30.0, "none_pct": 0.0}}}
    monkeypatch.setattr(patterns, "_prior_trend", lambda bars, start, n=20: "up")
    monkeypatch.setattr(patterns, "_stats_cache", {"kinds": {"1d:symmetric": base}})
    p = _find(_sym(70))[0]
    assert (p["odds"]["prior"], p["odds"]["up_pct"], p["odds"]["n"]) == ("up", 70.0, 35)
    assert "과거 앞선 상승 추세에서 위 70% · 아래 30%(표본 35)." in p["explain"]
    for verdict, said in (("unstable", "과거 구간마다 결과가 달라"), ("unmeasured", "비교는 아직 없어요"),
                          ("no_edge", "무작위로 섞어도 비슷해서")):
        monkeypatch.setattr(patterns, "_stats_cache", {"kinds": {"1d:symmetric": {**base, "verdict": verdict}}})
        assert said in _find(_sym(70))[0]["explain"]


def test_the_backtest_records_where_the_break_came_and_whether_the_move_was_reached(monkeypatch):
    from desk import patterns_bt

    monkeypatch.setattr(patterns_bt, "MIN_N", 4)

    bars = _zigzag(lambda i: 120.0, lambda i: 90 * 1.002 ** i, 160)
    for i, b in enumerate(bars[100:], start=100):       # a run steep enough to carry past the starting height
        c = 121.0 + (i - 100) * 2.0
        b.update(open=c, high=c + 0.3, low=c - 0.3, close=c)
    r = patterns_bt.instances(bars, "1d", K, 5, "T")[0]
    assert 0 < r["frac"] <= 1.05 and r["prior"] in ("up", "down", "none")
    assert r["reach"] is True                                          # the run carried past the starting height
    rows = [{**r, "at": f"2024-01-{d:02d}", "frac": 0.4 + d / 100, "reach": d % 2 == 0,
             "prior": "up" if d < 15 else "down"} for d in range(1, 31)]
    s = patterns_bt.summarize(rows)
    assert s["frac_q25"] < s["frac_q50"] < s["frac_q75"] and s["reach_pct"] == 50.0 and s["reach_n"] == 30
    assert set(s["by_prior"]) == {"up", "down"} and s["by_prior"]["up"]["n"] == 14


def test_a_starting_height_too_wide_gives_way_to_the_recent_width_or_no_target(monkeypatch):
    """(#629 follow-up) 005930 1d began in a 94% swing: targets 431k / 91k. Past MAX_HEIGHT_ATR (or
    MAX_HEIGHT_PCT) the width at the last touch stands in; too wide as well, the targets are left out."""
    monkeypatch.setattr(patterns, "_stats_cache", {"kinds": {"1d:symmetric": {"n": 40, "reach_pct": 30.0}}})
    p = patterns.find(_sym(70), K, 2.5)[0]                    # a 30-point height is ~12 ATR of 2.5
    t = p["targets"]
    assert t["basis"] == "recent_swing" and t["reach_pct"] is None   # the measured reach was for the full height
    full = 1 + t["height_pct"] / 100                          # the starting height it stands in for
    assert p["upper"]["to"]["price"] < t["up"] < p["upper"]["to"]["price"] * full
    assert p["lower"]["to"]["price"] / full < t["down"] < p["lower"]["to"]["price"]
    assert "최근 폭 기준" in p["explain"] and "목표까지 간 비율" not in p["explain"]
    monkeypatch.setattr(patterns, "MAX_HEIGHT_ATR", 0.5)      # the recent width too wide as well
    p = patterns.find(_sym(70), K, 2.5)[0]
    assert (p["targets"]["up"], p["targets"]["down"], p["targets"]["basis"]) == (None, None, "omitted")
    assert "너무 넓어 목표는 생략했어요." in p["explain"] and "꼭짓점 전" in p["explain"]
    assert p["explain"].endswith("예측 아님.")
