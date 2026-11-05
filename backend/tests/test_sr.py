import asyncio
from datetime import date, timedelta

import pytest

from desk import sr
from desk.broker import Bar, MockBroker
from desk.prices import PriceSource
from desk.profile import desk_portfolio
from tests.test_api import OWNER, PASSWORD
from tests.test_prices import _client


def _bars(closes: list[float], start: date, step_days: int, spread: float = 1.0) -> list[dict]:
    return [{"at": f"{start + timedelta(days=i * step_days)}T00:00:00+00:00", "open": c, "high": c + spread,
             "low": c - spread, "close": c, "volume": 1000.0} for i, c in enumerate(closes)]


def _zigzag(n: int, low: float, high: float, period: int) -> list[float]:
    """Prices bouncing between ``low`` and ``high`` — a range with clear floor and ceiling."""
    half = period // 2
    out = []
    for i in range(n):
        phase = i % period
        frac = phase / half if phase <= half else (period - phase) / half
        out.append(low + (high - low) * frac)
    return out


DAILY = _bars(_zigzag(200, 90.0, 110.0, 20), date(2026, 1, 1), 1)
WEEKLY = _bars(_zigzag(156, 80.0, 120.0, 10), date(2023, 11, 1), 7, spread=3.0)


def test_range_floor_and_ceiling_become_support_and_resistance():
    result = sr.compute(DAILY, WEEKLY, price=100.0)
    assert result["reference_price"] == 100.0 and result["reference"] == "live" and result["atr"] > 0
    kinds = {lv["kind"] for lv in result["levels"]}
    assert kinds == {"support", "resistance"}
    supports = [lv for lv in result["levels"] if lv["kind"] == "support"]
    resistances = [lv for lv in result["levels"] if lv["kind"] == "resistance"]
    assert all(lv["price"] < 100 for lv in supports) and all(lv["price"] > 100 for lv in resistances)
    # the daily range edges (89 low / 111 high incl. the bar spread) are the strongest zones
    assert any(abs(lv["price"] - 89) <= 1.5 and "일봉 스윙 저점" in lv["basis"] for lv in supports)
    assert any(abs(lv["price"] - 111) <= 1.5 and "일봉 스윙 고점" in lv["basis"] for lv in resistances)
    assert len(supports) <= sr.PER_SIDE and len(resistances) <= sr.PER_SIDE
    prices = [lv["price"] for lv in result["levels"]]
    assert prices == sorted(prices, reverse=True)


def test_without_a_live_price_the_last_close_is_the_reference():
    result = sr.compute(DAILY, WEEKLY)
    assert result["reference"] == "close" and result["reference_price"] == DAILY[-1]["close"]


def test_too_few_bars_is_an_error():
    with pytest.raises(ValueError):
        sr.compute(DAILY[:10], [])


def test_prompt_note_lists_levels_and_is_empty_without_them():
    note = sr.prompt_note(sr.compute(DAILY, WEEKLY, price=100.0))
    assert note.startswith("Desk-computed support") and "\n- S " in note and "\n- R " in note
    assert sr.prompt_note(None) is None and sr.prompt_note({"levels": []}) is None


def test_note_reaches_the_portfolio_prompt_even_without_profile_or_book():
    portfolio = desk_portfolio(None, None, "LEVELS")
    assert portfolio is not None and portfolio.render("NVDA").endswith("LEVELS")
    with_profile = desk_portfolio("balanced", None, "LEVELS")
    assert "balanced swing investor" in with_profile.mandate and with_profile.mandate.endswith("LEVELS")
    assert desk_portfolio(None, None) is None


class _Bars(MockBroker):
    name = "toss"

    def __init__(self):
        self.calls = []

    async def candles(self, symbol, interval, count):
        self.calls.append((interval, count))
        return [Bar(**bar) for bar in DAILY[-count:]]


def _source(broker=None):
    weekly_calls = []

    def yahoo_candles(ticker, interval, count):
        weekly_calls.append((interval, count))
        return [Bar(**bar) for bar in WEEKLY[-count:]]
    return PriceSource(broker or _Bars(), yahoo_candles=yahoo_candles), weekly_calls


def test_weekly_and_long_histories_skip_the_broker():
    broker = _Bars()
    source, yahoo_calls = _source(broker)
    asyncio.run(source.candles("NVDA", "1w", 156))
    asyncio.run(source.candles("NVDA", "1d", 400))
    asyncio.run(source.candles("NVDA", "1d", 200))
    assert yahoo_calls == [("1w", 156), ("1d", 400)] and broker.calls == [("1d", 200)]


def test_support_resistance_uses_both_timeframes_and_cuts_at_the_trade_date():
    sr._CACHE.clear()
    source, _ = _source()
    result = asyncio.run(sr.support_resistance(source, "NVDA", trade_date="2026-05-01"))
    assert result["sources"] == {"1d": "toss", "1w": "yahoo"} and result["as_of"][:10] <= "2026-05-01"
    assert result["ticker"] == "NVDA" and result["levels"]


def test_candles_and_levels_endpoints(db_url):
    sr._CACHE.clear()
    source, _ = _source()
    with _client(db_url, source, fake_runs=True) as client:
        assert client.get("/api/candles?ticker=NVDA").status_code == 401
        assert client.get("/api/levels?ticker=NVDA").status_code == 401
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        weekly = client.get("/api/candles?ticker=nvda&interval=1w&count=52").json()
        assert weekly["interval"] == "1w" and weekly["source"] == "yahoo" and len(weekly["bars"]) == 52
        daily = client.get("/api/candles?ticker=NVDA&interval=1d&count=30").json()
        assert daily["source"] == "toss" and len(daily["bars"]) == 30
        assert client.get("/api/candles?ticker=NVDA&interval=7m").status_code == 422
        levels = client.get("/api/levels?ticker=NVDA").json()
        assert levels["levels"] and {"price", "kind", "timeframe", "strength", "basis"} <= set(levels["levels"][0])
        assert isinstance(levels["trendlines"], list) and levels["fibonacci"]["levels"]   # desk.trend (#73)


def test_korean_bars_are_cut_on_the_korean_date_and_past_dates_use_finished_weeks():
    # Toss/Yahoo date a KRX day at local midnight = 15:00 UTC the day before
    daily = [{"at": f"2026-11-0{d}T15:00:00+00:00", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
             for d in (3, 4, 5)]               # KST 11-04, 11-05, 11-06
    weekly = [{"at": f"{d}T15:00:00+00:00", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
              for d in ("2026-10-25", "2026-11-01")]   # weeks starting KST Mon 10-26 and 11-02
    days, weeks = sr._until(daily, weekly, "005930.KS", "2026-11-05")
    assert [b["at"][:10] for b in days] == ["2026-11-03", "2026-11-04"]
    assert [b["at"][:10] for b in weeks] == ["2026-10-25"]      # the week of 11-02 has not ended
    us_days, _ = sr._until([{**daily[0], "at": "2026-11-05T05:00:00+00:00"}], [], "NVDA", "2026-11-05")
    assert len(us_days) == 1


def test_a_live_price_result_does_not_poison_the_cache():
    sr._CACHE.clear()
    source, _ = _source()
    live = asyncio.run(sr.support_resistance(source, "NVDA", price=100.0))
    plain = asyncio.run(sr.support_resistance(source, "NVDA"))
    assert live["reference"] == "live" and plain["reference"] == "close"


def test_run_finishes_without_levels_when_the_lookup_fails_or_hangs(monkeypatch):
    from desk import runner

    class Request:
        ticker, trade_date = "NVDA", "2026-11-04"

    async def hang(*_args, **_kw):
        await asyncio.sleep(60)

    manager = runner.RunManager.__new__(runner.RunManager)
    manager.prices = object()
    monkeypatch.setattr(runner, "SR_TIMEOUT_SEC", 0.05)
    monkeypatch.setattr(runner, "support_resistance", hang)
    assert asyncio.run(manager._support_resistance(Request())) is None

    async def boom(*_args, **_kw):
        raise ConnectionError("down")
    monkeypatch.setattr(runner, "support_resistance", boom)
    assert asyncio.run(manager._support_resistance(Request())) is None


def test_live_note_puts_the_current_price_in_front_of_the_agents():
    note = sr.live_note({"price": 200.13, "source": "toss", "fetched_at": "2026-11-05T15:00:00+00:00"}, 6.0)
    assert "200.13" in note and "ATR14 6.0" in note and "conditional" in note
    assert sr.live_note(None) is None


def test_a_hanging_quote_does_not_hold_the_run(monkeypatch):
    from datetime import date

    from desk import runner

    class Request:
        ticker, trade_date = "NVDA", date.today().isoformat()

    class Hanging:
        async def quote(self, _ticker):
            await asyncio.sleep(60)

    manager = runner.RunManager.__new__(runner.RunManager)
    manager.prices = Hanging()
    monkeypatch.setattr(runner, "QUOTE_TIMEOUT_SEC", 0.05)
    assert asyncio.run(manager._live_quote(Request())) is None
