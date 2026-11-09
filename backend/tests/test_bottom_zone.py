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
