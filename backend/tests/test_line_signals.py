"""#613 channel / trendline signals: what a finished bar sets off, the gates, the text and the watch API."""

import asyncio
import logging
import math
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest

from desk import channels, line_signals
from tests.test_channels import FIVE, HOURLY

LATER = datetime(2030, 1, 1, tzinfo=UTC)            # every bar of the fixtures has ended by then


def _signals(bars, tf, start):
    """Every signal of every finished bar from ``start`` on, as the loop would meet them bar by bar."""
    out = []
    for m in range(start, len(bars) + 1):
        out += [(m - 1, s) for s in line_signals.detect(bars[:m], tf, LATER)]
    return out


def test_a_channel_touch_from_inside_is_a_support_or_resistance_touch():
    got = _signals(HOURLY, "60m", 200)
    rules = {(s["rule"], s["role"]) for _, s in got}
    assert ("touch_support", "channel_lower") in rules and ("touch_resistance", "channel_upper") in rules
    sig = next(s for _, s in got if s["role"] == "channel_lower")
    assert sig["line"] == "하락 채널 하단" and sig["event"] == "touch" and sig["line_id"].startswith("60m-channel-")
    assert abs(math.log(sig["price"] / sig["line_price"])) < 0.02
    # an arrival, not a stay: no two touches of the same line on neighbouring bars
    seen = sorted((s["line_id"], s["rule"], m) for m, s in got if s["event"] == "touch")
    assert all(not (a[:2] == b[:2] and b[2] - a[2] == 1) for a, b in pairwise(seen))


def test_a_trendline_break_is_signalled_on_the_bar_that_closed_through_it():
    got = _signals(FIVE, "5m", len(FIVE) - 6)
    breaks = [(m, s) for m, s in got if s["rule"] == "break_down" and s["role"] == "trend_break"]
    assert breaks, got
    m, sig = breaks[0]
    assert sig["at"] == FIVE[m]["at"] and sig["line"] == "상승 추세선" and sig["price"] < sig["line_price"]


def test_a_bar_still_trading_is_not_judged():
    m = next(m for m, s in _signals(FIVE, "5m", len(FIVE) - 6) if s["rule"] == "break_down")
    bars = FIVE[:m + 1]
    trading = datetime.fromisoformat(bars[-1]["at"]) + timedelta(minutes=2)     # the break bar is still open
    assert not [s for s in line_signals.detect(bars, "5m", trading) if s["rule"] == "break_down"]


def test_a_retest_of_a_broken_support_from_below():
    m = next(m for m, s in _signals(FIVE, "5m", len(FIVE) - 6) if s["rule"] == "break_down")
    bars = FIVE[:m + 1]
    k, a = channels.SWING_K["5m"], channels.atr(bars)
    line = next(t for t in channels.find_trendlines(bars, k, a / bars[-1]["close"]) if t.broken_at == m)
    t0 = datetime.fromisoformat(bars[-1]["at"])
    for x in range(1, 4):                             # back up to the line, the third bar closing under it
        y = line.at(m + x)
        high, close = (y, y - 0.0012) if x == 3 else (y - 0.004, y - 0.005)
        bars.append({"at": (t0 + timedelta(minutes=5 * x)).isoformat(), "open": math.exp(close - 0.0005),
                     "high": math.exp(high), "low": math.exp(close - 0.001), "close": math.exp(close), "volume": 1.0})
    got = line_signals.detect(bars, "5m", LATER)
    assert any(s["rule"] == "retest_down" and s["role"] == "trend_retest" for s in got), got


def _row(ticker="NQ=F", tf="60m", rule="touch_support", line="L1", at="2026-11-18T01:00:00+00:00"):
    return {"ticker": ticker, "timeframe": tf, "event": rule.split("_")[0], "rule": rule, "line_id": line, "at": at}


def _sig(rule="touch_support", line="L1", at="2026-11-18T04:00:00+00:00"):
    return {"event": rule.split("_")[0], "rule": rule, "line_id": line, "at": at}


def test_the_gates():
    admit = line_signals.admit
    assert admit(_sig(), "NQ=F", "60m", [])
    assert not admit(_sig(), "NQ=F", "60m", [_row()])                                  # same line · signal: once a day
    near = "2026-11-18T03:00:00+00:00"                                                # 1 hour < 2 bars of 60m
    assert not admit(_sig(line="L2"), "NQ=F", "60m", [_row(at=near)])                 # same kind cooled down
    assert admit(_sig(rule="break_down", line="L2"), "NQ=F", "60m", [_row(at=near)])   # another kind
    assert admit(_sig(line="L2"), "NQ=F", "5m", [_row(tf="5m", at="2026-11-18T03:25:00+00:00")])   # 35 min > 30
    assert admit(_sig(line="L2"), "SOXL", "60m", [_row(at=near)])                     # another ticker
    full = [_row(ticker=f"T{i}", line=f"L{i}", at="2026-11-17T20:00:00+00:00") for i in range(line_signals.DAILY_MAX)]
    assert not admit(_sig(line="X"), "NQ=F", "60m", full)
    mine = [_row(line=f"L{i}", rule="touch_support", at=f"2026-11-17T{10 + i}:00:00+00:00")
            for i in range(line_signals.PER_TICKER_MAX)]
    assert not admit(_sig(rule="break_up", line="X"), "NQ=F", "60m", mine)
    assert admit(_sig(rule="break_up", line="X"), "SOXL", "60m", mine)


RATES = {"channel": {"upper": {"n": 474, "bounce_rate": 0.352, "break_rate": 0.57, "baseline_bounce_rate": 0.383,
                               "horizon_bars": 8}},
         "trend_break": {"n": 62, "follow_rate": 0.435, "baseline_rate": 0.608, "horizon_bars": 8}}


def test_the_text_follows_the_issue_example():
    sig = {"event": "touch", "rule": "touch_resistance", "role": "channel_upper", "line": "하락 채널 상단",
           "line_id": "x", "at": "2026-11-18T04:00:00+00:00", "price": 27180.0, "line_price": 27160.0}
    title, body = line_signals.text(sig, "SOXL", "60m", RATES)
    assert title == "🔵 저항 · 참고 | SOXL 1시간봉 — 가격이 저항 구간(하락 채널 상단)에 닿았어요 → 상승이 막히는지 지켜볼 자리"
    lines = body.split("\n")
    assert lines[0] == "선 27,160 · 지금 27,180 (+0.07%)"
    # 35% against any line's 38%: within WEAK_EDGE, so it says the line adds little (#613 "참고" tone)
    assert lines[1] == "과거 닿은 뒤 8봉 안 반등 35% · 이탈 57% (같은 자리 아무 선 반등 38%, 474번) · 차이 작음"
    assert lines[2] == "⚠️ 1시간봉은 짧은 봉이라 신호가 자주 틀릴 수 있어요"
    assert lines[3] == "※ 매수 · 매도 추천이 아닙니다"
    brk = {**sig, "event": "break", "rule": "break_down", "role": "trend_break", "line": "상승 추세선"}
    title, body = line_signals.text(brk, "NQ=F", "240m", RATES)
    assert title.startswith("🔻 하향 이탈 · 참고 | NQ=F 4시간봉 — 종가가 상승 추세선 아래로 넘었어요")
    assert "1 ATR 더 간 비율 44% (같은 자리 아무 선 61%, 62번)\n" in body and "짧은 봉" not in body   # 17%p apart: no note
    _t, body = line_signals.text({**sig, "role": "channel_break", "rule": "break_up"}, "KORU", "15m", RATES)
    assert "이 사건은 따로 재지 않았어요" in body and body.endswith("※ 매수 · 매도 추천이 아닙니다")
    _t, body = line_signals.text(sig, "KORU", "15m", {})
    assert "이 선 · 시간봉은 아직 재지 않았어요" in body


def test_the_setting_is_checked():
    assert line_signals.clean({"timeframes": ["1h", "2h", "60m"]})["timeframes"] == ["60m", "120m"]
    for bad in ({"timeframes": ["1M"]}, {"events": {"cross": True}},
                {"tickers": [f"T{i}" for i in range(line_signals.MAX_TICKERS + 1)]}):
        with pytest.raises(ValueError):
            line_signals.clean(bad)
    assert line_signals.clean({})["events"] == {"touch": True, "break": True, "retest": True, "long": True}


@pytest.fixture
def watch(db_url):
    from desk.db import make_engine, migrate

    migrate(db_url)
    engine = make_engine(db_url)
    yield line_signals.LineWatch(engine)
    engine.dispose()


def _break_bars():
    m = next(m for m, s in _signals(FIVE, "5m", len(FIVE) - 6) if s["rule"] == "break_down")
    return FIVE[:m + 1]


def test_a_scan_sends_once_and_keeps_it(watch, monkeypatch, caplog):
    sent = []
    monkeypatch.setattr(line_signals.notify, "send", lambda pusher, *kinds, payload: sent.append((kinds, payload)) or 0)
    bars = _break_bars()
    end = datetime.fromisoformat(bars[-1]["at"]) + timedelta(minutes=5)
    watch.clock = lambda: end + timedelta(seconds=30)
    assert watch.settings()["enabled"] is False                          # off until the owner turns it on
    cfg = watch.save({"enabled": True, "tickers": ["NQ=F"], "timeframes": ["5m"]})
    with caplog.at_level(logging.DEBUG):
        got = watch.scan("NQ=F", "5m", bars, cfg=cfg)
    assert [s["rule"] for s in got] == ["break_down"]
    kinds, payload = sent[0]
    # #613 "참고": under tone_neutral (a warnings-only device skips it); it opens that chart
    assert kinds == ("line_signal", "tone_neutral") and payload["title"].startswith("🔻 하향 이탈 · 참고 | NQ=F 5분봉")
    assert payload["body"].endswith("※ 매수 · 매도 추천이 아닙니다") and payload["url"] == "/#/charts?t=NQ%3DF&iv=5m"
    assert payload["tone"] == "neutral" and line_signals.chart_url("005930.KS", "120m") == "/#/charts?t=005930.KS&iv=60m"
    assert watch.recent()[0]["rule"] == "break_down"
    assert watch.scan("NQ=F", "5m", bars, cfg=cfg) == [] and len(sent) == 1   # the same bar again: nothing
    off = watch.save({**cfg, "events": {"break": False}})
    assert off["events"]["break"] is False
    assert not caplog.records or all("token" not in r.getMessage().lower() for r in caplog.records)


def test_an_old_bar_is_not_news(watch, monkeypatch):
    sent = []
    monkeypatch.setattr(line_signals.notify, "send", lambda pusher, *kinds, payload: sent.append(payload) or 0)
    bars = _break_bars()
    watch.clock = lambda: datetime.fromisoformat(bars[-1]["at"]) + timedelta(hours=3)   # the server was down
    cfg = watch.save({"enabled": True, "tickers": ["NQ=F"], "timeframes": ["5m"]})
    assert watch.scan("NQ=F", "5m", bars, cfg=cfg) == [] and not sent


def test_the_loop_reads_a_ticker_only_when_its_bar_has_ended(watch, monkeypatch):
    monkeypatch.setattr(line_signals.notify, "send", lambda pusher, *kinds, payload: 0)
    bars = _break_bars()
    asked = []

    class Prices:
        async def candles(self, ticker, tf, count):
            asked.append((ticker, tf, count))
            return {"bars": bars}

    end = datetime.fromisoformat(bars[-1]["at"]) + timedelta(minutes=5)
    now = [end + timedelta(seconds=30)]
    watch.clock = lambda: now[0]
    assert asyncio.run(line_signals.run_pass(watch, Prices(), None)) == 0 and not asked     # off: nothing read
    watch.save({"enabled": True, "tickers": ["NQ=F"], "timeframes": ["5m"]})
    assert asyncio.run(line_signals.run_pass(watch, Prices(), None)) == 1
    assert asked == [("NQ=F", "5m", channels.BARS["5m"])]
    asyncio.run(line_signals.run_pass(watch, Prices(), None))
    assert len(asked) == 1                              # the market closed: read again only after a while
    now[0] += timedelta(seconds=line_signals.IDLE_SEC)
    asyncio.run(line_signals.run_pass(watch, Prices(), None))
    assert len(asked) == 2


def test_api_line_watch(db_url):
    from datetime import timedelta as td

    from desk.auth import Auth
    from desk.db import make_engine
    from tests.test_api import OWNER, PASSWORD, _client

    with _client(db_url, login=False) as client:
        assert client.get("/api/notify/line-watch").status_code == 401
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        body = client.get("/api/notify/line-watch").json()
        assert body["enabled"] is False and body["can_edit"] is True and body["recent"] == []
        assert body["allowed"]["timeframes"] == list(line_signals.TIMEFRAMES)
        assert client.put("/api/notify/line-watch", json={"timeframes": ["1M"]}).status_code == 422
        assert client.put("/api/notify/line-watch", json={"tickers": ["bad ticker!"]}).status_code == 422
        too_many = {"tickers": [f"T{i}" for i in range(line_signals.MAX_TICKERS + 1)]}
        assert client.put("/api/notify/line-watch", json=too_many).status_code == 422
        got = client.put("/api/notify/line-watch", json={"enabled": True, "tickers": ["nq=f", "SOXL"],
                                                         "timeframes": ["1h", "5m"], "events": {"retest": False}})
        assert got.status_code == 200, got.text
        got = got.json()
        assert (got["enabled"], got["tickers"], got["timeframes"]) == (True, ["NQ=F", "SOXL"], ["60m", "5m"])
        assert got["events"] == {"touch": True, "break": True, "retest": False, "long": True}
        engine = make_engine(db_url)
        Auth(engine, td(hours=1), td(hours=1)).create_user("other@example.com", PASSWORD)
        engine.dispose()
        client.post("/api/auth/logout")
        client.post("/api/auth/login", json={"username": "other@example.com", "password": PASSWORD})
        assert client.get("/api/notify/line-watch").json()["can_edit"] is False
        assert client.put("/api/notify/line-watch", json={"enabled": False}).status_code == 403


def _mirror(bars):
    """The same bars upside down (price -> C^2 / price): a support becomes a resistance."""
    c2 = 27500.0 ** 2
    return [{**b, "open": c2 / b["open"], "high": c2 / b["low"], "low": c2 / b["high"], "close": c2 / b["close"]}
            for b in bars]


def test_the_rules_run_both_ways():
    up = _signals(_mirror(FIVE), "5m", len(FIVE) - 6)
    assert any(s["rule"] == "break_up" and s["role"] == "trend_break" and s["line"] == "하락 추세선" for _, s in up)
    ups = _signals(_mirror(HOURLY), "60m", 200)
    rules = {(s["rule"], s["role"]) for _, s in ups}
    assert ("touch_resistance", "channel_upper") in rules and ("touch_support", "channel_lower") in rules
    assert all(s["line"].startswith("상승 채널") for _, s in ups if s["role"].startswith("channel"))


def test_a_folded_bar_is_finished_only_once_the_next_has_begun():
    bars = HOURLY[:260]
    got = line_signals.detect(bars, "240m", LATER)
    assert all(s["at"] == bars[-2]["at"] for s in got)          # the last fold may still be growing
    assert line_signals._finished_end(bars, len(bars) - 1, "240m") is None
    assert line_signals._finished_end(bars, len(bars) - 2, "240m") == datetime.fromisoformat(bars[-1]["at"])


def test_a_copy_fetched_before_the_bar_ended_is_not_judged(watch, monkeypatch):
    """The loop may get a cached copy the chart filled seconds before the bar's end: that last bar was not
    finished in it - read again on the next pass, judged then."""
    sent = []
    monkeypatch.setattr(line_signals.notify, "send", lambda pusher, *kinds, payload: sent.append(payload) or 0)
    bars = _break_bars()
    end = datetime.fromisoformat(bars[-1]["at"]) + timedelta(minutes=5)
    watch.clock = lambda: end + timedelta(seconds=30)
    cfg = watch.save({"enabled": True, "tickers": ["NQ=F"], "timeframes": ["5m"]})
    early = (end - timedelta(seconds=10)).isoformat()
    assert not [s for s in watch.scan("NQ=F", "5m", bars, cfg=cfg, fetched_at=early) if s["rule"] == "break_down"]
    assert not watch.due("NQ=F", "5m") and watch.due("NQ=F", "5m", end + timedelta(seconds=30 + line_signals.TICK_SEC))
    fresh = (end + timedelta(seconds=25)).isoformat()
    assert [s["rule"] for s in watch.scan("NQ=F", "5m", bars, cfg=cfg, fetched_at=fresh)] == ["break_down"]


def test_the_cooldown_reaches_across_midnight():
    late = _row(tf="5m", at="2026-11-17T14:55:00+00:00")             # 23:55 KST, the day before
    sig = _sig(line="L2", at="2026-11-17T15:05:00+00:00")             # 00:05 KST
    assert line_signals.kst_day(sig["at"]) == "2026-11-18" and line_signals.kst_day(late["at"]) == "2026-11-17"
    assert not line_signals.admit(sig, "NQ=F", "5m", [], [late])
    assert line_signals.admit(sig, "NQ=F", "5m", [], [_row(tf="5m", at="2026-11-17T14:00:00+00:00")])


def test_a_partial_setting_keeps_the_rest(watch):
    watch.save({"enabled": False, "tickers": ["NQ=F", "SOXL"], "timeframes": ["5m"], "events": {"touch": False}})
    got = watch.save({"enabled": True, "events": {"retest": False}})
    assert (got["enabled"], got["tickers"], got["timeframes"]) == (True, ["NQ=F", "SOXL"], ["5m"])
    assert got["events"] == {"touch": False, "break": True, "retest": False, "long": True}
    with pytest.raises(ValueError):
        watch.save({"enabled": True, "tickers": []})


def test_a_pass_reads_at_most_pass_max_and_survives_a_failure(watch, monkeypatch):
    monkeypatch.setattr(line_signals.notify, "send", lambda pusher, *kinds, payload: 0)
    bars = _break_bars()
    asked = []

    class Prices:
        async def candles(self, ticker, tf, count):
            asked.append((ticker, tf))
            if ticker == "T0":
                raise RuntimeError("source down")
            return {"bars": bars, "fetched_at": watch.clock().isoformat()}

    now = datetime.fromisoformat(bars[-1]["at"]) + timedelta(minutes=5, seconds=30)
    watch.clock = lambda: now
    watch.save({"enabled": True, "tickers": [f"T{i}" for i in range(6)], "timeframes": list(channels.TIMEFRAMES)})
    asyncio.run(line_signals.run_pass(watch, Prices(), None))
    assert len(asked) == line_signals.PASS_MAX and ("T1", "5m") in asked     # T0 failed, the rest went on
    assert not watch.due("T0", "5m")                                         # the failed one waits IDLE_SEC
    asyncio.run(line_signals.run_pass(watch, Prices(), None))
    assert len(asked) == 6 * len(channels.TIMEFRAMES)                       # the rest on the next pass
