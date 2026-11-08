"""#203: Stochastic and Bollinger Bands on the candles, past RSI / Stochastic extremes and what
followed them, the zone now, the underlying of a leveraged product, and the analysis note."""

import asyncio
import math
from datetime import date, timedelta

import pytest

from desk import oscillator
from desk.broker import Bar
from desk.indicators import annotate, bollinger, normalize, stochastic
from desk.prices import PriceSource
from tests.test_api import OWNER, PASSWORD
from tests.test_prices import _client


def test_stochastic_matches_a_hand_computed_window():
    highs = [10, 11, 12, 13, 12, 11, 12, 14]
    lows = [8, 9, 10, 11, 10, 9, 10, 12]
    closes = [9, 10, 11, 12, 11, 10, 11, 13]
    k, d = stochastic(highs, lows, closes, k=3, smooth=1, d=2)
    # bar 2: hh 12, ll 8 -> (11 - 8) / 4 = 75; bar 3: hh 13, ll 9 -> 75; bar 4: hh 13, ll 10 -> 33.33
    assert k[:2] == [None, None] and k[2:5] == [75.0, 75.0, 33.33]
    assert d[2] is None and d[3] == 75.0 and d[4] == pytest.approx(54.17, abs=0.01)
    # a flat window has no range: 50
    assert stochastic([5, 5, 5], [5, 5, 5], [5, 5, 5], k=3)[0][2] == 50.0


def test_bollinger_uses_the_population_deviation_like_tradingview():
    closes = [float(i) for i in range(1, 21)]
    band = bollinger(closes, 20)
    sd = math.sqrt(sum((c - 10.5) ** 2 for c in closes) / 20)      # 5.766, not the sample 5.916
    assert band[18] is None and band[19] == {"mid": 10.5, "upper": round(10.5 + 2 * sd, 4),
                                             "lower": round(10.5 - 2 * sd, 4)}


def test_indicator_names_take_aliases_and_refuse_unknown_ones():
    assert normalize(["rsi", "stoch", "bb"]) == ("bb20", "bb50", "rsi14", "stoch")
    assert normalize(["rsi14", " ", "bb20"]) == ("bb20", "rsi14")
    with pytest.raises(ValueError):
        normalize(["macd"])


def _bars(closes, start=date(2023, 1, 2), spread=0.01):
    return [{"at": (start + timedelta(days=i)).isoformat() + "T00:00:00+00:00", "open": c,
             "high": c * (1 + spread), "low": c * (1 - spread), "close": c, "volume": 1}
            for i, c in enumerate(closes)]


def _cycle(n=400):
    """Rallies that overshoot then fall back, and drops that rebound - the extremes have a record."""
    out, price = [], 100.0
    for i in range(n):
        phase = i % 40
        price *= 1.02 if phase < 12 else 0.985 if phase < 28 else 1.004
        out.append(round(price, 4))
    return out


def test_episodes_count_each_run_once_and_look_twenty_bars_ahead():
    bars = _bars(_cycle())
    rows = oscillator.episodes(bars)
    hot = [r for r in rows if r["kind"] == "rsi_hot"]
    assert hot and len({r["at"] for r in hot}) == len(hot)
    done = [r for r in hot if "ret20" in r]
    assert done and all({"ret5", "ret10", "ret20", "max_drop", "max_rise"} <= set(r) for r in done)
    # the RSI's overshoots are followed by the fall: weaker 20 bars than any bar's; the stochastic
    # fires earlier in each rally and is not - each kind is judged on its own
    rep = oscillator.report(bars, "TEST")
    assert rep["by_kind"]["rsi_hot"]["n"] >= 5
    assert rep["effect"]["rsi_hot"] is True and rep["effect"]["stoch_hot"] is False


def test_the_zone_now_and_its_sentence():
    rising = _bars(_cycle(372) + [round(_cycle(372)[-1] * 1.02 ** i, 4) for i in range(1, 9)])
    rep = oscillator.report(rising, "SOXX")
    assert rep["zone"] == "hot" and rep["by"] == "rsi" and rep["rsi"] >= 70
    assert rep["reason"].startswith("SOXX 일봉 RSI") and "과열(위험) 구간" in rep["reason"]
    assert "번 중" in rep["reason"] and "평균 최대 낙폭" in rep["reason"]
    # #206: every kind of past extreme for the chart's markers, oldest first, the latest EPISODES_MAX
    eps = rep["episodes"]
    assert eps and len(eps) <= oscillator.EPISODES_MAX and [e["at"] for e in eps] == sorted(e["at"] for e in eps)
    assert {e["kind"] for e in eps} >= {"rsi_hot"} and all("value" in e for e in eps)
    flat =oscillator.report(_bars([100 + (i % 2) * 0.1 for i in range(200)]), "FLAT")
    assert flat["zone"] == "neutral" and "과열 · 침체 구간 아님" in flat["reason"]
    assert oscillator.report(_bars([100.0] * 10), "X")["zone"] is None


def test_a_leveraged_product_also_reads_its_underlying():
    oscillator._CACHE.clear()
    asked = []

    def fetch(ticker, interval):
        asked.append(ticker)
        return _bars(_cycle())

    view = oscillator.view(fetch, "SOXL")
    assert view["own"]["ticker"] == "SOXL" and view["underlying"]["ticker"] == "SOXX"
    assert view["underlying"]["leverage"] == 3 and asked == ["SOXL", "SOXX"]
    assert oscillator.view(fetch, "NVDA")["underlying"] is None
    with pytest.raises(ValueError):
        oscillator.view(fetch, "NVDA", "5m")
    note = asyncio.run(oscillator.note_for(fetch, "SOXX"))
    assert note.startswith("Oscillator (desk, past episodes): SOXX 일봉")


def _yahoo(closes):
    def candles(ticker, interval, count):
        return [Bar(b["at"], b["open"], b["high"], b["low"], b["close"], 1) for b in _bars(closes)][-count:]
    return candles


def test_candles_api_returns_stochastic_and_bands(db_url):
    source = PriceSource(None, yahoo_candles=_yahoo(_cycle(300)))
    rows = [dict(r) for r in _bars(_cycle(60))]
    annotate(rows, ("bb20", "stoch"))
    assert rows[-1]["stoch_k"] is not None and set(rows[-1]["bb20"]) == {"mid", "upper", "lower"}
    with _client(db_url, source, fake_runs=True) as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        body = client.get("/api/candles?ticker=NVDA&interval=1M&count=30&indicators=rsi,stoch,bb").json()
        assert body["indicators"] == ["bb20", "bb50", "rsi14", "stoch"]
        last = body["bars"][-1]
        assert {"rsi", "stoch_k", "stoch_d", "bb20", "bb50"} <= set(last)
        assert client.get("/api/candles?ticker=NVDA&indicators=macd").status_code == 422


def test_oscillator_api_needs_a_session_and_reads_the_underlying(db_url):
    oscillator._CACHE.clear()
    source = PriceSource(None, yahoo_candles=_yahoo(_cycle()))
    with _client(db_url, source, fake_runs=True) as client:
        assert client.get("/api/oscillator?ticker=SOXL").status_code == 401
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        body = client.get("/api/oscillator?ticker=SOXL").json()
        assert body["own"]["zone"] in ("hot", "cold", "neutral") and body["underlying"]["ticker"] == "SOXX"
        assert body["underlying"]["reason"].startswith("SOXX 일봉")
        assert client.get("/api/oscillator?ticker=SOXL&interval=5m").status_code == 422
