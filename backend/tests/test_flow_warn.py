"""#699 flow warnings: the individual alone against the main positions' selling, and selling past its floor."""

import json
from pathlib import Path

from desk import alert_tone, flow_warn, market_alerts
from tests.test_push import _store_with_sub

FIXTURE = Path(__file__).parent / "fixtures" / "flow_snapshots_2026-11-18_20.json"
DAYS = json.loads(FIXTURE.read_text(encoding="utf-8"))["days"]


def _point(t: str, kospi: dict, kosdaq: dict | None = None, fut: dict | None = None, price: float = 765.0,
           day: str = "2026-11-20") -> dict:
    fut = fut or {}
    return {"at": f"{day}T{t}:00+09:00", "date": day, "price": price,
            "investors": {c: {"net_contracts": fut.get(c)} for c in ("foreign", "institution", "individual")},
            "spot": {"kospi": kospi, "kosdaq": kosdaq or {}}}


def _spot(individual, foreign, institution):
    return {"individual": individual, "foreign": foreign, "institution": institution}


def _replay(points, hist=None):
    """Every snapshot judged as the loop does: the first of each (rule, market, severity)."""
    seen, out = set(), []
    for k in range(1, len(points) + 1):
        for a in flow_warn.judge(points[:k], hist):
            key = (a["rule"], a["market"], a["severity"])
            if key not in seen:
                seen.add(key)
                out.append(a)
    return out


def test_today_reads_as_retail_alone_on_kospi_and_turns_strong():
    got = _replay(DAYS["2026-11-20"])
    kospi = [(a["t"][11:16], a["severity"]) for a in got if (a["rule"], a["market"]) == ("retail_alone", "kospi")]
    assert kospi == [("09:30", "caution"), ("09:48", "strong")]
    strong = next(a for a in got if a["rule"] == "retail_alone" and a["severity"] == "strong")
    assert flow_warn.text(strong) == ("개미 홀로 받치기 · 코스피 · 09:48 — 개인 +1.00조 vs 외인 −6,111억 · 기관 −4,683억, "
                                      "외인 선물 −348계약 · 기관은 선물 +1,401계약 매수(헤지 · 방어)")
    assert alert_tone.tone(strong) == "warning"
    assert {(a["rule"], a["market"]) for a in got} == {("retail_alone", "kospi"), ("heavy_sell_institution", "kospi"),
                                                      ("retail_alone", "kosdaq")}


def test_an_ordinary_day_warns_nothing():
    assert _replay(DAYS["2026-11-18"]) == []       # the foreign and the individual sold, the institution bought


def test_today_through_the_alerts_once_and_once_more_when_strong(db_url):
    engine = _store_with_sub(db_url)
    alerts = market_alerts.MarketAlerts(engine)
    points = DAYS["2026-11-20"]
    for k in range(1, len(points) + 1):
        alerts.dispatch(flow_warn.judge(points[:k]))
    rows = [r for r in alerts.list("2026-11-20") if r["kind"] == "flow_warn"]
    kospi = [(r["t"][11:16], r["severity"], r["pushed"]) for r in rows if (r["rule"], r["market"]) == ("retail_alone", "kospi")]
    assert kospi == [("09:30", "caution", True), ("09:48", "strong", True)]
    assert rows[0]["text"].startswith("🟠 주의 · 개미 홀로 받치기 · 코스피 · 09:30")
    assert rows[0]["text"].endswith("개인 혼자 받는 장 — 외인 · 기관이 빠져나가는 중, 하락 압력 경계")
    assert len(rows) == 4                            # + 코스피 기관 과도한 매도, 코스닥 개미 홀로
    engine.dispose()


def test_admit_once_a_day_and_once_more_when_strong():
    def warn(t, severity="caution", rule="retail_alone", market="kospi"):
        return {"kind": "flow_warn", "rule": rule, "market": market, "who": flow_warn.WHO[rule],
                "severity": severity, "t": f"2026-11-20T{t}:00+09:00"}
    day = [warn("09:30")]
    assert not market_alerts.admit(warn("10:30"), day)                     # once a day
    assert market_alerts.admit(warn("09:31", "strong"), day)               # stronger: once more, no cooldown
    assert not market_alerts.admit(warn("11:00", "strong"), [*day, warn("09:31", "strong")])
    assert market_alerts.admit(warn("09:31", rule="heavy_sell_foreign"), day)   # another rule
    assert market_alerts.admit(warn("09:31", market="kosdaq"), day)        # another market
    assert not market_alerts.admit(warn("12:00", "strong"), [warn("09:30", "strong")])


def test_retail_alone_needs_both_main_positions_selling_most_of_it():
    both = [_point("10:00", _spot(6000, -4000, -1500))]
    assert [a["rule"] for a in flow_warn.judge(both)] == ["retail_alone"]
    inst_buys = [_point("10:00", _spot(6000, -6500, 600))]
    assert flow_warn.judge(inst_buys) == []
    little = [_point("10:00", _spot(6000, -2500, -1100))]                 # 3,600억 of 6,000억: not most of it
    assert flow_warn.judge(little) == []
    small = [_point("10:00", _spot(4000, -3000, -1000))]                  # under the 5,000억 floor
    assert flow_warn.judge(small) == []


def test_foreign_futures_selling_makes_kospi_retail_alone_strong():
    calm = flow_warn.judge([_point("10:00", _spot(6000, -4000, -1500), fut={"foreign": -100})])
    sells = flow_warn.judge([_point("10:00", _spot(6000, -4000, -1500), fut={"foreign": -600})])
    assert calm[0]["severity"] == "caution" and sells[0]["severity"] == "strong"


def test_heavy_selling_and_the_combined_spot_plus_futures():
    got = flow_warn.judge([_point("13:00", _spot(9000, -11000, -2000))])
    assert [(a["rule"], a["severity"]) for a in got] == [("retail_alone", "caution"), ("heavy_sell_foreign", "caution")]
    # 1,000 contracts at 800 x 250,000 KRW = 2,000억 more of the foreign and institution's selling
    both = flow_warn.judge([_point("13:00", _spot(None, -9000, -4500), fut={"foreign": -1000, "institution": 0},
                                   price=800.0)])
    assert both == []                                                       # no individual: nothing judged
    both = flow_warn.judge([_point("13:00", _spot(3000, -9000, -4500), fut={"foreign": -1000, "institution": 0},
                                   price=800.0)])
    combined = next(a for a in both if a["rule"] == "heavy_sell_combined")
    assert combined["value"] == -15500.0 and combined["who"] == "all"
    assert "현물 + 선물 −1.55조" in flow_warn.text(combined)


def test_only_from_half_an_hour_after_the_open_to_the_closing_auction():
    spot = _spot(6000, -4000, -1500)
    assert flow_warn.judge([_point("09:29", spot)]) == []
    assert flow_warn.judge([_point("09:30", spot)]) != []
    assert flow_warn.judge([_point("15:20", spot)]) == []


def test_with_enough_history_the_same_minute_must_be_extreme_too():
    def day(d, individual):
        return [_point(f"{h:02d}:{m:02d}", _spot(individual, -individual * 0.6, -individual * 0.3), day=d)
                for h in range(9, 16) for m in range(0, 60, 5) if (9, 0) <= (h, m) <= (15, 30)]
    busy = [day(f"2026-10-{i:02d}", 8000 + i * 100) for i in range(1, 13)]   # 12 days of 8,100 ~ 9,200억
    hist = flow_warn.history(busy)
    assert hist["n"] == 12 and len(hist["at"][("kospi", "individual", 600)]) == 12
    today = [_point("10:00", _spot(6000, -4000, -1500))]
    assert flow_warn.judge(today, hist) == []                               # 6,000억 is a quiet day here
    loud = flow_warn.judge([_point("10:00", _spot(9500, -6000, -3000))], hist)
    assert loud[0]["rule"] == "retail_alone" and loud[0]["pct"] == 1.0
    assert "12일 같은 시각 중 최대" in flow_warn.text(loud[0])
    assert flow_warn.judge(today, flow_warn.history(busy[:5])) != []        # under MIN_DAYS: the floors alone


def test_amounts_read_in_jo_and_eok():
    assert flow_warn._eok(12534) == "+1.25조" and flow_warn._eok(-7718) == "−7,718억" and flow_warn._eok(None) == "?"


def test_a_device_can_turn_the_warnings_off(db_url):
    from desk import push

    engine = _store_with_sub(db_url)
    store = push.PushStore(engine)
    store.subscribe(1, "https://fcm.googleapis.com/fcm/send/nowarn", {"p256dh": "k", "auth": "a"}, "탭",
                    {"flow_warn": False})
    endpoints = [s["endpoint"].rsplit("/", 1)[1] for s in store.for_kind("flow_warn")]
    assert "nowarn" not in endpoints and endpoints                     # the others still get them
    engine.dispose()


def test_heavy_selling_with_history_must_be_the_minutes_extreme():
    def day(d, foreign):
        return [_point(f"{h:02d}:{m:02d}", _spot(1000, foreign, -100), day=d)
                for h in range(9, 16) for m in range(0, 60, 5) if (9, 0) <= (h, m) <= (15, 30)]
    hist = flow_warn.history([day(f"2026-10-{i:02d}", -11000 - i * 500) for i in range(1, 13)])   # −1.15 ~ −1.7조
    mild = [_point("11:00", _spot(1000, -12000, -100))]                     # past the floor, an ordinary day here
    assert flow_warn.judge(mild, hist) == []
    worst = flow_warn.judge([_point("11:00", _spot(1000, -18000, -100))], hist)
    worst = [a for a in worst if a["rule"] == "heavy_sell_foreign"]
    assert worst and worst[0]["pct"] == 0.0
    assert "12일 같은 시각 중 최대 매도" in flow_warn.text(worst[0])
    near = flow_warn.judge([_point("11:00", _spot(1000, -16800, -100))], hist)
    assert near == [] or "매도 상위" in flow_warn.text(near[0])


def test_a_minute_too_few_days_have_falls_back_to_the_floors():
    days = [[_point("14:00", _spot(9000, -9000, -2000), day=f"2026-10-{i:02d}")] for i in range(1, 13)]
    hist = flow_warn.history(days)                    # 12 days with spot, but only around 14:00
    assert hist["n"] == 12 and ("kospi", "individual", 600) not in hist["at"]
    assert [a["rule"] for a in flow_warn.judge([_point("10:00", _spot(6000, -4000, -1500))], hist)] == ["retail_alone"]


def test_after_snapshot_sends_the_warnings_and_a_fault_there_keeps_the_shifts(db_url, monkeypatch):
    from datetime import date

    from desk import flow_shift

    class History:
        def __init__(self):
            self.reads = []

        def daily(self, n):
            return [{"date": "2026-11-20"}, {"date": "2026-11-18"}]

        def intraday(self, day):
            self.reads.append(day.isoformat())
            return DAYS.get(day.isoformat(), [])

    engine = _store_with_sub(db_url)
    hist = History()
    alerts = market_alerts.MarketAlerts(engine)
    got = alerts.after_snapshot(hist, date(2026, 11, 20))
    assert ("retail_alone", "kospi") in {(r["rule"], r["market"]) for r in got if r["kind"] == "flow_warn"}
    alerts.after_snapshot(hist, date(2026, 11, 20))
    assert hist.reads.count("2026-11-18") == 1                             # the past days once a day, for both

    def broken(points, hist=None):
        raise ValueError("bad snapshot")

    monkeypatch.setattr(flow_warn, "judge", broken)
    monkeypatch.setattr(flow_shift, "judge", lambda points, limits: [
        {"kind": "flow_shift", "rule": "sell_grow", "market": "futures", "who": "foreign", "severity": "caution",
         "t": "2026-11-20T10:31:00+09:00", "value": -900.0, "prev": -100.0}])
    assert [r["rule"] for r in market_alerts.MarketAlerts(engine).after_snapshot(hist, date(2026, 11, 20))] == ["sell_grow"]
    engine.dispose()


def test_warnings_of_two_rules_do_not_replace_each_other_on_the_device():
    a = {"kind": "flow_warn", "rule": "retail_alone", "market": "kospi"}
    b = {**a, "rule": "heavy_sell_institution"}
    assert market_alerts._tag(a) != market_alerts._tag(b)
    assert market_alerts._tag({"kind": "flow_shift", "rule": "sell_grow", "market": "kospi"}) == "flow_shift-kospi"


def test_rounding_and_a_combined_figure_without_the_futures():
    assert flow_warn._eok(-9999.6) == "−1.00조"
    alert = {"rule": "heavy_sell_combined", "market": "kospi", "t": "2026-11-20T13:00:00+09:00", "value": -15500.0,
             "individual": 3000, "foreign": -11000, "institution": -4500, "futures_foreign": None,
             "futures_institution": None, "pct": None}
    assert "선물 값 없음" in flow_warn.text(alert) and "현물 + 선물" not in flow_warn.text(alert)
