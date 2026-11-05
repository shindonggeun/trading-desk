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
    lines = trend.trendlines(bars, "1d", 3, bars[-1]["close"], sr._atr(bars))
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
    assert "Rising support (1d)" in note and "testing it" in note and "Fibonacci retracement of the rise" in note
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
