"""#674 NQ=F alarms judged every minute on a live price (desk.nq_fast)."""

import asyncio
import random
from datetime import UTC, datetime, timedelta

import httpx

from desk import market_alerts, nq_fast, nq_watch
from desk.estimate import BinanceEstimator
from desk.jobs import Jobs
from tests.test_estimate import WIDE_INFO

START = datetime(2026, 11, 10, 23, 0, tzinfo=UTC)          # 18:00 ET Tuesday: a CME day opens
N = 1300                                                    # minutes: past MIN_SAMPLE, before the daily break


def _minutes(n: int = N, start: datetime = START, wobble: float = 0.0002) -> list[dict]:
    """``n`` contiguous 1-minute bars wobbling ±``wobble``."""
    out, close = [], 500.0
    for i in range(n):
        close *= 1 + (wobble if i % 2 else -wobble)
        out.append({"at": (start + i * nq_fast.STEP).isoformat(timespec="seconds"), "open": close,
                    "high": close, "low": close, "close": close})
    return out


def _noisy(n: int = N, seed: int = 7, sigma: float = 0.0005) -> list[dict]:
    """A seeded random walk of 1-minute bars: 5- and 15-minute returns spread like a market's."""
    rng, out, close = random.Random(seed), [], 500.0
    for i in range(n):
        close *= 1 + rng.gauss(0, sigma)
        out.append({"at": (START + i * nq_fast.STEP).isoformat(timespec="seconds"), "open": close,
                    "high": close, "low": close, "close": close})
    return out


def _end(bars: list[dict]) -> datetime:
    return datetime.fromisoformat(bars[-1]["at"]) + nq_fast.STEP


def test_a_fast_drop_on_the_live_price_fires_within_the_minute_and_says_its_source():
    bars = _minutes()
    now = _end(bars) + timedelta(seconds=20)
    price = bars[-1]["close"] * 0.996
    got = nq_fast.judge(bars, price, now - timedelta(seconds=3), now, "binance", nq_watch.cme_open)
    assert [(a["rule"], a["severity"], a["source"], a["delay_sec"], a["window_min"]) for a in got] == [
        ("nq_drop", "strong", "binance", 3, 5)]
    assert got[0]["t"] == _end(bars).astimezone(nq_watch.KST).isoformat(timespec="seconds")
    assert nq_watch.text(got[0]).endswith("· 판정 추정(바이낸스 QQQ 무기한) · 지연 3초")
    assert "(하위 0.5% " in nq_watch.text(got[0])
    assert nq_fast.judge(bars, bars[-1]["close"] * 1.004, now, now, "binance", nq_watch.cme_open) == []   # up


def test_a_stale_price_a_thin_history_or_a_closed_hour_says_nothing():
    bars = _minutes()
    now = _end(bars) + timedelta(seconds=20)
    price = bars[-1]["close"] * 0.996
    stale = now - timedelta(seconds=nq_fast.FRESH_SEC + 1)
    assert nq_fast.judge(bars, price, stale, now, "binance", nq_watch.cme_open) == []
    assert nq_fast.judge(bars[-200:], price, now, now, "binance", nq_watch.cme_open) == []
    assert nq_fast.judge(bars, price, now, now, "qqq", nq_fast.qqq_hours) == []   # too few of QQQ's minutes


def test_a_slow_slide_is_the_fifteen_minute_rule_and_the_session_high_is_watched():
    bars = _noisy()
    base = bars[-16]["close"]
    for k in range(15):                        # fifteen minutes sliding 0.045% each: no 5-minute extreme
        c = base * (1 - 0.00045 * (k + 1))
        bars[-15 + k] = {**bars[-15 + k], "open": c, "high": c, "low": c, "close": c}
    now = _end(bars) + timedelta(seconds=5)
    got = nq_fast.judge(bars, bars[-1]["close"], now, now, "binance", nq_watch.cme_open)
    assert [a["rule"] for a in got] == ["nq_drop15"]
    high = nq_fast.judge(bars, bars[-1]["close"] * 0.98, now, now, "binance", nq_watch.cme_open)
    assert [a["rule"] for a in high] == ["nq_drop", "nq_from_high"]
    assert nq_watch.text(high[1]).startswith("🔵 나스닥 선물 장중 고점 대비 −")
    assert [a["rule"] for a in nq_fast.judge(bars, bars[-1]["close"] * 0.98, now, now, "binance",
                                             nq_watch.cme_open, from_high=False)] == ["nq_drop"]


def test_qqq_hours_are_the_regular_session_past_the_open():
    et = (lambda d, h, m: datetime(2026, 11, d, h, m, tzinfo=nq_fast.US.zone))
    assert not nq_fast.qqq_hours(et(17, 9, 31)) and nq_fast.qqq_hours(et(17, 9, 35))
    assert nq_fast.qqq_hours(et(17, 15, 59)) and not nq_fast.qqq_hours(et(17, 16, 0))
    assert not nq_fast.qqq_hours(et(21, 11, 0))                                         # Saturday


class _Prices:
    def __init__(self, bars):
        self.bars, self.asked = bars, []

    async def candles(self, ticker, interval, count):
        self.asked.append((ticker, interval, count))
        return {"bars": self.bars}


class _Estimator:
    def __init__(self, bars):
        self.bars, self.since = bars, []

    async def minute_marks(self, ticker, since):
        self.since.append(since)
        return [b for b in self.bars if datetime.fromisoformat(b["at"]) >= since]


def _qqq_minutes(now: datetime) -> list[dict]:
    """QQQ's regular-hours minutes (09:30-16:00 ET = 14:30-21:00 UTC in winter) on three days, then today to now."""
    out = []
    for day in (12, 13, 16):
        out += _minutes(390, start=datetime(2026, 11, day, 14, 30, tzinfo=UTC))
    return out + _minutes(int((now - datetime(2026, 11, 17, 14, 30, tzinfo=UTC)).total_seconds() // 60),
                          start=datetime(2026, 11, 17, 14, 30, tzinfo=UTC))


def _watch(monkeypatch, now, live=None, regular=True, marks=None):
    seen = []

    def judge(bars, price, price_at, now_, source, keep, limits, from_high):
        seen.append((source, price, price_at, from_high, len(bars)))
        return []

    monkeypatch.setattr(nq_fast, "judge", judge)

    async def qqq_price():
        if isinstance(live, Exception):
            raise live
        return live

    async def qqq_regular():
        return regular

    w = nq_fast.NqFast(_Prices(_qqq_minutes(now)), _Estimator(marks or _minutes()), qqq_price, qqq_regular,
                       now=lambda: now)
    return w, seen


def test_a_pass_takes_qqq_live_in_its_hours_else_the_binance_contract(monkeypatch):
    now = datetime(2026, 11, 17, 15, 0, 10, tzinfo=UTC)                 # 10:00 ET Tuesday
    fresh = {"price": 600.0, "at": (now - timedelta(seconds=4)).isoformat(), "live": True, "estimated": False}
    marks = _minutes(301, start=datetime(2026, 11, 17, 10, 0, tzinfo=UTC))               # the last one forming
    w, seen = _watch(monkeypatch, now, fresh, marks=marks)
    assert asyncio.run(w.run_pass()) == ("qqq", [])
    assert seen[0][:4] == ("qqq", 600.0, now - timedelta(seconds=4), False)
    assert [x[0] for x in seen] == ["qqq", "binance"] and seen[1][3] is True      # the high: the contract
    assert w.prices.asked == [("QQQ", "1m", nq_fast.QQQ_BARS)] and w.last["delay_sec"] == 4

    for live, regular in ((fresh, False), ({**fresh, "estimated": True}, True), (RuntimeError("down"), True),
                          ({**fresh, "at": (now - timedelta(minutes=5)).isoformat()}, True)):
        w, seen = _watch(monkeypatch, now, live, regular, marks)
        assert asyncio.run(w.run_pass())[0] == "binance"
        assert seen[-1][0] == "binance" and seen[-1][3] is True


def test_the_binance_minutes_are_read_once_then_only_the_new_ones(monkeypatch):
    base = datetime(2026, 11, 17, 3, 0, tzinfo=UTC)                     # 22:00 ET Monday: CME trades
    now = base + timedelta(seconds=10)
    marks = _minutes(start=base - timedelta(days=9), n=12961)                          # the last one forming
    w, seen = _watch(monkeypatch, now, marks=marks)
    asyncio.run(w.run_pass())
    first = w.estimator.since[0]
    assert first == now - nq_fast.HISTORY
    assert all(datetime.fromisoformat(b["at"]) >= now - nq_fast.HISTORY for b in w._minutes)
    asyncio.run(w.run_pass())
    assert w.estimator.since[1] == datetime.fromisoformat(w._minutes[-1]["at"])         # from the last one on
    assert seen[-1][1] == marks[-1]["close"] and seen[-1][2] == now                  # the forming minute: now


def test_a_qqq_drop_goes_out_only_when_the_binance_contract_moved_with_it():
    marks = _minutes()
    at = _end(marks)
    alert = nq_watch._alert("nq_drop", at, -0.004, -0.002, "caution", window_min=5)
    ref = marks[-6]["close"]                                          # the bar ending five minutes before
    assert nq_fast.confirmed(alert, marks, (ref * 0.998, at), at)    # half of QQQ's -0.4%
    assert not nq_fast.confirmed(alert, marks, (ref * 0.999, at), at)    # a quarter: a bad QQQ print
    assert nq_fast.confirmed(alert, marks, None, at)                 # no contract price: QQQ stands
    high = nq_watch._alert("nq_from_high", at, -0.02, -0.015, "caution")
    assert nq_fast.confirmed(high, marks, (ref, at), at)


def test_cme_closed_is_no_pass(monkeypatch):
    w, seen = _watch(monkeypatch, datetime(2026, 11, 21, 17, 0, tzinfo=UTC))           # Saturday
    assert asyncio.run(w.run_pass()) == (None, []) and seen == []


def test_the_late_yahoo_alarm_of_a_move_already_sent_is_held_back():
    bars = _minutes()
    now = _end(bars) + timedelta(seconds=20)
    fast = nq_fast.judge(bars, bars[-1]["close"] * 0.996, now, now, "binance", nq_watch.cme_open)
    late = nq_watch._alert("nq_drop15", _end(bars) + timedelta(minutes=5), -0.006, -0.004, "caution")
    assert not market_alerts.admit(late, fast)                       # the market's 20-minute cooldown
    same = nq_watch._alert("nq_drop", _end(bars) + timedelta(minutes=5), -0.006, -0.004, "strong")
    assert not market_alerts.admit(same, fast)                       # the same rule: 60 minutes


def test_the_kst_midnight_does_not_split_one_move_into_two_alarms():
    fast = nq_watch._alert("nq_drop", datetime(2026, 11, 17, 14, 58, tzinfo=UTC), -0.006, -0.004, "caution")
    late = nq_watch._alert("nq_drop15", datetime(2026, 11, 17, 15, 0, tzinfo=UTC), -0.008, -0.005, "caution")
    assert fast["t"].startswith("2026-11-17T23:58") and late["t"].startswith("2026-11-18T00:00")
    assert not market_alerts.admit(late, [], [fast])               # yesterday's (KST) alarm holds it back
    flow = {**late, "kind": "flow_shift", "market": "kospi"}
    assert market_alerts.admit(flow, [], [fast])


def test_a_qqq_too_thin_to_judge_leaves_the_drops_to_the_contract_and_is_asked_again(monkeypatch):
    now = datetime(2026, 11, 17, 15, 0, 10, tzinfo=UTC)                 # 10:00 ET Tuesday
    fresh = {"price": 600.0, "at": (now - timedelta(seconds=4)).isoformat(), "live": True, "estimated": False}
    w = nq_fast.NqFast(_Prices(_minutes(50)), _Estimator(_minutes(301, start=datetime(2026, 11, 17, 10, 0, tzinfo=UTC))),
                       lambda: _async(fresh), lambda: _async(True), now=lambda: now)
    assert asyncio.run(w.run_pass())[0] == "binance"                  # fifty QQQ minutes: no thresholds
    assert w._limits.get(("qqq", "2026-11-17T15")) is None            # not kept for the hour
    assert "qqq" in w._down


def test_a_binance_price_that_stopped_coming_neither_judges_nor_confirms():
    now = datetime(2026, 11, 17, 3, 0, 10, tzinfo=UTC)
    old = _minutes(301, start=now - timedelta(minutes=320))            # the last minute ended 19 min ago
    w = nq_fast.NqFast(_Prices([]), _Estimator(old), None, lambda: _async(False), now=lambda: now)
    assert asyncio.run(w.run_pass()) == (None, [])


async def _async(value):
    return value


def test_the_yahoo_pass_says_it_is_late():
    from tests.test_nq_watch import _after, _bars

    bars = _bars(last_ret=-0.004, last_volume=400)
    at = _after(bars) + timedelta(seconds=620)            # Yahoo's bar comes ten minutes after it ends

    class _Alerts(market_alerts.MarketAlerts):
        def dispatch(self, alerts):
            return alerts

    got = _Alerts(None).nq_pass(bars, at)
    assert got[0]["source"] == "yahoo" and got[0]["delay_sec"] == 620
    assert nq_watch.text(got[0]).endswith("· 판정 야후 5분봉 · 지연 약 10분")


def test_status_carries_a_jobs_note():
    jobs = Jobs()
    jobs.add("nq_fast", "나스닥 선물 1분 판정", "QQQ 실시간 · 바이낸스 추정")
    assert jobs.status()[0]["note"] is None
    jobs.get("nq_fast").note = {"source": "qqq", "delay_sec": 4}
    assert jobs.status()[0]["note"] == {"source": "qqq", "delay_sec": 4}


def test_the_estimator_reads_the_contracts_minutes_page_by_page():
    calls = []

    def binance(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fapi/v1/exchangeInfo":
            return httpx.Response(200, json=WIDE_INFO)
        params = request.url.params
        assert request.url.path == "/fapi/v1/markPriceKlines" and params["interval"] == "1m"
        calls.append((params["symbol"], int(params["startTime"])))
        start = int(params["startTime"])
        size = nq_fast_limit() if len(calls) == 1 else 3
        return httpx.Response(200, json=[[start + i * 60_000, "1", "2", "0.5", "1.5", "0"] for i in range(size)])

    def nq_fast_limit():
        from desk.estimate import MINUTE_LIMIT
        return MINUTE_LIMIT

    est = BinanceEstimator(http=httpx.AsyncClient(base_url="https://fapi.test", transport=httpx.MockTransport(binance)),
                           now=lambda: START)
    got = asyncio.run(est.minute_marks("NQ=F", START))
    assert [c[0] for c in calls] == ["QQQUSDT", "QQQUSDT"] and len(got) == nq_fast_limit() + 3
    assert got[0] == {"at": START.isoformat(), "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}
    assert asyncio.run(est.minute_marks("ZZZZ", START)) == []
