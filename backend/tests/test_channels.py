"""#611 parallel channels, short trendlines and their walk-forward backtest, on fixed synthetic bars."""

import math
import random
from datetime import UTC, datetime, timedelta

import pytest

from desk import channels, lines_bt


def _tri(x, period):
    p = (x % period) / period
    return 4 * p - 1 if p < 0.5 else 3 - 4 * p


def _bars(n, minutes, drift, amp, period, start=27500.0, drop=None):
    """A zigzag around a straight (log) line; ``drop`` (bars, log step) bends the last bars down."""
    t0 = datetime(2026, 11, 10, tzinfo=UTC)
    out, prev, last = [], math.log(start), None
    for x in range(n):
        y = math.log(start) + drift * x + amp * _tri(x, period)
        if drop and x >= n - drop[0]:
            y = last + drop[1] * (x - (n - drop[0]) + 1)
        else:
            last = y
        out.append({"at": (t0 + timedelta(minutes=minutes * x)).isoformat(), "open": math.exp(prev),
                    "high": math.exp(max(prev, y) + amp * 0.08), "low": math.exp(min(prev, y) - amp * 0.08),
                    "close": math.exp(y), "volume": 100.0})
        prev = y
    return out


HOURLY = _bars(300, 60, -0.0008, 0.012, 24)                       # the user's 1-hour falling channel
FIVE = _bars(200, 5, 0.0004, 0.002, 12, drop=(5, -0.0025))       # a 5-minute rising line, then a break


def _log_slope(line):
    s, e = line["start"], line["end"]
    return math.log(e["price"] / s["price"])


def test_a_falling_channel_is_found_with_parallel_lines_and_its_outer_ones():
    out = channels.lines(HOURLY, "60m")
    assert out["channels"], out
    ch = out["channels"][0]
    assert ch["direction"] == "down" and ch["timeframe"] == "60m"
    slopes = [_log_slope(ch[k]) for k in ("upper", "lower", "mid", "outer_upper", "outer_lower")]
    assert max(slopes) - min(slopes) < 0.002                          # parallel (prices rounded)
    up, low = ch["upper"]["end"]["price"], ch["lower"]["end"]["price"]
    assert low < ch["mid"]["end"]["price"] < up
    width = math.log(up / low)
    assert math.log(ch["outer_upper"]["end"]["price"] / up) == pytest.approx(width, abs=0.002)
    assert math.log(low / ch["outer_lower"]["end"]["price"]) == pytest.approx(width, abs=0.002)
    assert len(ch["touches"]["upper"]) >= 2 and len(ch["touches"]["lower"]) >= 2 and ch["body_breaks"] == 0
    assert 0 <= ch["position"] <= 1 and ch["state"] in ("inside", "near_upper", "near_lower")
    assert "1시간봉 하락 채널" in ch["explain"] and out["note"].endswith("추천도 아니에요.")


def test_a_short_rising_trendline_breaks_and_says_so():
    out = channels.lines(FIVE, "5m")
    broken = [t for t in out["trendlines"] if t["kind"] == "support" and t["state"] == "broken"]
    assert broken, out["trendlines"]
    t = broken[0]
    assert t["broken_at"] > t["anchors"][1]["at"] and t["broken_price"] > 0
    assert t["explain"].startswith("5분봉 상승추세 이탈")
    assert any(e["kind"] == "trend_break" and e["line_id"] == t["id"] for e in out["events"])
    # before the drop the same line holds
    before = channels.lines(FIVE[:-5], "5m")
    assert all(t["state"] != "broken" for t in before["trendlines"] if t["kind"] == "support")


def test_a_trend_break_names_the_next_channel_line():
    chans = [{"direction": "down", **{k: {"end": {"price": p}} for k, p in (
        ("upper", 27160.0), ("lower", 26800.0), ("mid", 26980.0), ("outer_upper", 27520.0), ("outer_lower", 26440.0))}}]
    assert channels._next_line(27200.0, chans, below=True) == ("하락 채널 상단", 27160.0)
    assert channels._next_line(27200.0, chans, below=False) == ("하락 채널 바깥 상단", 27520.0)


def test_too_few_bars_give_no_lines():
    out = channels.lines(HOURLY[:10], "60m")
    assert (out["channels"], out["trendlines"], out["events"]) == ([], [], [])
    assert out["as_of"] == HOURLY[9]["at"] and out["atr"] is None


def test_the_backtest_reads_only_the_bars_up_to_each_step(monkeypatch):
    seen = []
    real = channels.find_channels

    def spy(past, k, unit):
        seen.append(past[-1]["at"])
        return real(past, k, unit)

    touched = []
    real_touch = lines_bt._touch

    def spy_touch(future, n, f, sign, unit, horizon):
        touched.append(future[0]["at"])
        return real_touch(future, n, f, sign, unit, horizon)

    monkeypatch.setattr(channels, "find_channels", spy)
    monkeypatch.setattr(lines_bt, "_touch", spy_touch)
    bars = _bars(700, 60, -0.0003, 0.01, 24)
    lines_bt.backtest(bars, "60m")
    assert seen and touched
    # every watched stretch (the lines' and the baselines') starts on the bar right after a window whose
    # lines were found on that window alone
    index = {b["at"]: i for i, b in enumerate(bars)}
    ends = {index[last] for last in seen}
    assert all(index[first] - 1 in ends for first in touched)


def _walk(n, seed):
    random.seed(seed)
    y, bars, t0 = math.log(20000), [], datetime(2026, 1, 1, tzinfo=UTC)
    for x in range(n):
        o = y
        y += random.gauss(0, 0.002)
        bars.append({"at": (t0 + timedelta(minutes=5 * x)).isoformat(), "open": math.exp(o),
                     "high": math.exp(max(o, y) + 0.0005), "low": math.exp(min(o, y) - 0.0005),
                     "close": math.exp(y), "volume": 1.0})
    return bars


def test_on_a_random_walk_each_line_reads_like_its_baseline():
    """The baseline is fair: on noise a line and a line of the same slope and distance set elsewhere
    "hold" alike (a flat baseline made the rising support read ~6 points worse, a symmetric run 0.5 always)."""
    got = [lines_bt.backtest(_walk(4000, seed), "5m") for seed in (1, 2)]
    assert all(g["steps"] > 200 and set(g["roles"]) == {*lines_bt.ROLES, "trend_break_follow"} for g in got)
    for role in lines_bt.ROLES:
        line = [sum(g["roles"][role][k] for g in got) for k in ("n", "bounce")]
        base = [sum(g["roles"][role]["baseline"][k] for g in got) for k in ("n", "bounce")]
        assert line[0] >= 40 and base[0] >= 40, role
        p = (line[1] + base[1]) / (line[0] + base[0])
        bound = 3.5 * math.sqrt(p * (1 - p) * (1 / line[0] + 1 / base[0]))  # no edge in noise, within its sampling
        assert abs(line[1] / line[0] - base[1] / base[0]) < bound, role
        r = got[0]["roles"][role]
        assert r["bounce"] + r["break"] + r["none"] == r["n"]
    follow = [g["roles"]["trend_break_follow"] for g in got]
    n, base_n = sum(f["n"] for f in follow), sum(f["baseline_n"] for f in follow)
    assert n >= 10 and base_n >= 10                                    # measured, not a constant
    assert abs(sum(f["follow"] for f in follow) / n - sum(f["baseline_follow"] for f in follow) / base_n) < 0.3


def test_a_line_desk_trend_already_draws_is_left_to_it(monkeypatch):
    k, a = channels.SWING_K["5m"], channels.atr(FIVE)
    unit = a / FIVE[-1]["close"]
    mine = next(t for t in channels.find_trendlines(FIVE, k, unit) if t.kind == "support")
    same = {"kind": "support", "anchors": [{"at": FIVE[mine.i]["at"]}, {"at": FIVE[mine.j]["at"]}]}
    monkeypatch.setattr(channels.trend, "trendlines", lambda *a, **kw: [same])
    _chans, picked = channels.pick(FIVE, "5m", unit, a)
    assert all((t.i, t.j) != (mine.i, mine.j) for t in picked)


def test_a_bar_still_trading_is_not_a_break():
    bars = FIVE[:-4]                                    # the first bar of the drop is the last one
    done = channels.lines(bars, "5m")                   # taken as finished: broken
    assert any(t["state"] == "broken" for t in done["trendlines"] if t["kind"] == "support")
    live = channels.lines(bars, "5m", live=True)        # still trading: only testing, no event
    support = [t for t in live["trendlines"] if t["kind"] == "support"]
    assert support and all(t["state"] == "testing" for t in support)
    assert not [e for e in live["events"] if e["kind"] == "trend_break"]
    assert live["as_of"] == bars[-1]["at"] and support[0]["end"]["at"] == bars[-1]["at"]


def test_rates_and_summary_read_the_table():
    role = {"n": 10, "bounce": 4, "break": 6, "none": 0, "bounce_rate": 0.4, "break_rate": 0.6,
            "baseline": {"n": 9, "bounce": 5, "break": 4, "none": 0, "bounce_rate": 0.556, "break_rate": 0.444}}
    table = {"generated_at": "2026-11-18T05:00:00+00:00", "tickers": {"NQ=F": {"5m": {
        "from": "a", "to": "b", "steps": 3, "horizon_bars": 12,
        "roles": {**{r: role for r in lines_bt.ROLES},
                  "trend_break_follow": {"n": 5, "follow": 2, "follow_rate": 0.4, "baseline_rate": 0.5}}}}}}
    got = lines_bt.rates("NQ=F", "5m", table)
    assert got["channel"]["lower"] == {"n": 10, "bounce_rate": 0.4, "break_rate": 0.6,
                                       "baseline_bounce_rate": 0.556, "horizon_bars": 12}
    assert got["trend_break"]["follow_rate"] == 0.4
    assert lines_bt.rates("SOXL", "5m", table) == {} and lines_bt.rates("NQ=F", "1d", table) == {}
    assert lines_bt.summary("NQ=F", table)["timeframes"]["5m"]["steps"] == 3
    assert lines_bt.summary("SOXL", table) is None


def test_the_shipped_table_has_nq_rates():
    for tf in ("5m", "60m", "1d"):
        got = lines_bt.rates("NQ=F", tf)
        assert got["channel"]["lower"]["n"] > 0 and got["trend_break"]["baseline_rate"] is not None


def test_api_lines(db_url):
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
        asked.append((ticker, interval, count))
        src = HOURLY if interval == "60m" else FIVE
        return [Bar(b["at"], b["open"], b["high"], b["low"], b["close"], b["volume"]) for b in src][-count:]

    settings = Settings(database_url=db_url, fake_delay=0, fake_runs=True)
    client = TestClient(create_app(settings, runner=make_fake_runner(0),
                                   prices=PriceSource(MockBroker(), yahoo_candles=yahoo)),
                        base_url="https://testserver")
    engine = make_engine(db_url)
    Auth(engine, td(hours=1), td(hours=1)).create_user(OWNER, PASSWORD)
    engine.dispose()
    with client:
        assert client.get("/api/lines?ticker=NQ=F&tf=1h").status_code == 401
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        assert client.get("/api/lines?ticker=NQ=F&tf=2h").status_code == 422
        body = client.get("/api/lines?ticker=NQ=F&tf=1h").json()
        assert body["interval"] == "60m" and body["channels"][0]["direction"] == "down"
        assert body["channels"][0]["hit_rate"]["lower"]["n"] > 0           # the shipped NQ=F table
        assert body["backtest"]["timeframes"]["60m"]["steps"] > 0
        five = client.get("/api/lines?tf=5m").json()
        assert five["ticker"] == "NQ=F" and any(e["kind"] == "trend_break" for e in five["events"])
        assert ("NQ=F", "60m", channels.BARS["60m"]) in asked
