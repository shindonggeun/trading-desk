import asyncio
import math
from datetime import date, timedelta

from desk import horizons, sr, trend
from tests.test_sr import _source

START = date(2026, 1, 1)


def _bar(x: int, close: float, spread: float = 0.005) -> dict:
    return {"at": f"{START + timedelta(days=x)}T00:00:00+00:00", "open": close,
            "high": close * (1 + spread), "low": close * (1 - spread), "close": close, "volume": 1000.0}


def _wave(x: int, period: int = 20, depth: float = 0.06) -> float:
    """0 at multiples of ``period``, ``depth`` half-way between."""
    return depth * (1 - math.cos(2 * math.pi * x / period)) / 2


def _rising(n: int = 100) -> list[dict]:
    """Lows touching a line rising 0.2% a bar every 20 bars (bars 20, 40, 60, 80)."""
    return [_bar(x, 100 * math.exp(0.002 * x) * (1 + _wave(x))) for x in range(n)]


def _falling(n: int = 100) -> list[dict]:
    """Highs touching a line falling 0.2% a bar every 20 bars."""
    return [_bar(x, 200 * math.exp(-0.002 * x) * (1 - _wave(x))) for x in range(n)]


def _line_at(x: int) -> float:
    return 100 * math.exp(0.002 * x) * 0.995


def test_rising_support_through_the_swing_lows_is_found_and_tested():
    bars = _rising()
    atr = sr._atr(bars)
    lines = trend.trendlines(bars, "1d", 3, bars[-1]["close"], atr)
    support = [t for t in lines if t["kind"] == "support"]
    assert support, lines
    best = support[0]
    # the rise pulls each low a bar early: bars 19, 39, 59, 79
    assert [t[:10] for t in best["touches"]] == [str(START + timedelta(days=x)) for x in (19, 39, 59, 79)]
    assert abs(best["price_now"] - _line_at(99)) < 0.3
    assert best["slope_pct_per_bar"] == round((math.exp(0.002) - 1) * 100, 3)
    assert best["state"] == "testing" and abs(best["distance_atr"]) <= trend.TEST_ATR
    assert best["end"]["at"] == bars[-1]["at"] and len(best["anchors"]) == 2
    assert not [t for t in lines if t["kind"] == "resistance"]   # nothing falls here


def test_state_follows_the_price_above_on_and_through_the_line():
    bars = _rising()
    atr = sr._atr(bars)
    line = _line_at(99)
    above = trend.trendlines(bars, "1d", 3, line + 2 * atr, atr)
    assert above[0]["state"] == "intact" and above[0]["distance_atr"] > 1.5
    # the last two closes 3% under the line: closed through it
    broken_bars = bars[:-2] + [_bar(x, _line_at(x) * 0.97) for x in (98, 99)]
    broken = trend.trendlines(broken_bars, "1d", 3, broken_bars[-1]["close"], atr)
    assert broken and broken[0]["kind"] == "support" and broken[0]["state"] == "broken"
    assert broken[0]["distance_atr"] < 0


def test_a_line_closed_through_in_the_middle_is_not_a_trendline():
    bars = _rising()
    for x in range(49, 52):
        bars[x] = _bar(x, _line_at(x) * 0.95)
    atr = sr._atr(bars)
    assert any(t["touches"][0].startswith(str(START + timedelta(days=19)))
               for t in trend.trendlines(_rising(), "1d", 3, _rising()[-1]["close"], atr))   # the line exists unbroken
    lines = trend.trendlines(bars, "1d", 3, bars[-1]["close"], atr)
    assert all(not t["touches"][0].startswith(str(START + timedelta(days=19))) for t in lines)


def test_falling_resistance_and_too_few_touches():
    bars = _falling()
    lines = trend.trendlines(bars, "1d", 3, bars[-1]["close"], sr._atr(bars))
    resistance = [t for t in lines if t["kind"] == "resistance"]
    assert resistance and resistance[0]["slope_pct_per_bar"] < 0 and len(resistance[0]["touches"]) >= 3
    # two swings are a line through two points, not a trend
    assert trend.trendlines(_rising(50), "1d", 3, 100.0, 1.0) == []
    assert trend.trendlines(_rising()[:10], "1d", 3, 100.0, 1.0) == []
    assert trend.trendlines(_rising(), "1d", 3, 100.0, 0.0) == []


def test_fibonacci_levels_of_a_rise_and_a_fall():
    rise = [_bar(0, 100.0, 0), _bar(1, 150.0, 0), _bar(2, 200.0, 0), _bar(3, 180.0, 0)]
    fib = trend.fibonacci(rise)
    assert fib["direction"] == "up" and fib["from"]["price"] == 100.0 and fib["to"]["price"] == 200.0
    assert {lv["ratio"]: lv["price"] for lv in fib["levels"]} == {
        0.0: 200.0, 0.25: 175.0, 0.382: 161.8, 0.5: 150.0, 0.618: 138.2, 0.786: 121.4, 1.0: 100.0}
    fall = list(reversed(rise))
    fib = trend.fibonacci(fall)
    assert fib["direction"] == "down" and fib["from"]["price"] == 200.0
    assert next(lv["price"] for lv in fib["levels"] if lv["ratio"] == 0.382) == 138.2
    assert trend.fibonacci([_bar(0, 100.0, 0)]) is None
    assert trend.fibonacci([_bar(0, 100.0, 0), _bar(1, 100.0, 0)]) is None


def test_a_fibonacci_level_on_a_zone_carries_it():
    rise = [_bar(0, 100.0, 0), _bar(1, 200.0, 0)]
    zones = [{"price": 151.0, "strength": 3.0, "basis": "일봉 스윙 저점"},
             {"price": 149.5, "strength": 5.0, "basis": "50일선"},
             {"price": 115.0, "strength": 9.0, "basis": "주봉 스윙 저점"}]
    levels = {lv["ratio"]: lv for lv in trend.fibonacci(rise, zones, tolerance=2.0)["levels"]}
    assert levels[0.5]["confluence"] == {"price": 149.5, "strength": 5.0, "basis": "50일선"}
    assert "confluence" not in levels[0.382] and "confluence" not in levels[0.786]


def test_prompt_note_reads_lines_and_retracement_and_rides_on_the_levels_note():
    bars = _rising()
    result = sr.compute(bars, [])
    sr.add_trend(result, bars, [], horizons.get(None), None)
    assert result["trendlines"] and result["fibonacci"] and "trend_error" not in result
    note = trend.prompt_note(result)
    assert "Rising support (1d, short swing)" in note and "testing it" in note and "Fibonacci retracement of the rise" in note
    assert "both scenarios" in note
    assert note in sr.prompt_note(result)
    assert trend.prompt_note(None) is None and trend.prompt_note({"trendlines": [], "fibonacci": None}) is None
    # no horizontal levels but a line: the line still reaches the agents
    assert sr.prompt_note({"levels": [], "trendlines": result["trendlines"]}) == trend.prompt_note(
        {"trendlines": result["trendlines"]})


def test_a_trend_failure_leaves_the_levels(monkeypatch):
    bars = _rising()
    result = sr.compute(bars, [])
    levels = list(result["levels"])

    def boom(*_args, **_kw):
        raise ZeroDivisionError("bad bars")
    monkeypatch.setattr(trend, "trendlines", boom)
    sr.add_trend(result, bars, [], horizons.get(None), None)
    assert result["levels"] == levels and result["trendlines"] == [] and result["fibonacci"] is None
    assert result["trend_error"] == "ZeroDivisionError: bad bars"


def test_levels_response_carries_trendlines_and_fibonacci():
    sr._CACHE.clear()
    source, _ = _source()
    result = asyncio.run(sr.support_resistance(source, "NVDA"))
    assert "trendlines" in result and isinstance(result["trendlines"], list)
    assert result["fibonacci"]["levels"][0]["ratio"] == 0.0


def test_a_close_through_the_price_has_taken_back_is_not_a_break():
    bars = _rising()
    atr = sr._atr(bars)
    bars[97] = _bar(97, _line_at(97) - 0.4 * atr)                 # closed through …
    bars[98] = _bar(98, _line_at(98) + 1.0 * atr)                 # … and back above
    bars[99] = _bar(99, _line_at(99) + 1.2 * atr)
    line = trend.trendlines(bars, "1d", 3, bars[-1]["close"], atr)[0]
    assert line["kind"] == "support" and line["state"] == "intact" and line["distance_atr"] > 1
    # a live price back on the line after a close through it is a test, not yet a break
    bars[99] = _bar(99, _line_at(99) - 0.4 * atr)
    line = trend.trendlines(bars, "1d", 3, _line_at(99) - 0.1 * atr, atr)[0]
    assert line["state"] == "testing"
    assert trend.trendlines(bars, "1d", 3, _line_at(99) - 0.4 * atr, atr)[0]["state"] == "broken"


def test_falling_resistance_broken_on_a_close_above():
    bars = _falling()
    atr = sr._atr(bars)
    line_now = 200 * math.exp(-0.002 * 99) * 1.005
    bars[99] = _bar(99, line_now + 2 * atr)
    lines = [t for t in trend.trendlines(bars, "1d", 3, bars[-1]["close"], atr) if t["kind"] == "resistance"]
    assert lines and lines[0]["state"] == "broken" and lines[0]["distance_atr"] > 0


def test_a_weekly_line_is_judged_on_weekly_volatility_and_distance_is_in_daily_atr():
    bars = _rising()
    weekly_atr = sr._atr(bars)
    daily_atr = weekly_atr / 2.5
    price = _line_at(99) - 0.2 * weekly_atr                        # inside the line's own touch band
    line = trend.trendlines(bars, "1w", 3, price, daily_atr, weekly_atr)[0]
    assert line["state"] == "testing"
    assert abs(line["distance_atr"] - (price - line["price_now"]) / daily_atr) < 0.02
    # far in daily ATR but near in the line's own: kept
    assert trend.trendlines(bars, "1w", 3, _line_at(99) + 6 * weekly_atr, daily_atr, weekly_atr)
    # #530: past MAX_DISTANCE_ATR (own ATR) an unbroken line stays, marked far; past MAX_FAR_ATR it goes
    far = trend.trendlines(bars, "1w", 3, _line_at(99) + 9 * weekly_atr, daily_atr, weekly_atr)
    assert [t["status"] for t in far] == ["far"] and far[0]["state"] == "intact"
    assert trend.trendlines(bars, "1w", 3, _line_at(99) + 26 * weekly_atr, daily_atr, weekly_atr) == []


def test_extend_projects_the_line_to_today():
    bars = _rising()
    atr = sr._atr(bars)
    base = trend.trendlines(bars, "1w", 3, bars[-1]["close"], atr)[0]
    ahead = trend.trendlines(bars, "1w", 3, bars[-1]["close"], atr, extend=1.5, now_at="2026-05-01T00:00:00+00:00")[0]
    assert abs(ahead["price_now"] - base["price_now"] * math.exp(0.002 * 1.5)) < 0.02
    assert ahead["end"] == {"at": "2026-05-01T00:00:00+00:00", "price": ahead["price_now"]}
    assert sr._bars_behind("2026-10-26T00:00:00+00:00", "2026-11-05T00:00:00+00:00", 7) == 10 / 7
    assert sr._bars_behind("2026-10-30T00:00:00+00:00", "2026-11-02T00:00:00+00:00", 1) == 1.0
    assert sr._bars_behind("2026-11-02T00:00:00+00:00", "2026-11-02T00:00:00+00:00", 7) == 0.0


def test_near_duplicate_lines_are_one_and_each_kind_keeps_two():
    lines = [trend._Line("support", 0, 10, 0.0, 0.001, [0, 10, 20]),
             trend._Line("support", 1, 10, 0.0001, 0.001, [1, 10, 20]),      # the same line, one bar on
             trend._Line("support", 0, 30, -0.05, 0.002, [0, 30, 40, 50]),
             trend._Line("support", 0, 30, 0.1, 0.0005, [0, 30, 60])]
    kept = trend._pick(lines, 100, 0.01)
    assert len(kept) == trend.PER_KIND and kept[0].touches == [0, 30, 40, 50]
    assert sum(1 for k in kept if k.touches[-1] == 20) <= 1


def test_one_bar_with_both_extremes_has_no_retracement():
    assert trend.fibonacci([_bar(0, 100.0, 0.5), _bar(1, 100.0, 0.1)]) is None


def test_the_swing_fallback_and_cache_keep_the_lines():
    from tests.test_horizons import _Prices
    sr._CACHE.clear()
    first = asyncio.run(sr.support_resistance(_Prices([]), "NVDA", horizon="scalp"))
    assert first["fallback"] and "trendlines" in first and first["fibonacci"]
    again = asyncio.run(sr.support_resistance(_Prices([]), "NVDA", horizon="scalp"))
    assert again is first


# ------------------------------------------------------------------ #530 scales, far lines, scenarios

_S1 = math.log(250 / 300) / 60


def _l1(x: float) -> float:
    """A long falling line through two big tops (bars 20 and 80)."""
    return 300 * math.exp(_S1 * (x - 20))


def _two_scales(n: int = 200) -> list[dict]:
    """Two big tops on a long falling line, then from bar 110 a gentler slide whose small tops
    every 15 bars make a short falling line well under the long one."""
    out = []
    for x in range(n):
        if x < 110:
            p = _l1(x) * (1 - _wave(x - 20, 60, 0.2))
        else:
            p = 0.8 * _l1(110) * math.exp(-0.002 * (x - 110)) * (1 - _wave(x - 110, 15, 0.05))
        out.append(_bar(x, p, 0.01))
    return out


def test_short_and_long_swings_give_different_lines_and_a_far_one_stays_with_its_rise():
    bars = _two_scales()
    atr = sr._atr(bars)
    ref = bars[-1]["close"]
    lines = trend.trendlines(bars, "1d", 3, ref, atr, scales=trend.SCALES)
    assert {t["scale"] for t in lines} == {"short", "long"} and all(t["kind"] == "resistance" for t in lines)
    long_ = next(t for t in lines if t["scale"] == "long")
    assert long_["swing_k"] == 12 and len(long_["touches"]) == 2
    assert long_["id"] == f"1d-resistance-{long_['anchors'][0]['at']}-{long_['anchors'][1]['at']}"
    assert abs(long_["price_now"] - _l1(199)) / _l1(199) < 0.02
    assert long_["distance_pct"] == round((long_["price_now"] / ref - 1) * 100, 2) > 10   # the rise it needs
    # 7 ATR over the price: a big move away, marked far; the short line just above is near
    assert long_["status"] == "far" and long_["state"] == "intact" and long_["distance_atr"] < -trend.NEAR_ATR
    assert next(t for t in lines if t["scale"] == "short")["status"] == "near"
    # 12 ATR away, past where lines used to be dropped: still there
    low = ref - 5 * atr
    far = next(t for t in trend.trendlines(bars, "1d", 3, low, atr, scales=trend.SCALES) if t["scale"] == "long")
    assert far["status"] == "far" and far["distance_atr"] < -trend.MAX_DISTANCE_ATR
    # the short scale alone does not see the long line
    assert {t["scale"] for t in trend.trendlines(bars, "1d", 3, ref, atr)} == {"short"}


def test_a_line_every_scale_finds_is_one_line_at_its_shortest_scale():
    bars = _falling()
    atr = sr._atr(bars)
    one = trend.trendlines(bars, "1d", 3, bars[-1]["close"], atr)
    assert trend.trendlines(bars, "1d", 3, bars[-1]["close"], atr, scales=(trend.SCALES[1],))   # mid finds it too
    every = trend.trendlines(bars, "1d", 3, bars[-1]["close"], atr, scales=trend.SCALES)
    assert len(every) == 1 and every[0]["scale"] == "short" and every[0]["anchors"] == one[0]["anchors"]


def test_the_line_ahead_and_the_status_bands():
    bars = _rising()
    atr = sr._atr(bars)
    line = _line_at(99)
    t = trend.trendlines(bars, "1d", 3, line + 2 * atr, atr)[0]
    for a in t["ahead"]:
        assert abs(a["price"] - _line_at(99 + a["bars"])) < 0.3
        assert abs(a["distance_pct"] - (a["price"] / (line + 2 * atr) - 1) * 100) < 0.01
    assert [a["bars"] for a in t["ahead"]] == list(trend.AHEAD)
    on = t["price_now"]
    status = {d: [x["status"] for x in trend.trendlines(bars, "1d", 3, on + d * atr, atr)]
              for d in (0.3, 2.9, 3.1, 9, 26)}
    assert status == {0.3: ["testing"], 2.9: ["near"], 3.1: ["far"], 9: ["far"], 26: []}
    broken_bars = bars[:-2] + [_bar(x, _line_at(x) * 0.97) for x in (98, 99)]
    assert trend.trendlines(broken_bars, "1d", 3, broken_bars[-1]["close"], atr)[0]["status"] == "broken"


def _fake(kind: str, tf: str, scale: str, pct: float, status: str = "near") -> dict:
    return {"kind": kind, "timeframe": tf, "scale": scale, "distance_pct": pct, "status": status}


def test_select_keeps_each_scale_then_the_nearest_and_far_lines_only_fill_what_is_left():
    lines = ([_fake("resistance", "1d", "short", p) for p in (1, 2, 3)]
             + [_fake("resistance", "1d", "mid", 4), _fake("resistance", "1w", "mid", 12)]
             + [_fake("resistance", tf, sc, p, "far") for tf, sc, p in (("1d", "long", 30), ("1w", "short", 40))]
             + [_fake("support", "1d", "short", -p) for p in (1, 2)]
             + [_fake("support", tf, sc, p, "far") for tf, sc, p in
                (("1d", "short", -20), ("1d", "short", -21), ("1d", "long", -30), ("1w", "long", -40))])
    kept = trend.select(lines)
    res = [t for t in kept if t["kind"] == "resistance"]
    sup = [t for t in kept if t["kind"] == "support"]
    # five near resistance lines: no far one takes a near one's place
    assert [t["distance_pct"] for t in res] == [1, 2, 3, 4, 12]
    # two near supports, then the far ones - one per timeframe and scale before the nearest rest
    assert [t["distance_pct"] for t in sup] == [-1, -2, -20, -30]
    assert kept.index(sup[0]) < kept.index(res[0])                 # support first
    spread = trend.select([_fake("resistance", "1d", "short", p) for p in (1, 2, 3, 4, 5)]
                          + [_fake("resistance", "1w", "long", 9)])
    assert [t["distance_pct"] for t in spread] == [1, 2, 3, 4, 9]   # a weekly long line is not crowded out


def test_scenarios_take_their_targets_from_the_zones_and_lines_around():
    bars = _falling()
    atr = sr._atr(bars)
    ref = bars[-1]["close"]
    t = trend.trendlines(bars, "1d", 3, ref, atr)[0]
    up, now = round(t["price_now"] + trend.BREAK_ATR * atr, 2), t["price_now"]
    levels = [{"price": round(up * 1.01, 2), "timeframe": "1d"}, {"price": round(min(now, ref) * 0.997, 2), "timeframe": "1w"}]
    trend.annotate([t], levels, ref, {"1d": {"bars": bars, "k": 3, "atr": atr}})
    out, back = t["scenarios"]
    assert out["kind"] == "breakout" and abs(out["trigger"] - up) <= 0.01 and out["invalid"] == now
    assert out["volume_min"] == 1000 and out["target"] == levels[0]["price"] and out["target_basis"] == "일봉 저항 구역"
    assert back["kind"] == "reject" and back["trigger"] == now and back["invalid"] == out["trigger"]
    assert back["target"] == levels[1]["price"] and back["target_basis"] == "주봉 지지 구역" and back["volume_min"] is None
    assert len(t["summary"]) == 3 and "짧은 스윙 하락 저항선" in t["summary"][0]
    assert f"{levels[0]['price']:,.2f}" in out["text"] and "거래량 20봉 평균(1,000)" in out["text"]


def test_support_scenarios_bounce_and_break_down_to_the_swing_lows():
    bars = _rising()
    atr = sr._atr(bars)
    ref = _line_at(99) + 2 * atr
    t = trend.trendlines(bars, "1d", 3, ref, atr)[0]
    trend.annotate([t], [], ref, {"1d": {"bars": [{**b, "volume": 0} for b in bars], "k": 3, "atr": atr}})
    bounce, down = t["scenarios"]
    assert bounce["kind"] == "bounce" and down["kind"] == "breakdown"
    assert down["trigger"] < bounce["trigger"] == t["price_now"] == down["invalid"]
    assert down["volume_min"] is None and "거래량" not in down["text"]       # no volume, no volume bar
    # under a rising line the earlier, lower swing lows are where a break goes; a bounce goes to the last top
    lows = [b["low"] for b in bars if b["low"] < down["trigger"]]
    assert down["target_basis"] == "일봉 앞 저점" and down["target"] in [round(x, 2) for x in lows]
    assert bounce["target_basis"] == "일봉 앞 고점" and bounce["target"] == round(max(b["high"] for b in bars), 2)
    # nothing past a line: the sentence says so instead of a number
    trend.annotate([t], [], ref, {"1d": {"bars": [{**b, "high": min(b["high"], ref)} for b in bars], "k": 3, "atr": atr}})
    assert t["scenarios"][0]["target"] is None and "위로 가까운 목표가 없다" in t["scenarios"][0]["text"]


def test_the_levels_carry_scenarios_and_the_note_reads_only_the_nearest_lines():
    bars = _two_scales()
    result = sr.compute(bars, [])
    sr.add_trend(result, bars, [], horizons.get(None), None)
    assert result["trendlines"] and all(t["scenarios"] and t["summary"] for t in result["trendlines"])
    many = [{**result["trendlines"][0], "distance_atr": d, "price_now": 100 + d} for d in (5, -1, 3, 0.5, -8)]
    note = trend.prompt_note({"trendlines": many})
    assert note.count("\n- ") + note.startswith("- ") == trend.PROMPT_LINES
    assert "at 100.5 now" in note and "at 99 now" in note and "at 103 now" in note and "at 105 now" not in note


def test_the_playbook_builds_zones_from_near_lines_only():
    from desk import playbook

    bars = _rising()
    lines = [{"kind": "resistance", "state": "intact", "status": "far", "price_now": p, "distance_bar_atr": d}
             for p, d in ((130.0, -7.9), (190.0, -8.1))] + [{"kind": "support", "state": "testing", "price_now": 120.0}]
    trend_prices = [c["price"] for c in playbook.candidates({"trendlines": lines}, bars, False) if c["source"] == "trend"]
    assert trend_prices == [130.0, 120.0]   # as far as lines used to reach; a line without a distance counts


def test_a_broken_support_is_overhead_and_its_scenarios_are_retaking_it():
    bars = _rising()
    atr = sr._atr(bars)
    broken_bars = bars[:-2] + [_bar(x, _line_at(x) * 0.97) for x in (98, 99)]
    t = trend.trendlines(broken_bars, "1d", 3, broken_bars[-1]["close"], atr)[0]
    assert t["kind"] == "support" and t["status"] == "broken" and t["distance_pct"] > 0
    trend.annotate([t], [], broken_bars[-1]["close"], {"1d": {"bars": broken_bars, "k": 3, "atr": atr}})
    assert [s["kind"] for s in t["scenarios"]] == ["breakout", "reject"]
    assert t["scenarios"][0]["trigger"] > t["price_now"] and "깨고 내려갔다" in t["summary"][0]
    assert "기다리면" not in t["summary"][1]                       # a rising line does not come down to the price


def test_intraday_lines_have_their_own_ids():
    from datetime import datetime

    start = datetime(2026, 11, 17, 9, 0)
    bars = [{**b, "at": f"{(start + timedelta(minutes=x)).isoformat()}+00:00"} for x, b in enumerate(_two_scales())]
    lines = trend.trendlines(bars, "1m", 3, bars[-1]["close"], sr._atr(bars), scales=trend.SCALES)
    assert len(lines) == 2 and len({t["id"] for t in lines}) == 2


def _flat_line(price: float, ref: float, line_id: str) -> dict:
    return {"id": line_id, "kind": "resistance", "timeframe": "1d", "scale": "short", "status": "far",
            "price_now": price, "distance_pct": round((price / ref - 1) * 100, 2), "distance_atr": (ref - price) / 2,
            "slope_pct_per_bar": -0.1, "ahead": [{"bars": a, "price": price, "distance_pct": 0} for a in trend.AHEAD]}


def test_another_line_is_a_target_and_a_line_is_never_its_own():
    flat = [_bar(x, 100.0) for x in range(60)]
    # the same id on purpose: a line is left out of its own targets by what it is, not by its id
    near, far = _flat_line(110.0, 100.0, "same"), _flat_line(120.0, 100.0, "same")
    trend.annotate([near, far], [], 100.0, {"1d": {"bars": flat, "k": 3, "atr": 2.0}})
    assert near["scenarios"][0]["target"] == 120.0 and near["scenarios"][0]["target_basis"] == "일봉 하락 저항선"
    assert far["scenarios"][0]["target"] is None
    assert far["scenarios"][1]["target"] == 110.0 or far["scenarios"][1]["target"] < 100.0


def test_a_broken_resistance_is_underneath_and_a_far_broken_line_goes():
    bars = _falling()
    atr = sr._atr(bars)
    line_now = 200 * math.exp(-0.002 * 99) * 1.005
    bars[99] = _bar(99, line_now + 2 * atr)
    t = next(t for t in trend.trendlines(bars, "1d", 3, bars[-1]["close"], atr) if t["kind"] == "resistance")
    assert t["status"] == "broken" and t["distance_pct"] < 0
    trend.annotate([t], [], bars[-1]["close"], {"1d": {"bars": bars, "k": 3, "atr": atr}})
    assert [s["kind"] for s in t["scenarios"]] == ["bounce", "breakdown"] and "뚫고 올라섰다" in t["summary"][0]
    # 9 ATR over the broken line: dropped, as before; an unbroken line that far stays
    assert not [x for x in trend.trendlines(bars, "1d", 3, line_now + 9 * atr, atr) if x["kind"] == "resistance"]
    rising = _rising()
    assert trend.trendlines(rising, "1d", 3, _line_at(99) + 9 * atr, atr)[0]["status"] == "far"


def test_add_trend_reads_both_timeframes_and_the_backtest_skips_the_sentences():
    from tests.test_backtest import _weekly

    bars = _two_scales()
    weeks = _weekly(bars)
    result = sr.compute(bars, weeks)
    sr.add_trend(result, bars, weeks, horizons.get(None), None)
    lines = result["trendlines"]
    assert "trend_error" not in result and lines and all(t["scenarios"] and t["summary"] for t in lines)
    assert len({t["id"] for t in lines}) == len(lines)
    for kind in ("support", "resistance"):
        assert len([t for t in lines if t["kind"] == kind]) <= trend.MAX_PER_KIND[kind]
    bare = sr.compute(bars, weeks)
    sr.add_trend(bare, bars, weeks, horizons.get(None), None, annotate=False)
    assert [t["id"] for t in bare["trendlines"]] == [t["id"] for t in lines]
    assert not any("scenarios" in t for t in bare["trendlines"])


def test_the_volume_bar_is_the_finished_bars_before_the_last():
    bars = [{**b, "volume": 1000.0} for b in _falling()]
    bars[-1] = {**bars[-1], "volume": 10.0}          # today, still forming
    assert trend._frame(bars, 3)[0] == 1000.0
