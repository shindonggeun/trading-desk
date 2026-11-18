"""#579 investor flow shifts on minute snapshots (desk.flow_shift)."""

from datetime import UTC, datetime, timedelta
from itertools import pairwise

from desk import flow_shift
from desk.futures_flow import KST

DAY = datetime(2026, 11, 17, 8, 45, tzinfo=KST)


def _points(totals: dict, until: str = "15:45") -> list[dict]:
    """A snapshot a minute from 08:45 to ``until``; ``totals[(market, who)](minute)`` -> running total
    (minute 0 = 08:45). Spot is only there from 09:00."""
    end = datetime.combine(DAY.date(), datetime.strptime(until, "%H:%M").time(), KST)
    out, m = [], 0
    while DAY + timedelta(minutes=m) <= end:
        at = DAY + timedelta(minutes=m)
        val = (lambda market, who, m=m: float(totals[(market, who)](m)) if (market, who) in totals else 0.0)
        p = {"at": at.astimezone(UTC).isoformat(timespec="seconds"),
             "investors": {w: {"net_contracts": val("futures", w)} for w in flow_shift.WHO}}
        if m >= 15:
            p["spot"] = {mk: {w: val(mk, w) for w in flow_shift.WHO} for mk in ("kospi", "kosdaq")}
        out.append(p)
        m += 1
    return out


def _minute(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return (h * 60 + m) - (8 * 60 + 45)


def _ramp(points_at: list[tuple[str, float]]):
    """Piecewise-linear running total through (HH:MM, total) points, flat before and after."""
    pts = [(_minute(t), v) for t, v in points_at]

    def total(m):
        if m <= pts[0][0]:
            return pts[0][1]
        for (m0, v0), (m1, v1) in pairwise(pts):
            if m <= m1:
                return v0 + (v1 - v0) * (m - m0) / (m1 - m0)
        return pts[-1][1]
    return total


LIMITS = {**flow_shift.DEFAULT, ("kospi", "institution"): 500.0, ("kospi", "foreign"): 1000.0,
          ("futures", "foreign"): 400.0}


def test_selling_that_speeds_up_is_a_sell_growth_with_the_issues_text():
    # the institution sold 310 in 09:42-10:12, then 1,250 in 10:12-10:42
    inst = _ramp([("09:42", 0), ("10:12", -310), ("10:42", -1560)])
    got = flow_shift.judge(_points({("kospi", "institution"): inst}, until="10:42"), LIMITS)
    grow = [a for a in got if a["rule"] == "sell_grow"]
    assert len(grow) == 1 and grow[0]["market"] == "kospi" and grow[0]["who"] == "institution"
    assert grow[0]["value"] == -1250 and grow[0]["prev"] == -310 and grow[0]["severity"] == "strong"
    assert flow_shift.text(grow[0]) == "🔵 기관 코스피 현물 매도 확대 · 10:42 · 최근 30분 −1,250억 (직전 30분 −310억)"


def test_buying_that_slows_down_is_a_buy_shrink_on_the_timeline_only():
    foreign = _ramp([("10:00", 0), ("10:30", 2000), ("11:00", 2400)])
    got = flow_shift.judge(_points({("kospi", "foreign"): foreign}, until="11:00"), LIMITS)
    assert [(a["rule"], a["severity"], a["value"], a["prev"]) for a in got] == [("buy_shrink", "info", 400, 2000)]
    assert flow_shift.text(got[0]).startswith("🔵 외인 코스피 현물 매수 축소 · 11:00")


def test_pace_waits_for_the_opening_to_pass_and_stops_at_the_closing_auction():
    burst = _ramp([("09:00", 0), ("09:30", 3000), ("10:00", 3100)])
    assert flow_shift.judge(_points({("kospi", "foreign"): burst}, until="10:00"), LIMITS) == []   # 10:00 < 10:30
    late = _ramp([("14:50", 0), ("15:20", -5000), ("15:30", -9000)])
    assert flow_shift.judge(_points({("kospi", "foreign"): late}, until="15:25"), LIMITS) == []
    assert [a["rule"] for a in flow_shift.judge(_points({("kospi", "foreign"): late}, until="15:19"), LIMITS)] \
        == ["sell_grow"]


def test_the_individuals_pace_is_not_judged_but_their_turns_are_after_the_first_half_hour():
    retail = _ramp([("10:00", 0), ("10:30", -3000), ("11:00", -9000)])
    assert flow_shift.judge(_points({("kospi", "individual"): retail}, until="11:00"), LIMITS) == []
    # institution futures: buying, then net selling for good at 10:20 (dead band 500 contracts, 2 snapshots)
    inst = _ramp([("09:20", 800), ("10:10", 800), ("10:20", -700)])
    got = flow_shift.judge(_points({("futures", "institution"): inst}, until="10:25"), LIMITS)
    turns = [a for a in got if a["rule"] == "sell_turn"]
    assert len(turns) == 1 and turns[0]["who"] == "institution" and turns[0]["severity"] == "caution"
    assert flow_shift.text(turns[0]) == "🔵 기관 선물 순매도 전향 · 10:20 · 누적 −700계약"
    # the same turn ten minutes on is old news
    assert not [a for a in flow_shift.judge(_points({("futures", "institution"): inst}, until="10:40"), LIMITS)
                if a["rule"] == "sell_turn"]
    # in the opening half hour a turn is noise
    early = _ramp([("09:01", 800), ("09:10", -800)])
    assert flow_shift.judge(_points({("kospi", "individual"): early}, until="09:15"), LIMITS) == []


def test_a_strong_opening_that_turns_is_an_open_reversal():
    foreign = _ramp([("08:45", 0), ("09:45", 1200), ("11:00", 400), ("11:30", 350)])
    got = flow_shift.judge(_points({("futures", "foreign"): foreign}, until="11:30"), LIMITS)
    rev = [a for a in got if a["rule"] == "open_buy_reversal"]
    assert len(rev) == 1 and rev[0]["value"] == -850 and rev[0]["prev"] == 1200
    assert flow_shift.text(rev[0]) == "🔵 외인 선물 장초 매수 뒤 매도 반전 · 11:30 · 장초 1시간 +1,200계약 → 이후 −850계약"


def test_thresholds_come_from_three_days_of_history_and_never_under_half_the_default():
    quiet = _points({("futures", "foreign"): lambda m: m * 1.0})       # 30 contracts every half hour
    assert flow_shift.thresholds([quiet, quiet]) == flow_shift.DEFAULT
    busy = _points({("futures", "foreign"): lambda m: m * 40.0})       # 1,200 every half hour
    got = flow_shift.thresholds([busy, busy, busy])
    assert got[("futures", "foreign")] == 1200
    assert flow_shift.thresholds([quiet] * 3)[("futures", "foreign")] == flow_shift.DEFAULT[("futures", "foreign")] / 2
