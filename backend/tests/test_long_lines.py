"""#679 long trendlines: the support a person draws under months of widely spaced lows, its touches,
false breaks (closes that came back - a wick never counts) and the easy sentence."""

import json
import math
from datetime import date, timedelta
from pathlib import Path

from desk import long_lines

FIX = Path(__file__).parent / "fixtures" / "long_lines_2026-11-19.json"


def _fix(ticker: str, key: str = "daily") -> list[dict]:
    return json.loads(FIX.read_text(encoding="utf-8"))[ticker][key]


def test_amd_daily_finds_the_may_july_september_support_tested_today():
    # the issue's picture: May low → July → September, a two-close dip in early November that came back,
    # and today (-7.7%) back on the line - its fourth touch
    got = [ln for ln in long_lines.lines(_fix("AMD"), "1d") if ln["kind"] == "support"]
    assert len(got) == 1
    ln = got[0]
    assert [t[:10] for t in ln["touches"]] == ["2026-05-19", "2026-07-29", "2026-09-03", "2026-11-19"]
    assert [p["at"] for p in ln["touch_points"]] == ln["touches"] and ln["touch_points"][0]["price"] == 393.36
    assert [(f["from"][:10], f["to"][:10], f["closes"]) for f in ln["false_breaks"]] == \
        [("2026-11-05", "2026-11-06", 2)]
    assert ln["state"] == "testing" and ln["status"] == "testing" and ln["long"] is True
    assert ln["id"].startswith("1d-long-support-") and ln["since"][:10] == "2026-05-19"
    assert "5월 19일부터 4번 닿았어요" in ln["explain"] and "거짓 이탈 1번" in ln["explain"]
    assert "오늘 -7.7% 내려 장기 지지선" in ln["explain"] and "시험 중" in ln["explain"]
    # the /api/lines trendline fields stay as they are (desk.channels' shape)
    for key in ("timeframe", "kind", "anchors", "end", "touches", "slope_pct_per_bar", "state", "broken_at",
                "broken_price", "fresh_break", "distance_atr", "explain"):
        assert key in ln


def test_soxx_weekly_support_under_this_weeks_drop():
    got = long_lines.lines(_fix("SOXX", "weekly"), "1w")
    sup = next(ln for ln in got if ln["kind"] == "support")
    assert len(sup["touches"]) >= 4 and sup["state"] == "testing" and "이번 주" in sup["explain"]


def _bars(n: int, closes_off: dict[int, float] | None = None, lows_off: dict[int, float] | None = None) -> list[dict]:
    """A rising support (log 100 + 0.002 a bar) the price swings above, back to it every 50 bars."""
    out = []
    day = date(2025, 1, 1)
    for x in range(n):
        line = math.log(100) + 0.002 * x
        d = 0.10 * (1 - math.cos(2 * math.pi * x / 50)) / 2
        c = math.exp(line + d + (closes_off or {}).get(x, 0.0))
        lo = c * 0.99 if d > 0.002 else math.exp(line) * 0.995
        lo = min(lo, c * 0.99) * math.exp((lows_off or {}).get(x, 0.0))
        out.append({"at": (day + timedelta(days=x)).isoformat() + "T05:00:00+00:00", "open": c, "high": c * 1.01,
                    "low": lo, "close": c, "volume": 1.0})
    return out


def test_a_wick_far_under_the_line_is_no_false_break():
    bars = _bars(203, lows_off={120: -0.15})          # one bar's low 15% under, its close above
    ln = next(ln for ln in long_lines.lines(bars, "1d") if ln["kind"] == "support")
    assert ln["false_breaks"] == [] and ln["state"] != "broken" and len(ln["touches"]) >= 3


def test_a_short_dip_of_closes_is_a_false_break_and_a_long_one_is_no_line():
    two = _bars(203, closes_off={100: -0.08, 101: -0.08})
    ln = next(ln for ln in long_lines.lines(two, "1d") if ln["kind"] == "support")
    assert [f["closes"] for f in ln["false_breaks"]] == [2]
    six = _bars(203, closes_off={x: -0.08 for x in range(100, 106)})
    assert not [ln for ln in long_lines.lines(six, "1d")
                if ln["kind"] == "support" and ln["touches"][0][:10] < six[100]["at"][:10]]


def test_two_closes_under_it_at_the_end_break_it():
    bars = _bars(202, closes_off={200: -0.08, 201: -0.09})
    ln = next(ln for ln in long_lines.lines(bars, "1d") if ln["kind"] == "support")
    assert ln["state"] == "broken" and ln["broken_at"] == bars[200]["at"]
    assert "종가로 아래로 이탈했어요" in ln["explain"]


def test_too_few_bars_give_nothing():
    assert long_lines.lines(_bars(40), "1d") == []
