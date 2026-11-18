"""#572 PR-B: calls made in their windows, stored once, scored after the close, read back."""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import insert, select

from desk import day_call as dc
from desk import day_calls, push
from desk.db import day_calls as t
from desk.db import make_engine, migrate
from tests.test_day_call import SERIES

CLIM = [0.06, 0.24, 0.40, 0.24, 0.06]
DAY = date(2024, 6, 12)                                   # a Wednesday, open in both markets


def _model(market: str) -> dict:
    """Zero weights (the climatology): prev_close calls lean nowhere, open calls are holds."""
    calls = {}
    for name, (kind, extras) in dc.CALLS.items():
        rounds = {}
        for round_ in (1, 2):
            names = list(dc.features(market, round_, DAY, SERIES, extras)[0])
            k = len(names)
            rounds[str(round_)] = {"features": names, "mean": [0.0] * k, "std": [1.0] * k, "beta0": 0.0,
                                   "beta": [0.0] * k, "gamma0": -0.9, "gamma": [0.0] * k, "edges": list(dc.EDGES),
                                   "lambda": 0.5, "climatology": CLIM, "label": kind, "extras": list(extras),
                                   "trained_through": "2025-06-30", "trained_from": "2023-02-01", "n_train": 600}
        calls[name] = {"label": kind, "extras": list(extras), "rounds": rounds}
    return {"version": f"dc-test-{market}", "market": market, "format": 1, "code": "x", "calls": calls}


def _candles(symbol, interval, count):
    return SERIES[symbol] if interval == "1d" else SERIES[f"{symbol}@60m"]


@pytest.fixture
def desk(db_url):
    migrate(db_url)
    engine = make_engine(db_url)
    yield day_calls.DayCalls(engine, _candles, models={"kr": _model("kr"), "us": _model("us")}, reports={})
    engine.dispose()


def _kst(d: date, h: int, m: int = 0) -> datetime:
    return datetime.combine(d, time(h, m), dc.KST)


def _et(d: date, h: int, m: int = 0) -> datetime:
    return datetime.combine(d, time(h, m), dc.ET)


def test_a_round_is_called_only_inside_its_window_on_an_open_day(desk):
    assert desk.due(_kst(DAY, 7, 40)) == [("kr", 1, DAY)]
    assert desk.due(_kst(DAY, 8, 1)) == []                               # late: never
    assert desk.due(_kst(DAY, 9, 45)) == [("kr", 2, DAY)]
    assert desk.due(_et(DAY, 9, 10)) == [("us", 1, DAY)] and desk.due(_et(DAY, 10, 5)) == [("us", 2, DAY)]
    assert desk.due(_kst(date(2024, 6, 15), 7, 40)) == []                # a Saturday
    assert desk.due(_kst(date(2024, 6, 6), 7, 40)) == []                 # 현충일
    assert desk.due(_kst(date(2024, 11, 14), 9, 45)) == []               # the CSAT: opens at 10:00


def test_both_calls_are_stored_once_and_scored_after_the_close(desk):
    made = desk.run(_kst(DAY, 7, 40))
    assert sorted(r["call"] for r in made) == ["open", "prev_close"]
    assert desk.run(_kst(DAY, 7, 50)) == []                               # done: nothing twice
    with desk.engine.connect() as conn:
        rows = {r["call"]: r for r in conn.execute(select(t)).mappings()}
    assert rows["open"]["verdict"] == "hold" and rows["prev_close"]["verdict"] in ("long", "short")
    assert rows["prev_close"]["model"] == "prev_close" and round(sum(rows["open"]["probs"].values()), 1) == 100.0
    assert desk.score(_kst(DAY, 15, 55)) == 0                             # the session is not over
    assert desk.score(_kst(DAY, 16, 5)) == 2
    with desk.engine.connect() as conn:
        rows = {r["call"]: r for r in conn.execute(select(t)).mappings()}
    assert rows["open"]["hit_direction"] is None and rows["open"]["brier"] is not None   # a hold has no direction hit
    assert rows["prev_close"]["scored_at"] is not None
    assert rows["prev_close"]["outcome"]["open"] == pytest.approx(
        dc.label("kr", DAY, SERIES, "cc", _kst(DAY, 16, 5))["open"])        # yesterday's close


def test_without_the_hourly_futures_the_plain_model_stands_in(desk):
    desk.candles = lambda s, i, n: [] if i == "60m" else SERIES[s]
    made = {r["call"]: r for r in desk.run(_kst(DAY, 7, 40))}
    assert made["prev_close"]["model"] == "prev_close_plain"
    assert "야간 선물 없이" in day_calls.push_text(made["prev_close"])


def test_the_views_the_record_and_the_morning_line(desk):
    desk.run(_kst(DAY, 7, 40))
    desk.run(_kst(DAY, 9, 45))
    desk.score(_kst(DAY, 16, 5))
    now = _kst(DAY, 17)
    v = desk.view("kr", now)
    assert v["date"] == DAY.isoformat() and v["today"] is True and set(v["calls"]) == {"prev_close", "open"}
    pc = v["calls"]["prev_close"]
    assert pc["round"] == 2 and [e["round"] for e in v["rounds"]["prev_close"]] == [1, 2]
    assert pc["call_label"] == "전일 종가 대비" and pc["measured_from"] == "prev_close" and pc["outcome"]
    assert pc["record"]["n"] == 1 and pc["record"]["basis"] == "backtest"     # under 30 live calls
    assert v["calls"]["open"]["verdict"] == "hold" and len(v["note"]) == 3
    assert desk.view("kr", now + timedelta(days=1))["today"] is False          # the next morning: the last session
    h = desk.history("kr", 30, now)
    assert len(h["items"]) == 4 and h["items"][0]["round"] == 2
    s = desk.stats("kr", 30, now)["calls"]
    assert s["prev_close"]["n"] == 2 and s["prev_close"]["by_round"]["1"]["n"] == 1
    assert s["open"]["hold_rate"] == 1.0 and s["open"]["called"] == {"n": 0, "direction_hit_rate": None}
    assert s["open"]["direction_hit_rate"] is None and s["prev_close"]["missed"] == 0
    assert sum(p["actual"] for p in s["prev_close"]["per_stage"]) == 2
    line = desk.today_call(now)
    assert line["kr"]["label"] == dc.LABEL_KO[line["kr"]["stage"]] and line["us"] is None
    assert line["line"].startswith("오늘 국장은 전일 종가 대비")
    desk.clock = lambda: now
    day_calls.bind(desk)
    try:
        assert dc.today_call()["kr"]["session_date"] == DAY.isoformat()      # what #571 reads in-process
    finally:
        day_calls.bind(None)
    assert dc.today_call() is None


def test_a_missed_window_is_counted(desk):
    desk.run(_kst(DAY, 7, 40))                                     # round 1 only; round 2's window passes
    s = desk.stats("kr", 30, _kst(DAY, 12))["calls"]["prev_close"]
    assert s["missed"] == 1


def test_the_backfill_makes_only_the_sessions_after_training(desk):
    now = datetime(2025, 9, 6, 12, tzinfo=UTC)
    made = desk.backfill("kr", SERIES, 90, now)
    with desk.engine.connect() as conn:
        rows = conn.execute(select(t)).mappings().all()
    assert made == len(rows) > 0 and {r["source"] for r in rows} == {"backfill"}
    assert min(r["trade_date"] for r in rows) > date(2025, 6, 30)
    assert all(r["called_at"] is not None for r in rows) and sum(r["scored_at"] is not None for r in rows) > 0
    assert desk.backfill("kr", SERIES, 90, now) == 0                 # again: nothing new


def test_the_api_needs_a_session_checks_the_market_and_answers_empty(db_url):
    from tests.test_api import OWNER, PASSWORD, _client

    with _client(db_url, login=False) as client:
        assert client.get("/api/day-call").status_code == 401
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        assert client.get("/api/day-call?market=jp").status_code == 422
        body = client.get("/api/day-call?market=us").json()
        assert body["calls"] == {"prev_close": None, "open": None} and body["note"]
        assert client.get("/api/day-call/history?market=kr&days=9999").json()["days"] == 365
        stats = client.get("/api/day-call/stats?market=kr").json()
        assert stats["calls"]["prev_close"]["n"] == 0 and stats["calls"]["open"]["hold_rate"] is None
        # a stored call is served with its record
        today = datetime.now(dc.KST).date()
        with make_engine(db_url).begin() as conn:
            conn.execute(insert(t).values(
                market="kr", trade_date=today, round=1, call="prev_close", model="prev_close",
                model_version="dc-x", source="live", called_at=datetime.now(UTC), cutoff_at=datetime.now(UTC),
                stage="weak_long", verdict="long",
                probs={"strong_long": 10.0, "weak_long": 40.0, "flat": 30.0, "weak_short": 15.0, "strong_short": 5.0},
                reasons=[], edges_pct={"flat": 0.4, "strong": 1.3}, features={}))
        got = client.get("/api/day-call?market=kr").json()
        assert got["today"] is True and got["calls"]["prev_close"]["label"] == "약한 롱"
        assert got["calls"]["prev_close"]["record"]["basis"] == "backtest" and got["calls"]["open"] is None
    assert "day_call" in push.KINDS
