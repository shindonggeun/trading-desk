"""Fixed Yahoo-shaped daily observations. No network or broker calls."""

import asyncio
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from desk import market_watchboard as board

FRIDAY = date(2026, 11, 20)
MONDAY = date(2026, 11, 23)
TUESDAY = date(2026, 11, 24)
NOW = datetime(2026, 11, 24, 22, 30, tzinfo=UTC)
BENCHMARKS = ["^KS11", "NQ=F", "^SOX", "^N225", "^TWII"]


def _data(ticker, zone, prices, *, checked=NOW):
    rows = [{"at": datetime.combine(day, time(), ZoneInfo(zone)).astimezone(UTC).isoformat(), "close": close}
            for day, close in prices.items()]
    return {"ticker": ticker, "interval": "1d", "source": "yahoo", "fetched_at": checked.isoformat(), "bars": rows}


def _read(group, sources, now=NOW, days=7):
    async def fetch(ticker):
        value = sources.get(ticker)
        if isinstance(value, Exception):
            raise value
        return value

    return asyncio.run(board.read(group, days, fetch, clock=lambda: now))


def _item(result, ticker):
    return next(value for value in result["items"] if value["ticker"] == ticker)


def test_family_groups_keep_local_security_adr_and_price_units_separate():
    result = _read("semiconductors", {})
    items = result["items"]
    assert [item["ticker"] for item in items if item["relation"] == "benchmark"] == BENCHMARKS
    assert {item["ticker"] for item in items if item["relation"] == "related"} == {
        "005930.KS", "000660.KS", "NVDA", "285A.T", "2330.TW",
    }
    local, adr = _item(result, "2330.TW"), _item(result, "TSM")
    assert (local["market"], local["currency"], local["kind"], local["timezone"]) == (
        "tw", "TWD", "stock", "Asia/Taipei",
    )
    assert (adr["market"], adr["currency"], adr["kind"], adr["relation"]) == ("us", "USD", "adr", "reference")
    assert _item(result, "285A.T")["currency"] == "JPY"
    assert _item(result, "^KS11")["unit"] == "points"
    assert _item(result, "NQ=F")["chart_href"] == "/#/charts?t=NQ%3DF&iv=1d"
    assert [item["ticker"] for item in _read("crypto", {})["items"]] == ["BTC-USD", "ETH-USD"]
    assert [item["ticker"] for item in _read("gold_miners", {})["items"]] == ["GDX", "NEM", "AEM"]
    assert _item(_read("gold_miners", {}), "GDX")["kind"] == "etf"


def test_common_start_uses_real_local_dates_and_keeps_holiday_gap():
    prices = {
        "^KS11": _data("^KS11", "Asia/Seoul", {FRIDAY: 100, MONDAY: 110, TUESDAY: 121}),
        "NQ=F": _data("NQ=F", "America/New_York", {FRIDAY: 400, MONDAY: 420, TUESDAY: 440}),
        "^SOX": _data("^SOX", "America/New_York", {FRIDAY: 200, MONDAY: 210, TUESDAY: 190}),
        "^N225": _data("^N225", "Asia/Tokyo", {FRIDAY: 300, MONDAY: 999, TUESDAY: 330}),
        "^TWII": ConnectionError("provider credentials or response detail must not be returned"),
    }
    result = _read("semiconductors", prices)
    japan = _item(result, "^N225")
    assert japan["latest"] == {"trade_date": "2026-11-24", "at": "2026-11-23T15:00:00+00:00",
                               "closed_at": "2026-11-24T06:30:00+00:00", "close": 330}
    assert japan["change_pct"] == 10
    assert japan["source_delay_min"] == 20
    assert _item(result, "NQ=F")["latest"]["closed_at"] == "2026-11-24T22:00:00+00:00"
    assert _item(result, "^TWII")["reason"] == "source_error"
    assert "credentials" not in str(result)

    compare = result["comparison"]
    assert compare["status"] == "partial" and compare["excluded"] == ["^TWII"]
    assert compare["base_date"] == "2026-11-20"
    values = {entry["ticker"]: entry["points"] for entry in compare["series"]}
    assert set(values) == set(BENCHMARKS) - {"^TWII"}
    assert all(points[0] == {"trade_date": "2026-11-20", "return_pct": 0} for points in values.values())
    assert values["^N225"] == [{"trade_date": "2026-11-20", "return_pct": 0},
                               {"trade_date": "2026-11-24", "return_pct": 10}]
    assert values["^KS11"][-1]["return_pct"] == 21
    assert values["^SOX"][-1]["return_pct"] == -5


def test_period_is_unavailable_without_a_common_start_in_the_window():
    sources = {"BTC-USD": _data("BTC-USD", "UTC", {FRIDAY: 100, MONDAY: 105}),
               "ETH-USD": _data("ETH-USD", "UTC", {FRIDAY: 50, TUESDAY: 60})}
    comparison = _read("crypto", sources, datetime(2026, 11, 25, tzinfo=UTC), days=3)["comparison"]
    assert comparison == {"status": "unavailable", "base_date": None, "series": [],
                          "excluded": ["BTC-USD", "ETH-USD"]}


@pytest.mark.parametrize(("ticker", "now", "state"), [
    ("^N225", datetime(2026, 11, 24, 3, tzinfo=UTC), "break"),
    ("^N225", datetime(2026, 11, 23, 3, tzinfo=UTC), "holiday"),
    ("285A.T", datetime(2026, 11, 24, 5, tzinfo=UTC), "open"),
    ("^TWII", datetime(2026, 11, 24, 4, tzinfo=UTC), "open"),
    ("^TWII", datetime(2026, 11, 24, 5, 31, tzinfo=UTC), "closed"),
    ("^KS11", datetime(2026, 11, 24, 0, 5, tzinfo=UTC), "open"),
    ("^SOX", datetime(2026, 11, 27, 18, 1, tzinfo=UTC), "closed"),
    ("NQ=F", datetime(2026, 11, 22, 22, 59, tzinfo=UTC), "closed"),
    ("NQ=F", datetime(2026, 11, 22, 23, tzinfo=UTC), "open"),
    ("NQ=F", datetime(2026, 11, 24, 22, 30, tzinfo=UTC), "break"),
    ("NQ=F", datetime(2026, 11, 26, 19, tzinfo=UTC), "unknown"),
])
def test_market_state_follows_venue_hours_and_closures(ticker, now, state):
    assert _item(_read("semiconductors", {}, now), ticker)["market_state"] == state


def test_taipei_auction_grace_and_seoul_delayed_fetch_do_not_promote_todays_bar():
    before_taipei = datetime(2026, 11, 24, 5, 52, tzinfo=UTC)
    at_taipei = datetime(2026, 11, 24, 5, 53, tzinfo=UTC)
    for now, latest in ((before_taipei, MONDAY), (at_taipei, TUESDAY)):
        source = _data("2330.TW", "Asia/Taipei", {MONDAY: 100, TUESDAY: 120}, checked=now)
        item = _item(_read("semiconductors", {"2330.TW": source}, now), "2330.TW")
        assert item["status"] == "available" and item["latest"]["trade_date"] == latest.isoformat()
        assert item["latest"]["closed_at"] == f"{latest.isoformat()}T05:30:00+00:00"

    now = datetime(2026, 11, 24, 7, tzinfo=UTC)
    cached = datetime(2026, 11, 24, 6, 49, tzinfo=UTC)
    source = _data("^KS11", "Asia/Seoul", {FRIDAY: 100, MONDAY: 120, TUESDAY: 999}, checked=cached)
    item = _item(_read("semiconductors", {"^KS11": source}, now), "^KS11")
    assert (item["status"], item["reason"]) == ("delayed", "latest_session_missing")
    assert item["fetched_at"] == cached.isoformat() and item["latest"]["close"] == 120
    assert item["change_pct"] == 20


def test_cme_evening_rollover_accepts_only_separable_trading_days():
    now = datetime(2026, 11, 24, 23, 20, tzinfo=UTC)
    tentative = {FRIDAY: 100, MONDAY: 110, TUESDAY: 999}
    source = _data("NQ=F", "America/New_York", tentative, checked=now)
    item = _item(_read("semiconductors", {"NQ=F": source}, now), "NQ=F")
    assert item["market_state"] == "open"
    assert (item["status"], item["latest"]["trade_date"]) == ("delayed", "2026-11-23")
    separable = {**tentative, TUESDAY: 120, date(2026, 11, 25): 999}
    source = _data("NQ=F", "America/New_York", separable, checked=now)
    item = _item(_read("semiconductors", {"NQ=F": source}, now), "NQ=F")
    assert (item["status"], item["latest"]["close"]) == ("available", 120)
    assert [point["trade_date"] for point in item["history"]] == ["2026-11-20", "2026-11-23", "2026-11-24"]


def test_cme_special_session_is_not_relabelled_as_a_known_close():
    now = datetime(2026, 11, 26, 23, tzinfo=UTC)
    source = _data("NQ=F", "America/New_York", {date(2026, 11, 25): 100, date(2026, 11, 26): 999}, checked=now)
    item = _item(_read("semiconductors", {"NQ=F": source}, now), "NQ=F")
    assert (item["market_state"], item["status"], item["reason"]) == ("unknown", "delayed", "session_unverified")
    assert item["latest"]["trade_date"] == "2026-11-25"


def test_invalid_source_and_bars_remain_visible_as_missing_or_delayed():
    wrong_product = _data("TSM", "America/New_York", {TUESDAY: 123})
    bad_source = {**_data("^SOX", "America/New_York", {TUESDAY: 123}), "source": "estimate"}
    future = {**_data("^KS11", "Asia/Seoul", {TUESDAY: 123}), "fetched_at": "2026-11-24T23:59:00+00:00"}
    japan = _data("^N225", "Asia/Tokyo", {FRIDAY: 111, TUESDAY: 222})
    japan["bars"].append(dict(japan["bars"][-1]))
    sources = {"2330.TW": wrong_product, "^SOX": bad_source, "^KS11": future, "^N225": japan}
    result = _read("semiconductors", sources)
    for ticker, reason in (("2330.TW", "wrong_instrument"), ("^SOX", "source_unverified"),
                           ("^KS11", "timestamp_unverified")):
        item = _item(result, ticker)
        assert item["status"] == "missing" and item["reason"] == reason
        assert item["latest"] is None and item["change_pct"] is None
    assert _item(result, "^N225")["status"] == "delayed"
    assert _item(result, "^N225")["latest"]["trade_date"] == "2026-11-20"


def test_different_market_closes_do_not_admit_a_forming_utc_crypto_day():
    sources = {"BTC-USD": _data("BTC-USD", "UTC", {FRIDAY: 110, MONDAY: 111, TUESDAY: 999}),
               "ETH-USD": _data("ETH-USD", "UTC", {FRIDAY: 75, MONDAY: 80, TUESDAY: 999})}
    result = _read("crypto", sources)
    btc = _item(result, "BTC-USD")
    assert btc["market_state"] == "open" and btc["status"] == "available"
    assert btc["latest"]["trade_date"] == "2026-11-23"
    assert btc["latest"]["closed_at"] == "2026-11-24T00:00:00+00:00"
    assert btc["change_pct"] is None  # the omitted Sunday is not a crypto holiday
