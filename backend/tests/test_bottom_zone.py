"""#240: the long-term bottom zone ("인생 타점") on synthetic weekly bars."""

import itertools
from datetime import UTC, datetime, timedelta
from datetime import timedelta as td
from types import SimpleNamespace

from fastapi.testclient import TestClient

from desk import bottom_zone as bz


def _bars(closes: list[float], start=datetime(2015, 1, 5, tzinfo=UTC), wick: float = 0.01) -> list[dict]:
    out = []
    for n, c in enumerate(closes):
        at = (start + timedelta(weeks=n)).isoformat(timespec="seconds")
        out.append({"at": at, "open": c, "high": c * (1 + wick), "low": c * (1 - wick), "close": c})
    return out


def _crash(up: int = 150, down: int = 30, peak: float = 200.0, fall: float = 0.45) -> list[float]:
    """A long rise to ``peak``, then a steady fall of ``fall`` over ``down`` weeks."""
    rise = [100 + (peak - 100) * n / (up - 1) for n in range(up)]
    return rise + [peak * (1 - fall * (n + 1) / down) for n in range(down)]


def test_too_little_history_is_none():
    assert bz.assess(_bars([100.0] * (bz.MIN_WEEKS - 1))) is None


def test_a_small_fall_is_no_zone():
    z = bz.assess(_bars(_crash(fall=0.10)))
    assert z["stage"] == "none" and z["ladder"] == [] and bz.note(z, "X") is None


def test_a_deep_fall_reaches_the_bottom_stage_with_its_reasons_and_ladder():
    z = bz.assess(_bars(_crash()))
    assert z["drawdown_pct"] == 45.0 and z["stage"] == "bottom" and z["score"] >= 70
    met = {e["id"] for e in z["evidence"] if e["met"]}
    assert {"drawdown", "weekly_rsi", "long_ma"} <= met
    cap = next(e for e in z["evidence"] if e["id"] == "capitulation")
    assert cap["met"] is None                       # unknown, and left out of the score
    ladder = z["ladder"]
    assert [s["weight_pct"] for s in ladder] == list(bz.LADDER_WEIGHTS) and ladder[0]["price"] == z["close"]
    prices = [s["price"] for s in ladder]
    assert prices == sorted(prices, reverse=True)
    assert all(b < a * (1 - bz.MIN_STEP_GAP / 100) + 1e-9 for a, b in itertools.pairwise(prices))
    assert prices[-1] >= z["close"] * (1 - bz.MAX_DEPTH / 100) - 0.01
    assert z["invalidation"]["monthly_close_below"] < prices[-1]
    assert z["targets"][-1] == {"id": "high", "price": 200.0, "text": "직전 고점"}
    assert z["leveraged_note"] is None
    line = bz.note(z, "005930.KS")
    assert "stage bottom" in line and "void on a monthly close" in line


def test_no_look_ahead():
    bars = _bars(_crash() + [200.0] * 20)          # a full recovery after the low
    at_low = bz.assess(bars, 179)
    assert at_low == bz.assess(bars[:180])
    assert at_low["as_of"] == bars[179]["at"]


def test_capitulation_counts_only_when_given():
    bars = _bars(_crash(fall=0.25))
    without = bz.assess(bars)
    peaked = bz.assess(bars, capitulation={"score": 40, "peak": 80})
    calm = bz.assess(bars, capitulation={"score": 20, "peak": 30})
    assert peaked["score"] > without["score"] > calm["score"]


def test_a_leveraged_product_is_told_to_prefer_the_underlying():
    assert "감쇠" in bz.assess(_bars(_crash()), leveraged=True)["leveraged_note"]


def test_monthly_groups_weeks_by_month():
    m = bz.monthly(_bars([10, 12, 8, 11, 9, 13], start=datetime(2024, 1, 1, tzinfo=UTC)))
    assert [x["at"][:7] for x in m] == ["2024-01", "2024-02"]
    assert m[0]["close"] == 9 and round(m[0]["low"], 2) == round(8 * 0.99, 2)


def test_backtest_follows_the_plan_and_finds_the_low():
    closes = _crash() + [110 + 2 * n for n in range(110)]          # bottom near 110, then a rise
    bars = _bars(closes)
    bt = bz.backtest(bars, "approach")
    assert bt["count"] >= 1 and bt["signals"][0]["filled_steps"] >= 1
    first = bt["signals"][0]
    assert first["returns"]["52w"] > 0 and first["false"] is False
    assert bt["avg_52w"] is not None and bt["false_rate"] == 0.0
    low_month = min(bars[150:], key=lambda b: b["low"])["at"][:7]     # the crash low, not the start
    lead = bz.lead(bt["signals"], (low_month,), bars)[0]
    assert lead["signal_at"] is not None and lead["weeks_early"] >= 0


def test_a_fall_that_keeps_going_is_a_false_signal():
    closes = _crash() + [110 * 0.98 ** n for n in range(1, 60)]   # no bottom: down another ~70%
    bt = bz.backtest(_bars(closes), "approach")
    assert bt["count"] >= 1 and bt["signals"][0]["void"] and bt["signals"][0]["false"]


def test_api_answers_the_zone_and_the_note_joins_the_weekly_one(db_url):
    from desk import api
    from desk.auth import Auth
    from desk.db import make_engine
    from desk.settings import Settings
    from tests.test_api import OWNER, PASSWORD

    def candles(symbol, interval, count):
        closes = [100.0] * 10 if symbol == "NEW" else _crash()
        return [SimpleNamespace(**b) for b in _bars(closes)]

    zones = bz.Zones(candles=candles, leveraged=lambda t: t == "SOXL")
    client = TestClient(api.create_app(Settings(database_url=db_url, fake_runs=True, fake_delay=0), bottoms=zones),
                        base_url="https://testserver")
    engine = make_engine(db_url)
    Auth(engine, td(hours=1), td(hours=1)).create_user(OWNER, PASSWORD)
    engine.dispose()
    with client:
        assert client.get("/api/bottom-zone", params={"ticker": "NVDA"}).status_code == 401
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        body = client.get("/api/bottom-zone", params={"ticker": "nvda"}).json()
        assert body["ticker"] == "NVDA" and body["supported"] and body["stage"] == "bottom"
        assert {"score", "stage", "evidence", "ladder", "invalidation", "as_of"} <= body.keys()
        assert client.get("/api/bottom-zone", params={"ticker": "SOXL"}).json()["leveraged_note"]
        assert client.get("/api/bottom-zone", params={"ticker": "NEW"}).json()["supported"] is False
        assert client.get("/api/bottom-zone", params={"ticker": "A B"}).status_code == 422
    assert "Long-term bottom zone (NVDA" in zones.note_for("NVDA")
    assert zones.note_for("NEW") is None


def test_the_signal_week_fills_only_its_close_not_its_own_low():
    """#240 review: the signal is known at the week's close, so that week's low fills no deeper step."""
    closes = _crash()
    bars = _bars(closes, wick=0.12)                 # every week dips 12% under its close
    z = bz.assess(bars, len(bars) - 1)
    assert z["ladder"][1]["price"] > bars[-1]["low"]          # the low would have reached step 2
    follow = bz._follow(bars + _bars([closes[-1] * 1.5] * 3, start=datetime(2030, 1, 7, tzinfo=UTC), wick=0),
                        len(bars) - 1, z, (1,))
    assert follow["filled_steps"] == 1 and follow["max_drawdown"] >= 0


def test_a_long_stay_in_the_zone_is_one_signal():
    closes = _crash() + [110.0 * (1 + 0.001 * (n % 3)) for n in range(80)]   # 80 weeks flat at the low
    bt = bz.backtest(_bars(closes), "approach")
    assert bt["count"] == 1


def test_the_note_warns_about_leveraged_products():
    z = bz.assess(_bars(_crash()), leveraged=True)
    assert "Leveraged/inverse product" in bz.note(z, "SOXL")


def test_a_slow_bottom_zone_does_not_hold_back_the_weekly_note(db_url, monkeypatch):
    import asyncio
    import time

    from desk import api
    from desk import weekly_map as wm
    from desk.settings import Settings

    monkeypatch.setattr(api, "BOTTOM_NOTE_SEC", 0.2)
    weekly = wm.Maps(candles=lambda s, i, c: [])
    monkeypatch.setattr(weekly, "note_for", lambda t: "weekly map line")
    zones = bz.Zones(candles=lambda s, i, c: [])
    monkeypatch.setattr(zones, "note_for", lambda t: time.sleep(1.0) or "late bottom line")
    captured = {}
    real = api.RunManager.__init__

    def keep(self, *a, **k):
        captured["weekly"] = k.get("weekly")
        real(self, *a, **k)

    monkeypatch.setattr(api.RunManager, "__init__", keep)
    app = api.create_app(Settings(database_url=db_url, fake_runs=False, fake_delay=0), runner=lambda *a, **k: None,
                         weekly=weekly, bottoms=zones)
    with TestClient(app):
        pass
    async def timed():
        started = time.monotonic()
        line = await captured["weekly"]("SOXL")
        return line, time.monotonic() - started

    line, took = asyncio.run(timed())               # (asyncio.run then waits for the sleeping thread)
    assert line == "weekly map line" and took < 0.9


# --- #310: the ladder cut at the price now ----------------------------------------------------------

def test_a_price_under_the_weekly_close_starts_a_new_ladder_there():
    z = bz.assess(_bars(_crash()))
    close = z["close"]
    now = round(close * 0.94, 2)                         # 6% under the weekly close (KORU 8.01 vs 8.54)
    live = bz.live_ladder(z, now, "toss", "2026-11-12T08:42:00+00:00")
    assert (live["price_now"], live["price_source"], live["price_at"]) == (now, "toss", "2026-11-12T08:42:00+00:00")
    passed = [s for s in live["ladder"] if s["status"] == "passed"]
    fresh = [s for s in live["ladder"] if s["status"] != "passed"]
    assert passed and all(s["price"] > now for s in passed)
    assert fresh[0]["status"] == "now" and fresh[0]["price"] <= now and fresh[0]["from_now_pct"] == 0.0
    assert [s["weight_pct"] for s in fresh] == list(bz.LADDER_WEIGHTS)
    assert all(s["status"] == "below" and s["from_now_pct"] < 0 for s in fresh[1:])
    assert live["invalidation"]["monthly_close_below"] < fresh[-1]["price"]
    # the reading stays the weekly one
    assert (live["score"], live["stage"], live["close"]) == (z["score"], z["stage"], close)
    assert "from the price now" in bz.note(live, "KORU") and str(fresh[0]["price"]) in bz.note(live, "KORU")


def test_a_price_well_above_the_first_step_waits_for_it():
    z = bz.assess(_bars(_crash()))
    first = z["ladder"][0]["price"]
    near = bz.live_ladder(z, first * 1.02, "kis")
    assert near["ladder"][0]["status"] == "now" and near["first_step_gap_pct"] == 0.0
    far = bz.live_ladder(z, first * 1.10, "kis")
    assert far["ladder"][0]["status"] == "below" and far["first_step_gap_pct"] == round((1 / 1.10 - 1) * 100, 1)
    assert far["ladder"][0]["from_now_pct"] == far["first_step_gap_pct"]
    assert "not a buy yet" in bz.note(far, "NVDA")


def test_without_a_price_the_weekly_close_stands_in():
    z = bz.assess(_bars(_crash()))
    live = bz.live_ladder(z, None)
    assert live["price_source"] == "weekly_close" and live["price_now"] == bz._round(z["close"])
    assert live["ladder"][0]["status"] == "now" and [s["price"] for s in live["ladder"]] == [s["price"] for s in z["ladder"]]
    assert "from the last weekly close" in bz.note(live, "NVDA")


def test_the_api_cuts_the_ladder_at_the_quote(db_url, monkeypatch):
    from desk import api
    from desk.auth import Auth
    from desk.db import make_engine
    from desk.prices import Quote
    from desk.settings import Settings
    from tests.test_api import OWNER, PASSWORD

    def candles(symbol, interval, count):
        return [SimpleNamespace(**b) for b in _bars(_crash())]

    close = bz.assess(_bars(_crash()))["close"]

    async def quote(self, t):
        if t == "DOWN":
            raise ConnectionError("no quote")
        return Quote(t, round(close * 0.9, 2), "USD", "toss", "2026-11-12T08:42:00+00:00")

    monkeypatch.setattr("desk.prices.PriceSource.quote", quote)
    zones = bz.Zones(candles=candles, leveraged=lambda t: False)
    client = TestClient(api.create_app(Settings(database_url=db_url, fake_runs=True, fake_delay=0), bottoms=zones),
                        base_url="https://testserver")
    engine = make_engine(db_url)
    Auth(engine, td(hours=1), td(hours=1)).create_user(OWNER, PASSWORD)
    engine.dispose()
    with client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        body = client.get("/api/bottom-zone", params={"ticker": "KORU"}).json()
        assert body["price_source"] == "toss" and body["price_now"] == round(close * 0.9, 2)
        assert any(s["status"] == "passed" for s in body["ladder"])
        down = client.get("/api/bottom-zone", params={"ticker": "DOWN"}).json()
        assert down["price_source"] in ("daily_close", "weekly_close")       # the quote failed


def test_the_prewarm_reads_the_dashboards_zones_before_they_expire():
    """#643 the dashboard's first ask after the hour does not wait on Yahoo's weekly bars."""
    asked, now = [], [0.0]

    def candles(symbol, interval, count):
        asked.append(symbol)
        if symbol == "BAD":
            raise RuntimeError("Yahoo down")
        return []

    zones = bz.Zones(candles=candles, ttl_sec=3600, clock=lambda: now[0])
    assert zones.warm(["^KS11", "^NDX", "^KS11", "BAD"]) == 2               # once each, one failing alone
    assert asked == ["^KS11", "^NDX", "BAD"]
    now[0] = 3000
    zones.warm(["^KS11"], ahead=300)                                       # fresh for 600 s more: kept
    assert asked.count("^KS11") == 1
    zones.warm(["^KS11"], ahead=900)                                       # expiring within 900 s: read again
    assert asked.count("^KS11") == 2
    zones.bars("^NDX")                                                     # a page's own ask uses the cache
    assert asked.count("^NDX") == 1
