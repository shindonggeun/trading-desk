"""#694 a ticker's regime now: trend, range, turning (desk.regime_live)."""

import asyncio
import math
from datetime import UTC, datetime, timedelta

from desk import regime_live

START = datetime(2026, 1, 5, tzinfo=UTC)


def _bars(closes: list[float], spread: float = 0.006) -> list[dict]:
    out = []
    for i, c in enumerate(closes):
        prev = closes[i - 1] if i else c
        out.append({"at": (START + timedelta(days=i)).isoformat(timespec="seconds"), "open": prev,
                    "high": max(prev, c) * (1 + spread), "low": min(prev, c) * (1 - spread), "close": c})
    return out


def _trend(n: int = 150, step: float = 0.008) -> list[dict]:
    return _bars([100 * (1 + step) ** i * (1 + 0.004 * math.sin(i)) for i in range(n)])


def _range(n: int = 150) -> list[dict]:
    """Twelve-bar swings between about 95 and 105: each edge met again and again."""
    return _bars([100 + 5 * math.sin(2 * math.pi * i / 12) for i in range(n)])


def test_a_steady_rise_is_an_uptrend_and_its_mirror_a_downtrend():
    up = regime_live.classify(_trend(), "1d")
    assert up["regime"] == "trend_up" and up["label"] == "상승 추세" and up["adx"] >= regime_live.TREND_ADX
    assert up["plus_di"] > up["minus_di"] and up["basis"][0].startswith("ADX ")
    down = regime_live.classify(_trend(step=-0.008), "1d")
    assert down["regime"] == "trend_down" and "종가 < EMA20" in down["basis"][1]


def test_a_box_met_at_both_edges_is_a_range_with_its_numbers():
    got = regime_live.classify(_range(), "1d")
    assert got["regime"] == "range" and got["adx"] < regime_live.RANGE_ADX
    box = got["box"]
    assert 94 < box["low"] < 96 and 104 < box["high"] < 106
    assert box["touches_high"] >= 2 and box["touches_low"] >= 2 and box["round_trips"] >= 3
    assert any("박스" in b and "위 끝" in b for b in got["basis"])


def test_a_close_out_of_the_box_is_a_turn_and_few_bars_say_nothing():
    bars = _range()
    last = bars[-1]["close"]
    bars.append({**bars[-1], "at": (START + timedelta(days=len(bars))).isoformat(timespec="seconds"),
                 "open": last, "high": 112.0, "low": last, "close": 111.5})
    got = regime_live.classify(bars, "1d")
    assert got["regime"] == "turning" and "위로" in got["basis"][1]
    few = regime_live.classify(_range(40), "60m")
    assert few["regime"] == "unknown" and few["label"] == "판단 보류" and "40개" in few["basis"][0]
    assert regime_live.classify([], "1d")["regime"] == "unknown"


class _Prices:
    def __init__(self, by_tf, fail=()):
        self.by_tf, self.fail = by_tf, fail

    async def candles(self, ticker, tf, count):
        if tf in self.fail:
            raise ConnectionError("down")
        assert count == regime_live.COUNTS[tf]
        return {"bars": self.by_tf[tf]}


def test_the_view_takes_the_daily_regime_and_says_both_frames():
    got = asyncio.run(regime_live.view(_Prices({"1d": _trend(), "60m": _range()}), "AAA"))
    assert (got["ticker"], got["regime"], got["tf"]) == ("AAA", "trend_up", "1d")
    assert set(got["frames"]) == {"1d", "60m"} and got["summary"] == "1시간봉 박스권 · 일봉 상승 추세"
    hourly = asyncio.run(regime_live.view(_Prices({"60m": _range()}, fail=("1d",)), "AAA"))
    assert (hourly["regime"], hourly["tf"], list(hourly["frames"])) == ("range", "60m", ["60m"])
    none = asyncio.run(regime_live.view(_Prices({}, fail=("1d", "60m")), "AAA"))
    assert none["regime"] == "unknown" and none["frames"] == {}


def test_api_regime_by_ticker(db_url, monkeypatch):
    from tests.test_api import _client

    async def view(prices, ticker, tfs=regime_live.FRAMES):
        return {"ticker": ticker, "regime": "range", "label": "박스권", "tf": "1d", "frames": {}, "summary": ""}

    monkeypatch.setattr(regime_live, "view", view)
    with _client(db_url) as client:
        got = client.get("/api/regime", params={"ticker": "aapl"})
        assert got.status_code == 200 and got.json()["ticker"] == "AAPL" and got.json()["regime"] == "range"
        assert client.get("/api/regime", params={"ticker": "bad ticker!"}).status_code == 422


def test_a_big_down_day_in_a_downtrend_stays_a_downtrend():
    bars = _trend(step=-0.008)
    last = bars[-1]["close"]
    bars.append({"at": (START + timedelta(days=len(bars))).isoformat(timespec="seconds"), "open": last,
                 "high": last, "low": last * 0.94, "close": last * 0.95})
    assert regime_live.classify(bars, "1d")["regime"] == "trend_down"


def test_a_readable_daily_frame_decides_even_when_no_rule_fits(monkeypatch):
    real = regime_live.classify

    def classify(bars, tf):
        got = real(bars, tf)
        return {**got, "regime": "unknown", "label": "판단 보류"} if tf == "1d" and got["readable"] else got

    monkeypatch.setattr(regime_live, "classify", classify)
    got = asyncio.run(regime_live.view(_Prices({"1d": _range(), "60m": _trend()}), "AAA"))
    assert (got["tf"], got["regime"]) == ("1d", "unknown")                        # not the hourly's trend
    thin = asyncio.run(regime_live.view(_Prices({"1d": _trend(30), "60m": _trend()}), "AAA"))
    assert thin["tf"] == "60m" and thin["regime"] == "trend_up"                    # too few daily bars
    assert not real(_trend(30), "1d")["readable"] and real(_trend(), "1d")["readable"]
