"""#579 the day's market alerts: what gets through, what is pushed, the timeline API."""

import json
from datetime import date

from fastapi.testclient import TestClient

from desk import flow_alerts, market_alerts, notify, push
from tests.test_push import OTHER, OWNER, _app, _login, _pusher, _Sent, _store_with_sub, _turn

DAY = "2026-11-17"


def _shift(t: str, rule: str = "sell_grow", market: str = "kospi", who: str = "institution",
           severity: str = "caution", kind: str = "flow_shift") -> dict:
    return {"kind": kind, "rule": rule, "market": market, "who": who, "severity": severity,
            "t": f"{DAY}T{t}:00+09:00", "value": -1250.0, "prev": -310.0}


def test_admit_keeps_one_per_moment_cools_down_a_market_and_caps_the_day():
    day = [_shift("10:42")]
    assert not market_alerts.admit(_shift("10:42"), day)                                  # the same moment again
    assert not market_alerts.admit(_shift("10:55", "buy_grow", who="foreign"), day)      # the market cools 20 min
    assert market_alerts.admit(_shift("11:03", "buy_grow", who="foreign"), day)
    assert not market_alerts.admit(_shift("11:30"), day)                                 # the same rule: 60 min
    assert market_alerts.admit(_shift("11:42"), day)
    assert market_alerts.admit(_shift("10:50", market="futures"), day)                   # another market
    assert market_alerts.admit(_shift("10:44", "sell_turn"), day)                        # a turn skips the cooldown
    assert market_alerts.admit(_shift("10:44", "nq_drop", "nq", "all", kind="nq_alert"), day)   # another kind
    once = [_shift("10:00", "open_buy_reversal", market="futures", who="foreign")]
    assert not market_alerts.admit(_shift("14:00", "open_buy_reversal", market="futures", who="foreign"), once)
    full = [_shift(f"{9 + i}:00", market=f"m{i}") for i in range(market_alerts.DAILY_MAX)]
    assert not market_alerts.admit(_shift("15:00", market="kosdaq"), full)
    assert market_alerts.admit(_shift("15:00", market="kosdaq", severity="strong"), full)
    fuller = full + [_shift(f"15:0{i}", market=f"s{i}", severity="strong") for i in range(market_alerts.STRONG_EXTRA)]
    assert not market_alerts.admit(_shift("15:10", market="kosdaq", severity="strong"), fuller)


def test_info_never_holds_back_a_pushed_alert_and_turns_go_past_the_cap():
    info = [_shift("10:40", "buy_shrink", who="foreign", severity="info")]
    assert market_alerts.admit(_shift("10:42"), info)                                   # an info blocks nothing
    assert market_alerts.admit(_shift("10:45", "buy_shrink", who="institution", severity="info"), [_shift("10:42")])
    assert not market_alerts.admit(_shift("11:00", "buy_shrink", who="foreign", severity="info"), info)  # repeat
    infos = [_shift(f"{9 + i}:00", "buy_shrink", market=f"m{i}", severity="info") for i in range(market_alerts.DAILY_MAX)]
    assert market_alerts.admit(_shift("15:00", market="kosdaq"), infos)                 # info has its own cap
    assert not market_alerts.admit(_shift("15:00", "buy_shrink", market="kosdaq", severity="info"), infos)
    full = [_shift(f"{9 + i}:00", market=f"m{i}") for i in range(market_alerts.DAILY_MAX)]
    assert market_alerts.admit(_shift("15:00", "sell_turn", market="kosdaq"), full)


def test_nq_from_high_is_once_a_cme_session_across_the_kst_midnight():
    nq = (lambda t: {**_shift("00:00", "nq_from_high", "nq", "all", kind="nq_alert"), "t": t})
    first = nq("2026-11-17T23:55:00+09:00")              # 09:55 ET Tuesday: the session that opened Monday 18:00
    assert not market_alerts.admit(nq("2026-11-18T00:05:00+09:00"), [], [first])
    assert market_alerts.admit(nq("2026-11-18T08:10:00+09:00"), [], [first])           # 18:10 ET: the next session
    assert market_alerts.admit(nq("2026-11-18T00:05:00+09:00"), [], [])


def test_the_same_moment_is_judged_strongest_first(db_url):
    engine = _store_with_sub(db_url)
    alerts = market_alerts.MarketAlerts(engine)
    got = alerts.dispatch([_shift("10:42", "buy_grow", who="foreign"), _shift("10:42", severity="strong")])
    assert [(r["who"], r["severity"]) for r in got] == [("institution", "strong")]     # the caution waits 20 min
    engine.dispose()


def test_dispatch_records_once_and_pushes_only_caution_and_strong(db_url):
    engine = _store_with_sub(db_url)
    store = push.PushStore(engine)
    store.subscribe(1, "https://fcm.googleapis.com/fcm/send/nonq", {"p256dh": "k", "auth": "a"}, "탭",
                    {"nq_alert": False})
    sent = _Sent()
    alerts = market_alerts.MarketAlerts(engine, _pusher(engine, sent))
    batch = [_shift("10:42"), _shift("11:05", "buy_shrink", "kospi", "foreign", "info"),
             _shift("23:35", "nq_drop", "nq", "all", "strong", kind="nq_alert")]
    got = alerts.dispatch(batch)
    assert [(r["rule"], r["pushed"]) for r in got] == [("sell_grow", True), ("buy_shrink", False), ("nq_drop", True)]
    assert alerts.dispatch(batch) == []                                   # judged again: nothing twice
    to = [(c[0].rsplit("/", 1)[1], json.loads(c[1])["kind"]) for c in sent.calls]
    assert to == [("abc", "flow_shift"), ("nonq", "flow_shift"), ("abc", "nq_alert")]
    payload = json.loads(sent.calls[0][1])
    # #641 the tone's mark in place of the dot, and one line of reading
    assert payload["body"] == ("🟠 주의 · 기관 코스피 현물 매도 확대 · 10:42 · 최근 30분 −1,250억 (직전 30분 −310억)\n"
                               "기관 매도 확대 — 지수에 부담")
    assert payload["title"] == "수급 흐름 변화" and payload["severity"] == "caution" and payload["url"] == "/#/market"
    assert payload["tone"] == "caution" and json.loads(sent.calls[2][1])["tone"] == "warning"      # the NQ=F slump
    assert [r["tone"] for r in got] == ["caution", "caution", "warning"]    # a buy shrink (info by size): caution
    assert [a["rule"] for a in alerts.list(DAY)] == ["sell_grow", "buy_shrink", "nq_drop"]
    engine.dispose()


def test_a_push_failure_never_stops_recording(db_url):
    engine = _store_with_sub(db_url)

    class Broken:
        def broadcast(self, *kinds, payload):
            raise RuntimeError("push service down")

    assert notify.send(Broken(), "flow_shift", payload={}) == 0
    assert notify.send(None, "flow_shift", payload={}) == 0
    got = market_alerts.MarketAlerts(engine, Broken()).dispatch([_shift("10:42")])
    assert len(got) == 1 and market_alerts.MarketAlerts(engine).list(DAY)[0]["pushed"] is True
    engine.dispose()


class _History:
    """desk.futures_history's two reads: the days (newest first) and a day's snapshots."""

    def __init__(self, days: dict):
        self.days, self.reads = days, []

    def daily(self, n):
        return [{"date": d} for d in sorted(self.days, reverse=True)][:n]

    def intraday(self, day):
        self.reads.append(day.isoformat())
        return self.days.get(day.isoformat(), [])


def test_after_snapshot_judges_today_on_thresholds_read_once_a_day(db_url, monkeypatch):
    from desk import flow_shift

    engine = _store_with_sub(db_url)
    seen = []

    def judge(points, limits):
        seen.append((points, limits))
        return [_shift("10:42")]

    monkeypatch.setattr(flow_shift, "judge", judge)
    hist = _History({"2026-11-13": [{"p": 1}], "2026-11-16": [{"p": 2}], DAY: [{"p": 3}]})
    alerts = market_alerts.MarketAlerts(engine)
    assert [r["rule"] for r in alerts.after_snapshot(hist, date(2026, 11, 17))] == ["sell_grow"]
    assert alerts.after_snapshot(hist, date(2026, 11, 17)) == []
    assert seen[0][0] == [{"p": 3}] and seen[0][1] == flow_shift.DEFAULT        # two days: the defaults
    assert hist.reads == [DAY, "2026-11-16", "2026-11-13", DAY]                 # the history once a day
    assert alerts.after_snapshot(_History({}), date(2026, 11, 18)) == []
    engine.dispose()


def test_the_timeline_needs_a_session_and_merges_the_turns(db_url):
    app = _app(db_url)
    with TestClient(app, base_url="https://testserver") as client:
        assert client.get("/api/alerts/today").status_code == 401
        _login(client, OWNER)
        assert client.get("/api/alerts/today?date=17-11-2026").status_code == 422
        engine = app.state.market_alerts.engine
        flow_alerts.FlowAlerts(engine).dispatch([_turn("10:32", "sell_turn")], "2026-11-13")
        market_alerts.MarketAlerts(engine).dispatch([
            {**_shift("10:42"), "t": "2026-11-13T10:42:00+09:00"},
            {**_shift("09:05", "nq_drop", "nq", "all", kind="nq_alert"), "t": "2026-11-13T09:05:00+09:00"}])
        body = client.get("/api/alerts/today?date=2026-11-13").json()
        assert body["date"] == "2026-11-13"
        assert [(a["kind"], a["rule"], a["who"]) for a in body["items"]] == [
            ("nq_alert", "nq_drop", "all"), ("flow_turn", "sell_turn", "foreign"), ("flow_shift", "sell_grow", "institution")]
        assert body["items"][1]["text"] == "🔴 경고 · 외인 선물 순매도 전향 · 10:32 · 누적 -620계약\n외인 매수에서 매도로 돌아섬 — 하락 압력 경계"
        assert [a["tone"] for a in body["items"]] == ["warning", "warning", "caution"]     # #641 beside the severity
        assert [a["severity"] for a in body["items"]] == ["caution", "caution", "caution"]
        assert client.get("/api/alerts/today").json()["items"] == []


def test_the_timeline_shows_the_holdings_alerts_to_the_owner_only(db_url):
    """#581 a holding's stop alert (#694) is in the owner's timeline; another account doesn't see it."""
    app = _app(db_url)
    hold = {"kind": "holding_alert", "rule": "hold_stop", "market": "005930", "ticker": "005930", "who": "all",
            "severity": "strong", "t": "2026-11-13T10:50:00+09:00", "value": -6.2, "prev": -0.4, "price": 70000.0,
            "stop": 70300.0, "stop_basis": "20일선", "stop_is_max_loss": False, "regime": "trend_down",
            "regime_label": "하락 추세", "take_profit": None, "tp_basis": None}
    with TestClient(app, base_url="https://testserver") as client:
        mover = {"kind": "sector_mover", "rule": "sector_drop", "market": "kr_semi", "who": "all", "severity": "strong",
                 "t": "2026-11-13T11:00:00+09:00", "value": -3.4, "prev": None, "sentence": "국장 반도체 급락"}
        market_alerts.MarketAlerts(app.state.market_alerts.engine).dispatch([
            {**_shift("10:42"), "t": "2026-11-13T10:42:00+09:00"}, hold, mover])
        _login(client, OWNER)
        mine = client.get("/api/alerts/today?date=2026-11-13").json()["items"]
        assert [(a["kind"], a["rule"]) for a in mine] == [("flow_shift", "sell_grow"), ("holding_alert", "hold_stop")]
        assert mine[1]["tone"] == "warning" and "005930" in mine[1]["text"]
        assert mine[1]["value"] is None and mine[1]["prev"] is None        # the % against the average stays out
        # sector_mover (#680) is still left out until its screen (#740) shows it
        client.post("/api/auth/logout")
        _login(client, OTHER)
        theirs = client.get("/api/alerts/today?date=2026-11-13").json()["items"]
        assert [a["kind"] for a in theirs] == ["flow_shift"]
