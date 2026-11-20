"""#694 each holding's stop / take-profit alerts by its regime (desk.holding_watch)."""

import asyncio
import re
from datetime import UTC, datetime, timedelta

import pytest

from desk import alert_tone, holding_watch, market_alerts, regime_live
from tests.test_regime_live import _range, _trend

NOW = datetime(2026, 11, 17, 15, 0, tzinfo=UTC)          # 10:00 ET Tuesday: the US market trades


def _lines(bars, average=100.0, max_loss=8.0):
    cls = regime_live.classify(bars, "1d")
    return cls, holding_watch.lines(cls, bars, average, max_loss)


def test_a_range_stops_under_its_box_and_takes_profit_near_its_top():
    cls, ln = _lines(_range(), average=97.0)
    assert ln["regime"] == "range" and not ln["stop_is_max_loss"]
    assert ln["stop"] == pytest.approx(cls["box"]["low"] - holding_watch.BREAK_ATR * cls["atr"])
    assert ln["take_profit"] == pytest.approx(cls["box"]["high"] - 0.25 * cls["atr"])
    assert ln["stop_basis"].startswith("박스 하단") and ln["tp_basis"].startswith("박스 상단")


def test_a_trend_trails_and_a_far_structure_leaves_the_max_loss_line():
    bars = _trend()
    cls, ln = _lines(bars, average=bars[-1]["close"])
    top = max(b["high"] for b in bars[-holding_watch.TRAIL_N:])
    assert ln["regime"] == "trend_up" and ln["stop"] == pytest.approx(top - holding_watch.TRAIL_ATR * cls["atr"]) and ln["take_profit"] is None
    _, far = _lines(_range(), average=130.0)                 # bought far above the box: the loss line first
    assert far["stop_is_max_loss"] and far["stop"] == pytest.approx(130 * 0.92)
    assert far["stop_basis"] == "최대 손실(평단 −8%)"


def test_the_rules_and_their_words_give_neither_amount_nor_average():
    _, ln = _lines(_range(), average=97.0)
    below = holding_watch.judge("AAA", ln["stop"] * 0.99, 97.0, ln, NOW)
    assert [a["rule"] for a in below] == ["hold_stop"] and below[0]["severity"] == "strong"
    near = holding_watch.judge("AAA", ln["stop"] + 0.2 * ln["atr"], 97.0, ln, NOW)
    assert [a["rule"] for a in near] == ["hold_near_stop"]
    top = holding_watch.judge("AAA", ln["take_profit"] + 0.1, 97.0, ln, NOW)
    assert [a["rule"] for a in top] == ["hold_take_profit"]
    assert holding_watch.judge("AAA", ln["take_profit"] + 0.1, 120.0, ln, NOW) == []     # at a loss: no profit
    words = market_alerts.text(below[0])
    assert words.startswith("🔴 경고 · AAA 손절선 아래 · 현재 ") and "일봉 박스권" in words and "평단" not in words
    assert re.search(r"손절선 [\d.,]+ 1\.0% 아래", words)
    assert "97" not in words.split("평단 대비")[0] and "수량" not in words
    assert alert_tone.tone(top[0]) == "positive" and alert_tone.tone(near[0]) == "caution"
    _, by_loss = _lines(_range(), average=130.0)
    loss = market_alerts.text(holding_watch.judge("AAA", 110.0, 130.0, by_loss, NOW)[0])
    assert "손절선 = 평단 손실 한도" in loss and not re.search(r"119\.6|119,6", loss)   # 130 x 0.92 is not said
    assert "%" not in loss.splitlines()[0].split("현재")[1]          # no distance to it, no return on the average


def test_a_downtrend_says_reduce_and_each_rule_is_once_a_day_with_a_stop_past_the_cooldown():
    bars = _trend(step=-0.008)
    _, ln = _lines(bars, average=bars[-1]["close"] * 1.02)
    got = holding_watch.judge("AAA", bars[-1]["close"], bars[-1]["close"] * 1.02, ln, NOW)
    assert "hold_trend_down" in [a["rule"] for a in got]
    first = {**got[-1]}
    later = {**first, "t": (datetime.fromisoformat(first["t"]) + timedelta(hours=3)).isoformat()}
    assert not market_alerts.admit(later, [first])                                 # once a KST day
    near = {**first, "rule": "hold_near_stop"}
    stop = {**first, "rule": "hold_stop", "severity": "strong",
            "t": (datetime.fromisoformat(first["t"]) + timedelta(minutes=5)).isoformat()}
    assert market_alerts.admit(stop, [near])                                       # the stop goes at once
    other = {**later, "market": "BBB"}
    assert market_alerts.admit(other, [first])                                     # another holding


def test_settings_clean_and_per_ticker():
    assert holding_watch.clean({})["enabled"] is False                               # off until turned on
    cfg = holding_watch.clean({"enabled": True, "max_loss_pct": 5, "tickers": {"AAA": {"enabled": False}, "BBB": {"max_loss_pct": 3}}})
    assert holding_watch.for_ticker(cfg, "AAA") == (False, 5.0)
    assert holding_watch.for_ticker(cfg, "BBB") == (True, 3.0) and holding_watch.for_ticker(cfg, "CCC") == (True, 5.0)
    with pytest.raises(ValueError):
        holding_watch.clean({"max_loss_pct": 80})


class _Book:
    def __init__(self, rows):
        self.rows = rows

    async def snapshot(self):
        return {"holdings": self.rows}


class _Prices:
    def __init__(self, bars):
        self.bars = bars

    async def candles(self, ticker, tf, count):
        return {"bars": self.bars}


@pytest.fixture
def watch(db_url):
    from desk.db import make_engine, migrate

    migrate(db_url)
    engine = make_engine(db_url)
    yield holding_watch.HoldingWatch(engine)
    engine.dispose()


def test_a_pass_judges_the_open_markets_holdings_and_keeps_their_lines(watch, monkeypatch):
    async def is_open(calendar, ticker, now=None):
        return ticker != "005930.KS"

    monkeypatch.setattr(holding_watch.live, "is_open", is_open)
    bars = _range()
    _, ln = _lines(bars, average=97.0)
    rows = [{"yahoo_ticker": "AAA", "last_price": ln["stop"] * 0.99, "average_price": 97.0, "quantity": 12},
            {"yahoo_ticker": "005930.KS", "last_price": 70000, "average_price": 71000, "quantity": 40},
            {"yahoo_ticker": "OFF", "last_price": 1.0, "average_price": 2.0, "quantity": 1}]
    sent = []
    assert asyncio.run(watch.run_pass(_Book(rows), _Prices(bars), None, lambda a: a, NOW)) == 0   # off by default
    watch.save({"enabled": True, "tickers": {"OFF": {"enabled": False}}})
    n = asyncio.run(watch.run_pass(_Book(rows), _Prices(bars), None, lambda a: sent.extend(a) or a, NOW))
    assert n == 1 and [(a["market"], a["rule"]) for a in sent] == [("AAA", "hold_stop")]
    assert set(watch.last) == {"AAA"} and "quantity" not in watch.last["AAA"]
    assert watch.last["AAA"]["regime"] == "range" and watch.last["AAA"]["to_stop_pct"] < 0
    watch.save({"enabled": False})
    assert asyncio.run(watch.run_pass(_Book(rows), _Prices(bars), None, lambda a: a, NOW)) == 0 and watch.last == {}


def test_settings_round_trip(watch):
    assert watch.settings() == holding_watch.DEFAULT
    watch.save({"max_loss_pct": 6, "tickers": {"AAA": {"enabled": False}}})
    watch.save({"tickers": {"BBB": {"max_loss_pct": 4}}})
    got = watch.settings()
    assert got["max_loss_pct"] == 6 and got["tickers"]["AAA"]["enabled"] is False and got["enabled"] is False
    assert got["tickers"]["BBB"] == {"enabled": True, "max_loss_pct": 4.0}


def test_api_holding_watch(db_url):
    from datetime import timedelta as td

    from desk.auth import Auth
    from desk.db import make_engine
    from tests.test_api import OWNER, PASSWORD, _client

    with _client(db_url, login=False) as client:
        assert client.get("/api/holding-watch").status_code == 401
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        body = client.get("/api/holding-watch").json()
        assert body["enabled"] is False and body["max_loss_pct"] == 8.0 and body["can_edit"] is True
        assert body["holdings"] == [] and body["limits"]["max_loss_pct"] == [1.0, 50.0]
        assert client.put("/api/holding-watch", json={"max_loss_pct": 70}).status_code == 422
        assert client.put("/api/holding-watch", json={"tickers": {"bad ticker!": {}}}).status_code == 422
        got = client.put("/api/holding-watch", json={"max_loss_pct": 5, "tickers": {"005930.ks": {"enabled": False}}})
        assert got.status_code == 200, got.text
        assert got.json()["tickers"] == {"005930.KS": {"enabled": False, "max_loss_pct": None}}
        client.app.state.holding_watch.last = {"005930.KS": {"ticker": "005930.KS", "pnl_pct": -3.1}}
        assert [h["ticker"] for h in client.get("/api/holding-watch").json()["holdings"]] == ["005930.KS"]
        engine = make_engine(db_url)
        Auth(engine, td(hours=1), td(hours=1)).create_user("other@example.com", PASSWORD)
        engine.dispose()
        client.post("/api/auth/logout")
        client.post("/api/auth/login", json={"username": "other@example.com", "password": PASSWORD})
        theirs = client.get("/api/holding-watch").json()
        assert theirs["can_edit"] is False
        # #762 another account sees the default setting and no lines: not which tickers, limits or returns
        assert (theirs["enabled"], theirs["max_loss_pct"], theirs["tickers"], theirs["holdings"]) == (False, 8.0, {}, [])
        assert "005930" not in str(theirs)
        assert client.put("/api/holding-watch", json={"enabled": False}).status_code == 403


def test_a_holding_alert_pushes_to_the_owners_devices_only(db_url, monkeypatch):
    from desk import notify
    from desk.auth import Auth
    from desk.db import make_engine, migrate

    migrate(db_url)
    engine = make_engine(db_url)
    auth = Auth(engine, timedelta(hours=1), timedelta(hours=1))
    auth.create_user("owner@example.com", "pw-123456789")
    auth.create_user("other@example.com", "pw-123456789")

    class _Pusher:
        def __init__(self):
            self.calls, self.tags = [], []

        def broadcast(self, *kinds, payload, user_id=None):
            self.calls.append((kinds, user_id))
            self.tags.append(payload["tag"])
            return 1

    monkeypatch.setattr(notify, "_channels", {})
    pusher = _Pusher()
    alerts = market_alerts.MarketAlerts(engine, pusher)
    _, ln = _lines(_range(), average=97.0)
    alerts.dispatch(holding_watch.judge("AAA", ln["stop"] * 0.99, 97.0, ln, NOW))
    nq = {"kind": "nq_alert", "rule": "nq_drop", "market": "nq", "who": "all", "severity": "caution",
          "t": NOW.astimezone(holding_watch.KST).isoformat(), "value": -0.5, "prev": -0.3}
    alerts.dispatch([nq])
    assert pusher.calls == [(("holding_alert", "tone_warning"), 1), (("nq_alert", "tone_warning"), None)]
    assert pusher.tags == ["holding_alert-AAA-hold_stop", "nq_alert-nq"]     # a stop's not replaced by a caution
    engine.dispose()


def _hold(rule, t, ticker="AAA", severity="caution"):
    return {"kind": "holding_alert", "rule": rule, "market": ticker, "who": "all", "severity": severity,
            "t": t, "ticker": ticker, "value": -1.0, "prev": 1.0}


def test_a_us_holding_is_once_a_session_across_the_kst_midnight():
    first = _hold("hold_trend_down", "2026-11-17T23:31:00+09:00")          # 09:31 ET
    after = _hold("hold_trend_down", "2026-11-18T00:10:00+09:00")          # 10:10 ET: the same session
    assert not market_alerts.admit(after, [], [first])
    next_day = _hold("hold_trend_down", "2026-11-18T23:31:00+09:00")
    assert market_alerts.admit(next_day, [], [after])
    kr = _hold("hold_trend_down", "2026-11-18T09:05:00+09:00", "005930.KS")
    assert market_alerts.admit(kr, [], [_hold("hold_trend_down", "2026-11-17T15:00:00+09:00", "005930.KS")])


def test_a_stop_goes_past_a_day_full_of_other_holdings_cautions():
    full = [_hold("hold_trend_down", f"2026-11-17T{9 + i:02d}:00:00+09:00", f"T{i}.KS")
            for i in range(market_alerts.DAILY_MAX + market_alerts.STRONG_EXTRA)]
    assert not market_alerts.admit(_hold("hold_near_stop", "2026-11-17T23:40:00+09:00", "ZZZ.KS"), full)
    assert market_alerts.admit(_hold("hold_stop", "2026-11-17T23:40:00+09:00", "ZZZ.KS", "strong"), full)


def test_the_finished_daily_bars_leave_out_today_by_the_holdings_own_market():
    kr = [{"at": "2026-11-16T15:00:00+00:00"}, {"at": "2026-11-17T15:00:00+00:00"}]      # 11-17 · 11-18 KST
    assert holding_watch._finished(kr, datetime(2026, 11, 18, 1, 0, tzinfo=UTC), "005930.KS") == kr[:1]
    us = [{"at": "2026-11-16T05:00:00+00:00"}, {"at": "2026-11-17T05:00:00+00:00"}]      # 11-16 · 11-17 ET
    assert holding_watch._finished(us, datetime(2026, 11, 17, 15, 0, tzinfo=UTC), "AAA") == us[:1]
    assert holding_watch._finished(us, datetime(2026, 11, 18, 15, 0, tzinfo=UTC), "AAA") == us


def test_a_pass_skips_closed_markets_and_one_holding_failing(watch, monkeypatch):
    calls = {"snap": 0}

    class _Snap(_Book):
        async def snapshot(self):
            calls["snap"] += 1
            return await super().snapshot()

    async def closed(calendar, ticker, now=None):
        return False

    watch.save({"enabled": True})
    monkeypatch.setattr(holding_watch.live, "is_open", closed)
    assert asyncio.run(watch.run_pass(_Snap([]), _Prices([]), None, lambda a: a, NOW)) == 0 and calls["snap"] == 0

    async def opened(calendar, ticker, now=None):
        return True

    class _Some(_Prices):
        def __init__(self, bars):
            super().__init__(bars)
            self.asked = []

        async def candles(self, ticker, tf, count):
            self.asked.append(ticker)
            if ticker == "BAD":
                raise ConnectionError("down")
            return await super().candles(ticker, tf, count)

    monkeypatch.setattr(holding_watch.live, "is_open", opened)
    bars = _range()
    _, ln = _lines(bars, average=97.0)
    rows = [{"yahoo_ticker": "BAD", "last_price": 1.0, "average_price": 2.0},
            {"yahoo_ticker": "AAA", "last_price": ln["stop"] * 0.99, "average_price": 97.0}]
    prices, sent = _Some(bars), []
    assert asyncio.run(watch.run_pass(_Book(rows), prices, None, lambda a: sent.extend(a) or a, NOW)) == 1
    asyncio.run(watch.run_pass(_Book(rows), prices, None, lambda a: a, NOW + timedelta(minutes=1)))
    assert prices.asked == ["BAD", "AAA", "BAD"]                     # AAA's daily bars: once a market date


def test_settings_merge_a_tickers_fields_and_null_removes_it(watch):
    watch.save({"tickers": {"AAA": {"enabled": False}}})
    watch.save({"tickers": {"AAA": {"max_loss_pct": 6}}})
    assert watch.settings()["tickers"]["AAA"] == {"enabled": False, "max_loss_pct": 6.0}
    watch.save({"tickers": {"AAA": None}})
    assert watch.settings()["tickers"] == {}


def test_a_closed_markets_holding_keeps_its_lines_from_its_own_session(watch, monkeypatch):
    is_us_open = {"v": True}

    async def is_open(calendar, ticker, now=None):
        return ticker.endswith(".KS") != is_us_open["v"]

    monkeypatch.setattr(holding_watch.live, "is_open", is_open)
    watch.save({"enabled": True})
    bars = _range()
    rows = [{"yahoo_ticker": "AAA", "last_price": 100.0, "average_price": 97.0},
            {"yahoo_ticker": "005930.KS", "last_price": 100.0, "average_price": 97.0}]
    asyncio.run(watch.run_pass(_Book(rows), _Prices(bars), None, lambda a: a, NOW))
    assert set(watch.last) == {"AAA"}
    is_us_open["v"] = False                                          # now Seoul trades, New York is shut
    asyncio.run(watch.run_pass(_Book(rows), _Prices(bars), None, lambda a: a, NOW + timedelta(hours=10)))
    assert set(watch.last) == {"AAA", "005930.KS"} and watch.last["AAA"]["at"] == NOW.isoformat(timespec="seconds")
    watch.save({"tickers": {"AAA": {"enabled": False}}})              # turned off: gone, not kept
    asyncio.run(watch.run_pass(_Book(rows), _Prices(bars), None, lambda a: a, NOW + timedelta(hours=10)))
    assert set(watch.last) == {"005930.KS"}
