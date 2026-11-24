from __future__ import annotations

import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from desk import binance_funding, shared_cache
from desk.binance_funding import FundingHistory

_DEFAULT_NOW = datetime(2026, 11, 24, 13, tzinfo=UTC)
_HOUR = timedelta(hours=1)
_DAY = timedelta(days=1)


class Exchange:
    def __init__(self, now=_DEFAULT_NOW):
        self.now = now
        self.elapsed = 1000.0
        self.calls = Counter()
        self.requests = []
        self.contracts = [self.contract("BTCUSDT"), self.contract("SAMSUNGUSDT", kind="TRADIFI_PERPETUAL")]
        self.funding_info = [{"symbol": "SAMSUNGUSDT", "fundingIntervalHours": 4}]
        self.rows = []
        self.rate_failure = None
        self.reverse = False

    @property
    def today(self):
        return self.now.replace(hour=0, minute=0, second=0, microsecond=0)

    def contract(self, symbol, *, kind="PERPETUAL", status="TRADING", onboard=None, quote="USDT"):
        return {
            "symbol": symbol,
            "contractType": kind,
            "baseAsset": symbol.removesuffix("USDT"),
            "quoteAsset": quote,
            "marginAsset": "USDT",
            "status": status,
            "onboardDate": _ms(onboard or self.today - 100 * _DAY),
        }

    async def request(self, request):
        self.requests.append(request)
        assert request.method == "GET"
        assert request.url.scheme == "https" and request.url.host == "fapi.binance.com"
        assert "authorization" not in request.headers and "x-mbx-apikey" not in request.headers
        name = request.url.path.removeprefix("/fapi/v1/")
        self.calls[name] += 1
        await asyncio.sleep(0)
        if name == "exchangeInfo":
            assert not request.url.params
            return httpx.Response(200, json={"symbols": self.contracts})
        if name == "fundingInfo":
            assert not request.url.params
            return httpx.Response(200, json=self.funding_info)
        assert name == "fundingRate"
        if self.rate_failure is not None:
            return self.rate_failure(request)
        start = int(request.url.params["startTime"])
        end = int(request.url.params["endTime"])
        limit = int(request.url.params["limit"])
        symbol = request.url.params["symbol"]
        rows = sorted(self.rows, key=lambda row: row["fundingTime"])
        page = [
            row for row in rows if start <= row["fundingTime"] <= end and row["symbol"] == symbol
        ][:limit]
        return httpx.Response(200, json=page[::-1] if self.reverse else page)

    def advance(self, delta):
        self.now += delta
        self.elapsed += delta.total_seconds()

    def service(self, *, enabled=True):
        return FundingHistory(
            transport=httpx.MockTransport(self.request), clock=lambda: self.now,
            monotonic=lambda: self.elapsed, enabled=enabled,
        )


def _ms(at):
    return int(at.timestamp() * 1000)


def _row(at, rate="-0.0001", *, symbol="BTCUSDT", mark="90000.10", rate_type=None, jitter=3):
    result = {
        "symbol": symbol, "fundingTime": _ms(at) + jitter, "fundingRate": rate, "markPrice": mark,
    }
    if rate_type is not None:
        result["rateType"] = rate_type
    return result


def _grid(at, step=8, rate="-0.0001", *, symbol="BTCUSDT"):
    return [_row(at + h * _HOUR, rate, symbol=symbol) for h in range(0, 24, step)]


def test_confirmed_rates_utc_days_average_streak_special_and_zero():
    async def run():
        exchange = Exchange(_DEFAULT_NOW.replace(hour=7))
        exchange.reverse = True
        for offset in range(1, 8):
            exchange.rows.extend(_grid(exchange.today - offset * _DAY))
        yesterday = exchange.today - _DAY
        exchange.rows.extend([
            _row(yesterday + 16 * _HOUR, "-0.0001", rate_type="Regular"),
            _row(yesterday + 16 * _HOUR, "0.0001", rate_type="Special"),
            _row(exchange.today, "0", mark="NaN", jitter=0),
        ])
        result = await exchange.service().read(" btcusdt ", 2)

        assert result["status"] == "available"
        assert result["symbol"] == "BTCUSDT"
        assert result["funding_interval_hours"] == 8
        assert result["latest_settlement"] == {
            "settled_at": "2026-11-24T00:00:00.000Z", "rate": 0, "mark_price": None, "rate_type": "regular",
        }
        assert result["current_day"] == {
            "date": "2026-11-24", "status": "partial", "total_rate": 0, "settlement_count": 1,
        }
        assert result["daily"][0]["average_7d"] is None
        assert result["daily"][1] == {
            "date": "2026-11-23", "status": "complete", "total_rate": -0.0002, "settlement_count": 4,
            "average_7d": pytest.approx(-0.002 / 7),
        }
        assert result["negative_streak_days"] == 7 and not result["negative_streak_truncated"]
        assert [row["rate_type"] for row in result["settlements"] if row["settled_at"].startswith("2026-11-23T16")] == [
            "regular", "special",
        ]

    asyncio.run(run())


def test_negative_streak_stops_at_zero_and_is_independent_of_display_range():
    async def run():
        exchange = Exchange()
        exchange.rows = [row for offset in range(1, 38) for row in _grid(exchange.today - offset * _DAY)]
        result = await exchange.service().read("BTCUSDT", 1)
        assert len(result["daily"]) == 1
        assert result["negative_streak_days"] == 37
        assert result["negative_streak_truncated"]
        assert result["daily"][0]["average_7d"] == -0.0003

        zero_day = exchange.today - 3 * _DAY
        exchange.rows[:] = [row for row in exchange.rows if row["fundingTime"] // 86400000 != _ms(zero_day) // 86400000]
        exchange.rows.extend(_grid(zero_day, rate="0"))
        result = await exchange.service().read("BTCUSDT", 4)
        assert result["negative_streak_days"] == 2
        assert not result["negative_streak_truncated"]
        assert result["daily"][1]["total_rate"] == 0
        assert result["daily"][1]["status"] == "complete"

    asyncio.run(run())


def test_damaged_or_ambiguous_days_do_not_fill_rates_or_seven_day_window():
    async def run():
        exchange = Exchange()
        exchange.rows = [
            *_grid(exchange.today - 1 * _DAY, 4),  # complete historical faster grid
            *_grid(exchange.today - 2 * _DAY, 12),  # ambiguous slower grid
            *_grid(exchange.today - 4 * _DAY), _row(exchange.today - 4 * _DAY, "-0.0005"),
            *_grid(exchange.today - 5 * _DAY), _row(exchange.today - 5 * _DAY, "NaN", jitter=5),
            *_grid(exchange.today - 6 * _DAY), _row(exchange.today - 6 * _DAY, rate_type="Other"),
            *_grid(exchange.today - 7 * _DAY), _row(exchange.today - 7 * _DAY, jitter=4),
            _row(exchange.today - 8 * _DAY, jitter=-1),
        ]
        result = await exchange.service().read("BTCUSDT", 8)
        daily = result["daily"]
        assert [row["status"] for row in daily] == [
            "missing", "partial", "partial", "partial", "partial", "missing", "partial", "complete",
        ]
        assert all(row["total_rate"] is None for row in daily[:-1])
        assert all(row["average_7d"] is None for row in daily)
        assert daily[4]["settlement_count"] == 2  # conflicting same instant omitted
        assert daily[-1]["total_rate"] == -0.0006
        assert result["negative_streak_days"] == 1

    asyncio.run(run())


def test_equity_contract_four_hour_grid_and_nontrading_metadata_never_fetch_history():
    async def run():
        exchange = Exchange()
        yesterday = exchange.today - _DAY
        exchange.contracts = [
            exchange.contract("SAMSUNGUSDT", kind="TRADIFI_PERPETUAL", onboard=yesterday + _HOUR),
            exchange.contract("SOXXUSDT", kind="TRADIFI_PERPETUAL", status="PENDING_TRADING"),
            exchange.contract("NVDAUSDT"),   # an unsupported contract type for curated equity
            exchange.contract("ETHUSDT", quote="BTC"),
        ]
        exchange.rows = [
            *_grid(yesterday, 4, symbol="SAMSUNGUSDT"),
            *_grid(exchange.today, 4, symbol="SAMSUNGUSDT"),
        ]
        service = exchange.service()
        samsung, soxx, nvda, ether, missing = await asyncio.gather(
            service.read("SAMSUNGUSDT", 3), service.read("SOXXUSDT", 1), service.read("NVDAUSDT", 1),
            service.read("ETHUSDT", 1), service.read("AMDUSDT", 1),
        )

        assert exchange.calls == {"exchangeInfo": 1, "fundingInfo": 1, "fundingRate": 1}
        assert samsung["underlying"] == {"kind": "stock", "ticker": "005930", "name": "삼성전자"}
        assert samsung["funding_interval_hours"] == 4
        assert samsung["status"] == "available"
        assert samsung["daily"][-1]["status"] == "partial"  # before actual trading began
        assert samsung["current_day"]["settlement_count"] == 4
        assert soxx["underlying"]["kind"] == "etf"
        assert soxx["status"] == "inactive" and soxx["contract_status"] == "PENDING_TRADING"
        assert nvda["status"] == ether["status"] == missing["status"] == "unsupported"

    asyncio.run(run())


def test_independent_windows_share_one_inflight_snapshot_and_a_fresh_cache():
    async def run():
        exchange = Exchange()
        exchange.rows = _grid(exchange.today - _DAY)
        service = exchange.service()
        seven, thirty, equity = await asyncio.gather(
            service.read("BTCUSDT", 7), service.read("BTCUSDT", 30), service.read("SAMSUNGUSDT", 7),
        )
        assert seven["fetched_at"] == thirty["fetched_at"]
        assert len(seven["daily"]) == 7 and len(thirty["daily"]) == 30
        assert equity["status"] == "no_data" and equity["reason"] == "no_settlements"
        assert all(row["status"] == "missing" for row in equity["daily"])
        assert exchange.calls == {"exchangeInfo": 1, "fundingInfo": 1, "fundingRate": 2}
        await service.read("BTCUSDT", 7)
        assert exchange.calls["fundingRate"] == 2
        btc = next(req for req in exchange.requests if req.url.params.get("symbol") == "BTCUSDT")
        assert btc.url.params["startTime"] == str(_ms(exchange.today - 37 * _DAY))
        assert btc.url.params["endTime"] == str(_ms(exchange.now))

    asyncio.run(run())


def test_curated_symbols_limits_and_disabled_mode_hold_before_transport():
    async def run():
        exchange = Exchange()
        service = exchange.service(enabled=False)
        for symbol, days in [
            ("BNBUSDT", 7), ("BTCUSDT", 0), ("BTCUSDT", 31), ("BTCUSDT", True), ("BTCUSDT", "7"),
            ("https://fapi.binance.com", 7),
        ]:
            with pytest.raises(ValueError, match="invalid funding symbol or days"):
                await service.read(symbol, days)
        disabled = await service.read("BTCUSDT", 7)
        assert disabled["status"] == "source_error" and disabled["reason"] == "fake_runs"
        assert disabled["fetched_at"] is None and disabled["settlements"] == []
        assert not exchange.calls

    asyncio.run(run())


def test_timeout_fallback_crosses_utc_day_and_never_relabels_fetched_at():
    async def run():
        exchange = Exchange(datetime(2026, 11, 24, 23, 59, tzinfo=UTC))
        exchange.rows = _grid(exchange.today)
        service = exchange.service()
        before = await service.read("BTCUSDT", 7)
        assert before["current_day"]["settlement_count"] == 3

        def timeout(request):
            raise httpx.ReadTimeout("test", request=request)

        exchange.advance(3 * timedelta(minutes=1))
        exchange.rate_failure = timeout
        after = await service.read("BTCUSDT", 7)
        assert after["status"] == "stale" and after["reason"] == "timeout"
        assert after["fetched_at"] == before["fetched_at"]
        assert after["daily"][-1]["status"] == "complete"
        assert after["daily"][-1]["date"] == before["current_day"]["date"]
        assert after["current_day"]["date"] == "2026-11-25"
        assert after["current_day"]["total_rate"] is None
        assert after["negative_streak_days"] == 1

        after_retry = await service.read("BTCUSDT", 30)
        assert after_retry["reason"] == "timeout" and exchange.calls["fundingRate"] == 2
        exchange.advance(6 * _HOUR)
        expired = await service.read("BTCUSDT", 7)
        assert expired["status"] == "source_error" and expired["reason"] == "timeout"
        assert expired["fetched_at"] is None

    asyncio.run(run())


@pytest.mark.parametrize("http_status", [429, 418])
def test_retry_after_applies_across_symbols_and_can_recover(http_status):
    async def run():
        exchange = Exchange()
        exchange.rows = _grid(exchange.today, symbol="BTCUSDT")
        service = exchange.service()
        before = await service.read("BTCUSDT", 7)
        exchange.advance(timedelta(minutes=2))
        exchange.rate_failure = lambda _: httpx.Response(http_status, headers={"Retry-After": "120"})
        blocked = await service.read("BTCUSDT", 7)
        uncached = await service.read("SAMSUNGUSDT", 30)

        assert blocked["status"] == "stale" and blocked["reason"] == "rate_limited"
        assert blocked["fetched_at"] == before["fetched_at"]
        assert uncached["status"] == "source_error" and uncached["reason"] == "rate_limited"
        assert exchange.calls["fundingRate"] == 2

        exchange.advance(timedelta(seconds=119))
        assert (await service.read("SAMSUNGUSDT", 7))["reason"] == "rate_limited"
        assert exchange.calls["fundingRate"] == 2
        exchange.advance(timedelta(seconds=1))
        exchange.rate_failure = None
        assert (await service.read("BTCUSDT", 7))["status"] == "available"
        assert exchange.calls["fundingRate"] == 3

    asyncio.run(run())


def test_delayed_regular_settlement_and_redirect_are_finite_failures():
    async def run():
        exchange = Exchange()
        exchange.rows = [_row(exchange.today), _row(exchange.today + 12 * _HOUR, rate_type="Special")]
        delayed = await exchange.service().read("BTCUSDT", 1)
        assert delayed["status"] == "stale"
        assert delayed["reason"] == "settlement_delayed"
        assert delayed["latest_settlement"]["rate_type"] == "special"

        exchange.rate_failure = lambda _: httpx.Response(307, headers={"Location": "https://example.com/funding"})
        redirected = await exchange.service().read("BTCUSDT", 1)
        assert redirected["status"] == "source_error"
        assert redirected["reason"] == "upstream_error"
        assert exchange.calls["fundingRate"] == 2

    asyncio.run(run())


def test_pagination_preserves_regular_and_special_at_inclusive_boundary(monkeypatch):
    monkeypatch.setattr(binance_funding, "_PAGE_SIZE", 4)

    async def run():
        exchange = Exchange()
        yesterday = exchange.today - _DAY
        exchange.rows = [
            *_grid(yesterday),
            _row(exchange.today),
            _row(exchange.today, rate_type="Special"),
        ]
        result = await exchange.service().read("BTCUSDT", 1)
        assert len(result["settlements"]) == 5
        assert result["latest_settlement"]["rate_type"] == "regular"
        assert exchange.calls["fundingRate"] == 2
        assert [request.url.params["startTime"] for request in exchange.requests[-2:]][1] == str(_ms(exchange.today) + 3)

    asyncio.run(run())


def test_shared_snapshot_reused_and_malformed_snapshot_does_not_override_local_last_good(monkeypatch):
    payloads = {}

    async def put(namespace, key, value, ttl, *, rank):
        assert namespace == "binance-funding-v1" and ttl == 6 * 3600
        assert rank(value) > 0
        payloads[key] = (value, ttl, ttl)

    async def get(namespace, key):
        return payloads.get(key)

    monkeypatch.setattr(shared_cache, "aget", get)
    monkeypatch.setattr(shared_cache, "aput_newer", put)

    async def run():
        exchange = Exchange()
        exchange.rows = _grid(exchange.today)
        producer = exchange.service()
        consumer = exchange.service()
        fetched = await producer.read("BTCUSDT", 7)
        hit = await consumer.read("BTCUSDT", 30)
        assert exchange.calls["fundingRate"] == 1 and fetched["fetched_at"] == hit["fetched_at"]
        assert hit["settlements"] == fetched["settlements"]

        exchange.advance(timedelta(minutes=2))
        cache, ttl, _ = payloads["BTCUSDT"]
        payloads["BTCUSDT"] = (
            {**cache, "fetched_ms": _ms(exchange.now), "cutoff_ms": _ms(exchange.now),
             "rows": [{"fundingRate": "malformed"}]}, ttl, ttl,
        )
        exchange.rate_failure = lambda _: httpx.Response(500)
        fallback = await consumer.read("BTCUSDT", 7)
        assert fallback["status"] == "stale" and fallback["reason"] == "upstream_error"
        assert fallback["fetched_at"] == fetched["fetched_at"]
        assert fallback["settlements"] == fetched["settlements"]

    asyncio.run(run())


def test_late_pre_midnight_refresh_does_not_replace_newer_shared_cutoff(monkeypatch):
    payloads = {}

    async def put(namespace, key, value, ttl, *, rank):
        existing = payloads.get(key)
        if existing is None or rank(value) > rank(existing[0]):
            payloads[key] = (value, ttl, ttl)

    async def get(namespace, key):
        return payloads.get(key)

    monkeypatch.setattr(shared_cache, "aget", get)
    monkeypatch.setattr(shared_cache, "aput_newer", put)

    async def run():
        exchange = Exchange(datetime(2026, 11, 24, 23, 59, tzinfo=UTC))
        exchange.rows = [*_grid(exchange.today), _row(exchange.today + _DAY)]
        old_history_started, complete_old = asyncio.Event(), asyncio.Event()

        async def delayed_request(request):
            if request.url.path.endswith("/fundingRate"):
                old_history_started.set()
                await complete_old.wait()
            return await exchange.request(request)

        old_service = FundingHistory(transport=httpx.MockTransport(delayed_request), clock=lambda: exchange.now)
        old_fetch = asyncio.create_task(old_service.read("BTCUSDT", 7))
        await old_history_started.wait()

        exchange.advance(timedelta(minutes=2))
        new = await exchange.service().read("BTCUSDT", 7)
        exchange.advance(timedelta(seconds=1))
        complete_old.set()
        old = await old_fetch
        assert old["fetched_at"] > new["fetched_at"]
        assert old["current_day"]["settlement_count"] == 0
        observer = await exchange.service().read("BTCUSDT", 7)
        assert observer["fetched_at"] == new["fetched_at"]
        assert observer["current_day"]["settlement_count"] == 1
        assert exchange.calls["fundingRate"] == 2

    asyncio.run(run())


def test_unassignable_settlement_time_invalidates_page_and_empty_cache_cannot_fallback():
    async def run():
        exchange = Exchange()
        service = exchange.service()
        assert (await service.read("BTCUSDT", 7))["status"] == "no_data"

        exchange.advance(timedelta(minutes=2))
        yesterday = exchange.today - _DAY
        special = _row(yesterday, rate_type="Special")
        special["fundingTime"] = "unassignable"
        exchange.rate_failure = lambda _: httpx.Response(200, json=[*_grid(yesterday), special])
        invalid = await service.read("BTCUSDT", 7)
        assert invalid["status"] == "source_error" and invalid["reason"] == "invalid_response"
        assert invalid["fetched_at"] is None

    asyncio.run(run())


def test_decoded_response_cap(monkeypatch):
    monkeypatch.setattr(binance_funding, "_HISTORY_BYTES", 64)

    async def run():
        exchange = Exchange()
        exchange.rows = _grid(exchange.today)
        invalid = await exchange.service().read("BTCUSDT", 7)
        assert invalid["status"] == "source_error" and invalid["reason"] == "invalid_response"

    asyncio.run(run())
