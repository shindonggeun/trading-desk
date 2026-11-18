"""#628 (#361) foreign-flow turn alerts: the wording, each turn pushed once, a turn back pushed again."""

import pytest

from desk import flow_alerts as fa
from desk.db import make_engine, migrate


class Pusher:
    def __init__(self):
        self.sent = []

    def broadcast(self, *kinds, payload):
        self.sent.append((kinds, payload))
        return 1


@pytest.fixture
def alerts(db_url):
    migrate(db_url)
    engine = make_engine(db_url)
    yield fa.FlowAlerts(engine, Pusher())
    engine.dispose()


def _turn(t, kind, market="futures", value=-620, who="foreign"):
    return {"t": f"2026-11-18T{t}:00+09:00", "market": market, "who": who, "kind": kind, "value": value}


def test_the_wording():
    assert fa.text(_turn("10:32", "sell_turn")) == "외인 선물 순매도 전향 · 10:32 · 누적 -620계약"
    assert fa.text(_turn("13:05", "buy_turn", "kospi", 1234.4)) == "외인 코스피 현물 순매수 전향 · 13:05 · 누적 +1,234억"
    assert fa.text(_turn("09:10", "buy_turn", "kosdaq", None)) == "외인 코스닥 현물 순매수 전향 · 09:10"


def test_each_turn_is_pushed_once_and_a_turn_back_pushes_again(alerts):
    day = "2026-11-18"
    first = alerts.dispatch([_turn("10:32", "sell_turn"), _turn("10:40", "sell_turn")], day)
    assert [r["turned_at"][11:16] for r in first] == ["10:32"]              # the same direction again: once
    assert alerts.dispatch([_turn("10:32", "sell_turn")], day) == []         # judged again: nothing twice
    back = alerts.dispatch([_turn("10:20", "buy_turn"), _turn("11:15", "buy_turn", value=300)], day)
    assert [r["turned_at"][11:16] for r in back] == ["11:15"]               # one before the last sent is old news
    kinds, payload = alerts.pusher.sent[-1]
    # #641 a buy turn is good news: its tone is one more kind a channel can turn off
    assert kinds == ("flow", "flow_futures", "tone_positive") and payload["url"] == fa.ALERT_URL
    assert payload["tag"] == "flow-futures" and payload["tone"] == "positive"
    assert payload["body"] == "🟢 호재 · 외인 선물 순매수 전향 · 11:15 · 누적 +300계약\n외인 매수 유입 — 지수에 우호적"
    assert [r["tone"] for r in first + back] == ["warning", "positive"]
    assert [r["kind"] for r in alerts.list(day)] == ["sell_turn", "buy_turn"] and alerts.list("2026-11-19") == []


def test_markets_are_apart_and_other_rows_are_skipped(alerts):
    day = "2026-11-18"
    sent = alerts.dispatch([_turn("10:00", "sell_turn"), _turn("10:01", "sell_turn", "kospi", -50),
                            _turn("10:02", "buy_turn", who="institution"), _turn("10:03", "buy_turn", "nasdaq"),
                            _turn("10:04", "flat")], day)
    assert [(r["market"], r["kind"]) for r in sent] == [("futures", "sell_turn"), ("kospi", "sell_turn")]
    assert len(alerts.pusher.sent) == 2


def test_after_a_snapshot_today_points_are_judged(alerts, monkeypatch):
    from datetime import date

    from desk import flow_intraday

    monkeypatch.setattr(flow_intraday, "turns",
                        lambda points, market: [_turn("10:32", "sell_turn")] if market == "futures" else [])
    got = alerts.after_snapshot(lambda d: [{"day": d.isoformat()}], date(2026, 11, 18))
    assert [r["market"] for r in got] == ["futures"] and got[0]["trade_date"] == "2026-11-18"
