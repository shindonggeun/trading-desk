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
    assert "11월 19일 -7.7% 내려 장기 지지선 480.55 을 시험 중" in ln["explain"]
    # the /api/lines trendline fields stay as they are (desk.channels' shape)
    for key in ("timeframe", "kind", "anchors", "end", "touches", "slope_pct_per_bar", "state", "broken_at",
                "broken_price", "fresh_break", "distance_atr", "explain"):
        assert key in ln


def test_soxx_weekly_support_under_this_weeks_drop():
    got = long_lines.lines(_fix("SOXX", "weekly"), "1w")
    sup = next(ln for ln in got if ln["kind"] == "support")
    assert len(sup["touches"]) >= 4 and sup["state"] == "testing" and " 주 " in sup["explain"]


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


def test_the_chart_reads_finished_bars_only():
    days, weeks = _fix("AMD"), _fix("AMD", "weekly")
    live = long_lines.for_chart([*days, {**days[-1], "at": "2026-11-20T05:00:00+00:00", "close": 300.0,
                                         "low": 290.0}], weeks, live=True)
    assert live == long_lines.for_chart(days, weeks, live=False)        # the bar still trading is not read
    # the week of 11-16 is not over on Thursday 11-19: the weekly lines are those of the weeks to 11-09
    assert weeks[-1]["at"][:10] == "2026-11-16"
    assert [ln for ln in live if ln["timeframe"] == "1w"] == long_lines.lines(weeks[:-1], "1w")
    assert [ln for ln in live if ln["timeframe"] == "1d"] == long_lines.lines(days, "1d")


def test_api_lines_carries_the_long_lines_on_a_daily_chart(db_url):
    from datetime import timedelta as td

    from fastapi.testclient import TestClient

    from desk.api import create_app
    from desk.auth import Auth
    from desk.broker import Bar, MockBroker
    from desk.db import make_engine
    from desk.prices import PriceSource
    from desk.runner import make_fake_runner
    from desk.settings import Settings
    from tests.test_api import OWNER, PASSWORD

    asked = []

    def yahoo(ticker, interval, count):
        asked.append((ticker, interval))
        src = _fix("AMD", "weekly" if interval == "1w" else "daily")
        return [Bar(b["at"], b["open"], b["high"], b["low"], b["close"], b["volume"]) for b in src][-count:]

    settings = Settings(database_url=db_url, fake_delay=0, fake_runs=True)
    client = TestClient(create_app(settings, runner=make_fake_runner(0),
                                   prices=PriceSource(MockBroker(), yahoo_candles=yahoo)),
                        base_url="https://testserver")
    engine = make_engine(db_url)
    Auth(engine, td(hours=1), td(hours=1)).create_user(OWNER, PASSWORD)
    engine.dispose()
    with client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        body = client.get("/api/lines?ticker=AMD&tf=1d").json()
        assert body["long_lines"] and all(ln["long"] for ln in body["long_lines"])
        assert any(ln["id"].startswith("1d-long-support-") for ln in body["long_lines"])
        assert ("AMD", "1w") in asked
        assert client.get("/api/lines?ticker=AMD&tf=5m").json()["long_lines"] == []


def test_a_daily_bar_arriving_at_a_long_line_is_a_long_signal():
    from datetime import UTC, datetime

    from desk import line_signals

    days = _fix("AMD")
    upto = [b for b in days if b["at"][:10] <= "2026-11-02"]
    got = [s for s in line_signals.detect(upto, "1d", datetime(2030, 1, 1, tzinfo=UTC)) if s["event"] == "long"]
    sup = [s for s in got if s["rule"] == "long_support"]
    assert [s["at"][:10] for s in sup] == ["2026-11-02"] and "5월 19일부터의" in sup[0]["line"]
    title, body = line_signals.text(sup[0], "AMD", "1d", {})
    assert "장기 지지 시험" in title and "장기 상승 지지선" in title and "AMD 일봉" in title
    assert "장기 선" in body and "짧은 봉" not in body                    # rate line, no short-bar warning
    soxx = [b for b in _fix("SOXX") if b["at"][:10] <= "2026-11-02"]
    rules = [s["rule"] for s in line_signals.detect(soxx, "1d", datetime(2030, 1, 1, tzinfo=UTC))]
    assert "long_break_down" in rules                                   # its second close under the line


def test_the_watch_takes_daily_bars_and_the_long_event():
    from desk import line_signals

    got = line_signals.clean({"enabled": True, "tickers": ["AMD"], "timeframes": ["1d"], "events": {"long": True}})
    assert got["timeframes"] == ["1d"] and got["events"]["long"] is True


def test_the_rate_line_reads_the_long_table():
    from desk import long_lines_bt

    table = {"horizon_bars": 10, "roles": {"long_support": {"n": 40, "bounce_rate": 0.55, "break_rate": 0.3,
                                                             "baseline": {"bounce_rate": 0.45}}}}
    assert long_lines_bt.rate_text("long_support", table) == \
        "과거 장기 선에 닿은 뒤 10봉 안 반등 55% · 이탈 30% (같은 자리 아무 선 반등 45%, 40번)"
    assert "아직" in long_lines_bt.rate_text("long_resistance", table)
    assert "이탈 뒤" in long_lines_bt.rate_text("long_break", table)
    # #613 the measured 40% against 36% (#679): about any line's - the line says it is only a note
    thin = {"horizon_bars": 10, "roles": {"long_support": {"n": 2100, "bounce_rate": 0.40, "break_rate": 0.31,
                                                            "baseline": {"bounce_rate": 0.36}}}}
    assert long_lines_bt.rate_text("long_support", thin).endswith("2100번) · 차이 작음")
    from desk.lines_bt import edge_note
    assert edge_note(0.40, 0.35) == "" and edge_note(0.404, 0.3545) == ""      # shown 40% against 35%: 5%p, either way
    assert edge_note(0.40, 0.36) == " · 차이 작음" and edge_note(None, 0.36) == ""


def test_the_backtest_counts_a_line_once_and_beats_nothing_on_a_random_walk():
    import random

    from desk import long_lines_bt

    rng = random.Random(7)
    price, bars, day = 100.0, [], date(2010, 1, 1)
    for x in range(900):
        price *= math.exp(rng.gauss(0.0003, 0.015))
        bars.append({"at": (day + timedelta(days=x)).isoformat(), "open": price, "high": price * 1.008,
                     "low": price * 0.992, "close": price})
    got = long_lines_bt.backtest(bars, step=10)
    sup = got["roles"]["long_support"]["line"]
    assert got["steps"] > 50 and sup["n"] == sup["bounce"] + sup["break"] + sup["none"]


def test_a_korean_days_dates_read_in_kst():
    # review #679: Yahoo stamps a Korean day at 15:00 UTC of the day before - the sentence's dates are KST
    assert long_lines._day("2026-11-19T15:00:00+00:00") == "11월 20일"
    assert long_lines._day("2026-11-19T05:00:00+00:00") == "11월 19일"           # a US day: the same date
    kr = [{**b, "at": f"{date.fromisoformat(b['at'][:10]) - timedelta(days=1)}T15:00:00+00:00"} for b in _fix("AMD")]
    ln = next(ln for ln in long_lines.lines(kr, "1d") if ln["kind"] == "support")
    assert "5월 19일부터" in ln["explain"] and "11월 19일 -7.7%" in ln["explain"]


def test_a_fresh_break_far_below_is_still_shown():
    # review #679: a crash two closes deep past the line is the news, not a line out of reach
    bars = _bars(202, closes_off={200: -0.35, 201: -0.40})
    ln = next(ln for ln in long_lines.lines(bars, "1d") if ln["kind"] == "support")
    assert ln["state"] == "broken" and ln["distance_pct"] > 25


def test_a_setting_stored_before_the_long_event_reads_it_as_default(db_url):
    from sqlalchemy import insert

    from desk import line_signals
    from desk.db import line_watch, make_engine, migrate

    migrate(db_url)
    engine = make_engine(db_url)
    with engine.begin() as conn:
        conn.execute(insert(line_watch).values(id=1, updated_at=__import__("datetime").datetime(2026, 11, 1), body={"enabled": True, "tickers": ["AMD"], "timeframes": ["1d"],
                                                           "events": {"touch": True, "break": True, "retest": False}}))
    watch = line_signals.LineWatch(engine)
    assert watch.settings()["events"]["long"] is True and watch.settings()["events"]["retest"] is False
    engine.dispose()
