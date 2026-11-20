"""#695 head and shoulders (and the inverse), the weekly Minsky-style bubble stage, their backtests and where
they show (chart read, trade map)."""

import itertools
import math
from datetime import date, timedelta

from desk import bubble_bt, bubble_stage, chart_read, hs_bt, hs_pattern, trade_map

TOP = [(0, 100), (20, 120), (32, 105), (46, 132), (60, 106), (72, 121), (84, 108), (99, 108.5)]


def _path(points, n):
    out = []
    for i in range(n):
        for (a, pa), (b, pb) in itertools.pairwise(points):
            if a <= i <= b:
                out.append(pa + (pb - pa) * (i - a) / (b - a))
                break
    return out


def _bars(closes, vols=None, start=date(2026, 1, 1)):
    return [{"at": (start + timedelta(days=i)).isoformat() + "T05:00:00+00:00", "open": c, "high": c * 1.004,
             "low": c * 0.996, "close": c, "volume": vols[i] if vols else 1000} for i, c in enumerate(closes)]


def _one(closes, **kw):
    got = hs_pattern.find(_bars(closes, **kw), 3, 1.5)
    return got[0] if got else None


def test_a_textbook_top_is_found_forming_with_its_neckline_and_target(monkeypatch):
    monkeypatch.setattr(hs_pattern, "_stats_cache", {})
    vols = [1000 if i < 60 else 600 for i in range(100)]                    # the right shoulder on less volume
    p = _one(_path(TOP, 100), vols=vols)
    assert p["kind"] == "head_shoulders" and p["state"] == "forming" and p["expected"] == "down"
    pts = p["points"]
    assert pts["head"]["price"] > pts["left_shoulder"]["price"] and pts["head"]["price"] > pts["right_shoulder"]["price"]
    assert p["shoulder_diff_pct"] < 5 and p["volume_right_lower"] is True
    assert p["target"] < p["neckline"]["to"]["price"] < pts["right_shoulder"]["price"]
    assert p["reference_only"] and p["reference_label"] == "참고용 · 과거 통계 없음"
    assert "헤드앤숄더" in p["explain"] and "넥라인" in p["explain"] and p["explain"].endswith("예측 아님.")


def test_states_broken_retest_failed_and_a_close_over_the_head_drops_it(monkeypatch):
    monkeypatch.setattr(hs_pattern, "_stats_cache", {})
    broken = _one(_path(TOP[:-1] + [(90, 100), (99, 96)], 100))
    assert broken["state"] == "broken" and broken["broke_at"]
    retest = _one(_path(TOP[:-1] + [(88, 100), (94, 98), (99, 106.6)], 100))
    assert retest["state"] == "retest"
    failed = _one(_path(TOP[:-1] + [(88, 100), (92, 99), (99, 114)], 100))
    assert failed["state"] == "failed" and "거짓 이탈" in failed["explain"]
    assert hs_pattern.find(_bars(_path(TOP[:-1] + [(92, 125), (99, 136)], 100)), 3, 1.5) == []
    # uneven shoulders or no clear head: nothing
    lopsided = [(0, 100), (20, 104), (32, 101), (46, 132), (60, 106), (72, 128), (84, 108), (99, 108.5)]
    assert all(p["kind"] != "head_shoulders" for p in hs_pattern.find(_bars(_path(lopsided, 100)), 3, 1.5))


def test_the_inverse_is_the_mirror_and_reads_its_record(monkeypatch):
    stats = {"n": 25, "expected_pct": 72.4, "false_break_pct": 31.0, "reach_pct": 25.7, "verdict": "no_edge",
             "edge_pct": 5.2}
    monkeypatch.setattr(hs_pattern, "_stats_cache", {"kinds": {"1d:inverse_head_shoulders": stats}})
    p = _one(_path([(i, 240 - v) for i, v in TOP], 100))
    assert p["kind"] == "inverse_head_shoulders" and p["expected"] == "up" and p["target"] > p["neckline"]["to"]["price"]
    assert p["odds"]["small_sample"] and p["reference_label"] == hs_pattern.NO_EDGE_KO
    assert "표본 25, 표본 적음" in p["explain"] and "목표 도달 26%" in p["explain"] and hs_pattern.NO_EDGE_KO in p["explain"]


def _weeks(closes):
    return [{"at": f"2000-01-01T{i}", "open": c, "high": c * 1.02, "low": c * 0.98, "close": c, "volume": 1000}
            for i, c in enumerate(closes)]


BASE = [100 * 1.003 ** i for i in range(80)]
PARA = BASE + [BASE[-1] * math.exp(0.004 * i * i / 2) for i in range(1, 30)]
PEAK = PARA[-1]
DROP = PARA + [PEAK * (1 - 0.06 * i) for i in range(1, 6)]
TRAP = DROP + [DROP[-1] + (PEAK - DROP[-1]) * 0.15 * i for i in range(1, 5)]
FEAR = TRAP + [TRAP[-1] * 0.93 ** i for i in range(1, 12)]


def test_the_bubble_stages_from_numbers(monkeypatch):
    monkeypatch.setattr(bubble_stage, "_stats_cache", {})
    got = {name: bubble_stage.stage(_weeks(c)) for name, c in
           (("quiet", BASE), ("mania", PARA), ("first_drop", DROP), ("bull_trap", TRAP), ("fear", FEAR))}
    for name, s in got.items():
        assert s["stage"] == name, (name, s["numbers"])
    assert [n for n, s in got.items() if s["caution"]] == ["mania", "first_drop", "bull_trap"]
    m = got["mania"]
    assert m["numbers"]["dist_40w_pct"] > 40 and m["numbers"]["accel"] >= bubble_stage.MANIA_ACCEL
    assert "진입 조심" not in m["explain"] and "들어가기 조심스러운" in m["explain"] and "예측 아님" in m["explain"]
    assert bubble_stage.stage(_weeks(BASE[:50])) is None                        # too short a history


def test_the_bubble_record_says_the_crash_share_against_every_week(monkeypatch):
    rec = {"n": 137, "crash30_pct": 36.1, "base_crash30_pct": 20.2, "verdict": "edge", "edge_pct": 15.9}
    monkeypatch.setattr(bubble_stage, "_stats_cache", {"stages": {"first_drop": rec}})
    s = bubble_stage.stage(_weeks(DROP))
    assert s["stats"] == rec and s["reference_only"] is False and s["reference_label"] is None
    assert "30% 넘게 빠진 경우 36%(모든 주 평균 20%, 표본 137)" in s["explain"]


def test_the_hs_backtest_follows_a_top_to_its_break_and_target():
    closes = _path(TOP[:-1] + [(90, 100), (120, 80), (199, 80)], 200)
    rows = hs_bt.instances(_bars(closes), "1d", 3, 2, "T")
    tops = [r for r in rows if r["kind"] == "head_shoulders"]
    assert len(tops) == 1 and tops[0]["direction"] == "down" and tops[0]["expected"] == "down"
    assert tops[0]["reach"] is True and tops[0]["false_break"] is False


def test_the_bubble_backtest_counts_a_flickering_stage_once_and_judges_against_every_week():
    weeks = _weeks(FEAR + [FEAR[-1] * 1.01 ** i for i in range(60)])
    rows = bubble_bt.walk(weeks, "T")
    starts = [r for r in rows if r["start"]]
    assert {r["stage"] for r in starts} >= {"mania", "first_drop", "fear"}
    assert rows[0]["r13"] is not None and rows[-1]["r13"] is None and rows[-1]["crash30"] is None
    flicker = [{"ticker": "T", "at": f"2000-{i:04d}", "stage": "mania" if i % 2 else "none", "start": True, "r13": 0.1,
                "r26": 0.1, "r52": 0.1, "crash30": i % 3 == 0} for i in range(60)]
    t = bubble_bt.table(flicker)
    assert t["mania"]["n"] == 30 and t["mania"]["base_crash30_pct"] == round(100 * 20 / 60, 1)
    assert t["mania"]["verdict"] in ("edge", "no_edge", "unstable")


def test_the_chart_read_names_the_neckline_and_the_weekly_stage(monkeypatch):
    monkeypatch.setattr(hs_pattern, "_stats_cache", {})
    p = hs_pattern.find(_bars(_path(TOP[:-1] + [(90, 100), (99, 96)], 100)), 3, 1.5)[0]
    frame = {"hs": [p], "patterns": [], "channels": [], "trendlines": []}
    got = chart_read._candidates(frame, _bars(_path(TOP, 100)), "1d", 100.0, [])
    names = [c[1] for c in got]
    assert "헤드앤숄더 넥라인" in names and "헤드앤숄더 목표" in names
    assert any(c[2]["kind"] == "neckline" and c[2]["edge"] == "target" for c in got)
    bubble = bubble_stage.stage(_weeks(PARA))
    text = chart_read.sentence({"timeframe": "1w", "price": 100.0, "trend": {"state": next(iter(chart_read.TREND_KO))}, "levels": {"above": [], "below": []},
                                "patterns": [], "hs": [p], "bubble": bubble, "volume": None, "trendlines": [], "channels": []})
    assert "헤드앤숄더 넥라인 이탈." in text and "주봉 민스키형 '열광 · 포물선' — 진입 조심." in text
    assert chart_read.read("T", [{"summary": "x", "bubble": bubble}])["bubble"]["stage"] == "mania"


def test_the_trade_map_cautions_against_a_long_into_a_top_or_a_bubble(monkeypatch):
    monkeypatch.setattr(hs_pattern, "_stats_cache", {})
    p = hs_pattern.find(_bars(_path(TOP, 100)), 3, 1.5)[0]
    bubble = bubble_stage.stage(_weeks(DROP))
    got = trade_map.cautions({"hs": [p], "bubble": bubble}, ["long"])
    assert [c["kind"] for c in got] == ["bubble", "head_shoulders"] and all(c["text"] for c in got)
    assert trade_map.cautions({"hs": [p], "bubble": None}, ["short"]) == []
    assert trade_map.cautions(None, ["long"]) == []


def test_a_retest_needs_the_price_to_leave_the_neckline_first(monkeypatch):
    monkeypatch.setattr(hs_pattern, "_stats_cache", {})
    # a close just through the neckline, then bars whose highs sit right under it: still only broken
    hugging = _bars(_path(TOP[:-1] + [(86, 107.5), (88, 106.0), (99, 105.8)], 100))
    for b in hugging[89:]:                                  # bars reaching back up near the line, never far below it
        b["high"], b["low"] = b["close"] * 1.008, b["close"] * 0.999
    got = hs_pattern.find(hugging, 3, 1.5)
    assert got and got[0]["state"] == "broken"


def test_the_false_break_share_counts_only_the_textbook_way_breaks():
    rows = []
    for d in range(1, 41):
        expected = d <= 24                                  # 24 neckline breaks (12 false), 16 over the head
        rows.append({"ticker": "T", "at": f"2024-01-{d:02d}" if d < 32 else f"2024-02-{d - 31:02d}",
                     "kind": "head_shoulders", "timeframe": "1d", "expected": "down",
                     "direction": "down" if expected else "up", "move_atr": 1.0,
                     "false_break": (d % 2 == 0) if expected else None, "prior": "none", "frac": None, "reach": None})
    s = hs_bt.table(rows, rows)["1d:head_shoulders"]
    assert s["false_break_pct"] == 50.0


def test_the_cautions_name_their_timeframe(monkeypatch):
    monkeypatch.setattr(hs_pattern, "_stats_cache", {})
    p = hs_pattern.find(_bars(_path(TOP, 100)), 3, 1.5)[0]
    got = trade_map.cautions({"hs": [p, {**p, "timeframe": "1w"}], "bubble": None}, ["long"])
    assert [(c["kind"], c["timeframe"]) for c in got] == [("head_shoulders", "1d"), ("head_shoulders", "1w")]
