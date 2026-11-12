"""#297: converging patterns (triangles, wedges) from swing highs and lows."""

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
