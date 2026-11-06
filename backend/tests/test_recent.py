"""#152: the last sessions count - yesterday's low is a level, an old range fades, a broken level
counts half, and the analysis gets the last days compared."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from desk import horizons, recent, sr

RAM = json.loads((Path(__file__).parent / "fixtures" / "ram_2026-11-05.json").read_text(encoding="utf-8"))["daily"]


def _days(closes, start=date(2026, 9, 1)):
    return [{"at": (start + timedelta(days=i)).isoformat() + "T05:00:00+00:00", "open": c, "high": c * 1.01,
             "low": c * 0.99, "close": c, "volume": 100 + i} for i, c in enumerate(closes)]


def test_yesterdays_low_is_a_level_after_the_crash():
    out = sr.compute(RAM, [], 5.13, horizon="swing")
    support = [lv for lv in out["levels"] if lv["kind"] == "support"]
    top = max(support, key=lambda lv: lv["strength"])
    # one bar's low counts once, with every name it carries - not 3x
    assert top["price"] == 4.84 and top["basis"] == "최근 일봉 저가 = 5일 저가 = 20일 저가" and top["strength"] == 1.5
    assert any(lv["price"] == 6.31 and "최근 일봉 고가" in lv["basis"] for lv in out["levels"])
    # the old box the price fell out of (7.6-8.7) does not outrank what the last days made
    old = [lv for lv in out["levels"] if 7.5 <= lv["price"] <= 8.8]
    assert all(lv["strength"] <= top["strength"] for lv in old)


def test_the_last_sessions_are_candidates_even_without_a_swing():
    h = horizons.get("swing")
    days = _days([100 + i for i in range(30)])         # a steady rise: no swing low near the end
    names = " ".join(c.basis for c in sr._recent(days, h))
    for name in ("최근 일봉 고가", "최근 일봉 저가", "그 전 일봉 고가", "그 전 일봉 저가", "5일 고가", "5일 저가",
                 "20일 고가", "20일 저가"):
        assert name in names
    assert sr._recent(days[:1], h) == []


def test_intraday_horizons_get_no_second_copy_of_the_prior_day():
    # their own "전일" and daily-range levels already exist (desk.sr.compute): nothing is added twice
    days = _days([100 + i for i in range(30)])
    for hid in ("scalp", "intraday", "short_swing"):
        assert sr._recent(days, horizons.get(hid)) == []
    fives = [{"at": f"2026-10-01T{13 + i // 12:02d}:{(i % 12) * 5:02d}:00+00:00", "open": 129, "high": 129.5,
              "low": 128.5, "close": 129, "volume": 10} for i in range(60)]
    out = sr.compute(fives, days, 129.0, weekly_complete=True, horizon="intraday")
    assert all("×2" not in lv["basis"] for lv in out["levels"])


def test_a_forming_session_is_no_level_and_its_volume_is_not_compared():
    h = horizons.get("swing")
    days = _days([100 + i for i in range(30)])
    partial = sr._recent(days, h, partial=True)
    assert days[-1]["high"] not in [c.price for c in partial]          # today's high still moves
    assert days[-2]["high"] in [c.price for c in partial]              # yesterday's is the "최근 일봉"
    r = recent.compare(days, partial=True)
    assert r["partial"] and r["volume_x20"] is None
    assert "today's session so far (partial)" in recent.prompt_note(r)
    assert sr.forming(days, "NVDA", now=datetime.fromisoformat(days[-1]["at"]))
    assert not sr.forming(days[:-1], "NVDA", now=datetime.fromisoformat(days[-1]["at"]))


def test_a_broken_swing_counts_half():
    closes = [100, 99, 98, 95, 98, 99, 100, 101, 100, 99, 97, 93, 90, 89, 88]   # the 95 low broken later
    days = _days(closes)
    swings = sr._swings(days, 2, "1d", "일봉", 1.0, timedelta(days=10_000), atr=2.0)
    low95 = next(c for c in swings if abs(c.price - 95 * 0.99) < 1e-9)
    assert "이탈됨" in low95.basis and 0.45 < low95.weight < 0.55
    intact = sr._swings(days, 2, "1d", "일봉", 1.0, timedelta(days=10_000))
    assert all("이탈됨" not in c.basis for c in intact)                  # no ATR given: no judgement


def test_the_swing_half_life_stays_a_year():
    # the basket backtest (2019-, swing): a 30-day daily / 180-day weekly half-life did not help
    # (expectancy -0.182R vs -0.180R) - the gain came from the recent-session levels alone
    h = horizons.get("swing")
    assert h.half_life == timedelta(days=365) and h.secondary_half_life == timedelta(days=365)


def test_compare_numbers_the_last_days():
    r = recent.compare(RAM)
    assert -32.6 < r["changes_pct"]["d1"] < -32.3 and r["changes_pct"]["d2"] < r["changes_pct"]["d1"] + 10
    assert r["streak"] == -2 and r["gap_pct"] < -13 and r["month_position_pct"] < 1
    assert r["higher_low"] is False and r["lower_high"] is True
    assert r["atr5_vs_atr20"] > 1 and r["volume_x20"] > 2
    assert [s["at"] for s in r["sessions"]][-1] == "2026-11-05" and len(r["sessions"]) == 5
    assert recent.compare(RAM[:3]) is None


def test_the_note_says_what_to_weigh():
    note = recent.prompt_note(recent.compare(RAM))
    assert "vs 1 session -32.5%" in note and "2026-11-05 O6.22 H6.31 L4.84 C4.87" in note
    assert "outranks one from a range months ago" in note
    assert recent.prompt_note(None) is None
    assert sr.compute(RAM, [], 5.13, horizon="swing")["recent"]["close"] == 4.87


def test_the_note_leaves_out_what_the_bars_cannot_give():
    flat = _days([100.0] * 8)
    for b in flat:
        b["volume"] = 0
    note = recent.prompt_note(recent.compare(flat))
    assert "None" not in note and "flat last close" in note and "volume" not in note.split("Sessions")[0]
    tiny = _days([0.000012 * (1 + i / 100) for i in range(8)])
    assert "e-0" not in recent.prompt_note(recent.compare(tiny))


def test_a_broken_swing_high_counts_half_too():
    closes = [100, 101, 102, 105, 102, 101, 100, 99, 100, 102, 104, 107, 110, 111, 112]   # the 105 high cleared
    swings = sr._swings(_days(closes), 2, "1d", "일봉", 1.0, timedelta(days=10_000), atr=2.0)
    high = next(c for c in swings if abs(c.price - 105 * 1.01) < 1e-9)
    assert "돌파됨" in high.basis and 0.45 < high.weight < 0.55
