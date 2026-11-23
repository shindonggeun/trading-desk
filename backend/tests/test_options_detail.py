import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest

from desk import options_detail as opt

NOW = datetime(2026, 11, 7, 1, tzinfo=UTC)  # Friday evening in New York.
TODAY = date(2026, 11, 6)
LATER = date(2026, 11, 13)
PAST = date(2026, 11, 5)


def _contract(symbol, side, strike, volume, oi, standard=True):
    return opt.OptionContract(symbol, side, strike, volume, oi, standard)


def _snapshot(expiration=TODAY, *contracts, spot=101):
    return opt.OptionChain(
        expiration, contracts,
        volume=opt.MetricTime(TODAY, NOW - timedelta(minutes=15), 900),
        oi=opt.MetricTime(PAST),
        spot=opt.Underlying(spot, NOW - timedelta(seconds=25), 25),
    )


class Source:
    def __init__(self, expirations=(LATER, PAST, TODAY, LATER), chains=None):
        self.expirations = expirations
        self.chains = chains or {
            TODAY: _snapshot(TODAY, _contract("a", "call", 100, 10, 100), _contract("b", "put", 100, 15, 50)),
            LATER: _snapshot(LATER, _contract("a", "call", 100, 50, 50), _contract("b", "put", 110, 10, 100)),
        }
        self.catalog_calls = []
        self.chain_calls = []
        self.active = 0
        self.peak = 0

    async def _read(self, value):
        self.active += 1
        self.peak = max(self.active, self.peak)
        try:
            await asyncio.sleep(0)
            if isinstance(value, Exception):
                raise value
            return value
        finally:
            self.active -= 1

    async def catalog(self, ticker):
        self.catalog_calls.append(ticker)
        if isinstance(self.expirations, Exception):
            return await self._read(self.expirations)
        return await self._read(opt.OptionCatalog("fixture", self.expirations))

    async def chain(self, ticker, expiration):
        self.chain_calls.append((ticker, expiration))
        return await self._read(self.chains[expiration])


def _service(source=None, **options):
    return opt.OptionsDetail(source, now=lambda: NOW, **options)


def test_defaults_to_first_new_york_expiry_and_selects_another_chain():
    source = Source()
    service = _service(source)

    async def both():
        return await service.get("NVDA"), await service.get("NVDA", LATER)

    near, later = asyncio.run(both())
    assert near["status"] == later["status"] == "ok"
    assert near["expirations"] == ["2026-11-06", "2026-11-13"]
    assert near["expiration"] == "2026-11-06" and later["expiration"] == "2026-11-13"
    assert near["volume"]["put_call_ratio"] == 1.5 and near["oi"]["put_call_ratio"] == .5
    assert later["volume"]["put_call_ratio"] == .2 and later["oi"]["put_call_ratio"] == 2
    assert source.catalog_calls == ["NVDA"]
    assert source.chain_calls == [("NVDA", TODAY), ("NVDA", LATER)]


def test_preserves_separate_reported_dates_times_and_delays():
    result = asyncio.run(_service(Source()).get("AAPL"))
    assert result["source"] == "fixture" and result["fetched_at"] == NOW.isoformat()
    assert result["volume"] == {"calls": 10, "puts": 15, "put_call_ratio": 1.5, "status": "ok",
                                "trade_date": "2026-11-06", "as_of": (NOW - timedelta(minutes=15)).isoformat(),
                                "delay_sec": 900}
    assert result["oi"]["trade_date"] == "2026-11-05"
    assert result["oi"]["as_of"] is None and result["oi"]["delay_sec"] is None
    assert result["spot"] == {"price": 101, "as_of": (NOW - timedelta(seconds=25)).isoformat(), "delay_sec": 25}


def test_distinguishes_actual_zero_calls_from_partial_and_missing_metrics():
    source = Source(chains={TODAY: _snapshot(
        TODAY, _contract("c1", "call", 100, 0, float("nan")),
        _contract("c2", "call", 105, "0", -10),
        _contract("p1", "put", 100, "12", 42),
        _contract("p2", "put", 110, 10, 2.5),
    )})
    result = asyncio.run(_service(source).get("SPY"))
    assert result["volume"]["calls"] == 0 and result["volume"]["puts"] == 22
    assert result["volume"]["status"] == "zero_calls" and result["volume"]["put_call_ratio"] is None
    assert result["oi"]["calls"] is None and result["oi"]["puts"] is None
    assert result["oi"]["status"] == "partial" and result["oi"]["put_call_ratio"] is None
    assert result["strikes"][0] == {"strike": 100, "call_volume": 0, "put_volume": 12,
                                     "call_oi": None, "put_oi": 42}
    assert result["top"]["oi"]["puts"] == [{"strike": 100, "count": 42}]
    assert result["top"]["volume"]["calls"] == []

    source = Source(chains={TODAY: _snapshot(TODAY, _contract("c", "call", 100, None, True))})
    missing = asyncio.run(_service(source).get("NVDA"))
    assert missing["volume"]["status"] == missing["oi"]["status"] == "missing"


def test_filters_nonstandard_and_repeated_contracts_before_aggregating():
    call = _contract("a", "call", 100, 10, 20)
    source = Source(chains={TODAY: _snapshot(
        TODAY, call, call, _contract("b", "call", 100, 5, 30), _contract("p", "put", 100, 15, 100),
        _contract("bad_adj", "call", 100, 1000, 1000, False), _contract("unknown", "put", 90, 1000, 1000, None),
    )})
    result = asyncio.run(_service(source).get("NVDA"))
    assert result["excluded_contracts"] == 3
    assert result["strikes"] == [{"strike": 100, "call_volume": 15, "put_volume": 15, "call_oi": 50, "put_oi": 100}]
    assert result["oi"]["put_call_ratio"] == 2


def test_keeps_full_expiry_totals_for_filtered_display_and_limits_strike_rows():
    contracts = [_contract("1", "call", 1, 90, 180)]
    contracts.extend(_contract(str(n), "call", n, 1, 2) for n in range(2, opt.MAX_STRIKES + 4))
    contracts.append(_contract("put", "put", 100, 8, 16))
    source = Source(chains={TODAY: _snapshot(TODAY, *contracts, spot=400)})
    service = _service(source)

    async def results():
        return (await service.get("SPY"), await service.get("SPY", strike_min=99, strike_max=101),
                await service.get("SPY", strike_min=10_000))

    all_, ranged, empty = asyncio.run(results())
    assert all_["range"]["truncated"] and len(all_["strikes"]) == opt.MAX_STRIKES
    assert all_["strikes"][0]["strike"] == 4
    assert all_["top"]["volume"]["calls"][0] == {"strike": 1, "count": 90}
    assert all_["top"]["oi"]["calls"][0] == {"strike": 1, "count": 180}
    assert ranged["range"] == {"min": 99, "max": 101, "total_strikes": opt.MAX_STRIKES + 3,
                               "matching_strikes": 3, "limit": opt.MAX_STRIKES, "truncated": False}
    assert [r["strike"] for r in ranged["strikes"]] == [99, 100, 101]
    assert ranged["top"]["volume"]["puts"] == [{"strike": 100, "count": 8}]
    assert ranged["top"]["volume"]["calls"][0] == {"strike": 99, "count": 1}
    assert empty["status"] == "no_data" and empty["reason"] == "empty_range"
    assert all_["volume"] == ranged["volume"] == empty["volume"]
    assert all_["oi"] == ranged["oi"] == empty["oi"]
    assert len(source.chain_calls) == 1  # Display range is not an upstream query key.


@pytest.mark.parametrize(("low", "high"), [(0, 100), (float("inf"), None), (None, float("nan")), (110, 100)])
def test_rejects_invalid_strike_bounds_before_provider_work(low, high):
    source = Source()
    with pytest.raises(ValueError, match="strike range"):
        asyncio.run(_service(source).get("SPY", strike_min=low, strike_max=high))
    assert source.catalog_calls == []


def test_distinguishes_missing_provider_empty_listing_and_invalid_expiration():
    no_provider = asyncio.run(_service().get("NVDA"))
    assert no_provider["status"] == "unavailable" and no_provider["source"] is None
    assert no_provider["reason"] == "no_provider" and no_provider["fetched_at"] is None

    listed = Source()
    invalid = asyncio.run(_service(listed).get("AAPL", PAST))
    assert invalid["status"] == "invalid_expiration" and invalid["expirations"]
    assert listed.chain_calls == []

    for dates, expected in [((), "no_options"), ((PAST,), "no_data")]:
        source = Source(expirations=dates)
        result = asyncio.run(_service(source).get("AAPL"))
        assert result["status"] == expected and result["source"] == "fixture"
        assert source.chain_calls == []


def test_source_failures_expire_quickly_and_never_become_no_options():
    clock = [0.0]
    source = Source(expirations=RuntimeError("private upstream detail"))
    service = _service(source, clock=lambda: clock[0])

    async def calls():
        first = await service.get("NVDA")
        source.expirations = (TODAY,)
        cached = await service.get("NVDA")
        clock[0] += opt.FAILURE_SECONDS
        retrieved = await service.get("NVDA")
        return first, cached, retrieved

    first, cached, retrieved = asyncio.run(calls())
    assert first == cached and first["status"] == "unavailable"
    assert first["reason"] == "catalog_unavailable" and "private" not in str(first)
    assert retrieved["status"] == "ok"
    assert source.catalog_calls == ["NVDA"] * 2 and len(source.chain_calls) == 1


def test_missing_and_broken_chain_are_not_an_unlisted_symbol():
    broken = Source(chains={TODAY: OSError("offline")})
    empty = Source(chains={TODAY: _snapshot()})
    wrong_expiry = Source(chains={TODAY: _snapshot(LATER)})
    conflict = Source(chains={TODAY: _snapshot(
        TODAY, _contract("a", "call", 100, 10, 10), _contract("a", "put", 101, 10, 10),
    )})
    for source, status, reason in [
        (broken, "unavailable", "chain_unavailable"),
        (empty, "no_data", "no_standard_contracts"),
        (wrong_expiry, "unavailable", "chain_unavailable"),
        (conflict, "unavailable", "chain_unavailable"),
    ]:
        result = asyncio.run(_service(source).get("SPY"))
        assert (result["status"], result["reason"]) == (status, reason)
        assert result["volume"]["calls"] is None


def test_conflicting_contracts_are_cached_as_a_short_failure():
    source = Source(chains={TODAY: _snapshot(
        TODAY, _contract("a", "call", 100, 10, 10), _contract("a", "call", 100, 11, 10),
    )})
    clock = [0.0]
    service = _service(source, clock=lambda: clock[0])

    async def calls():
        first = await service.get("SPY")
        source.chains[TODAY] = _snapshot(TODAY, _contract("a", "call", 100, 10, 10))
        second = await service.get("SPY")
        clock[0] = opt.FAILURE_SECONDS
        return first, second, await service.get("SPY")

    failed, cached, recovered = asyncio.run(calls())
    assert failed == cached and failed["reason"] == "chain_unavailable"
    assert recovered["status"] == "ok" and recovered["volume"]["calls"] == 10
    assert source.chain_calls == [("SPY", TODAY)] * 2


@pytest.mark.parametrize(("symbol", "side", "strike"), [("", "call", 100), ("b", "unknown", 110),
                                                        ("b", "put", float("inf")), ("b", "put", 0)])
def test_broken_standard_contract_identity_invalidates_the_whole_chain(symbol, side, strike):
    call = _contract("a", "call", 100, 10, 10)
    put = _contract("b", "put", 110, 15, 15)
    source = Source(chains={TODAY: _snapshot(TODAY, call, _contract(symbol, side, strike, 15, 15))})
    clock = [0.0]
    service = _service(source, clock=lambda: clock[0])

    async def calls():
        first = await service.get("SPY")
        source.chains[TODAY] = _snapshot(TODAY, call, put)
        second = await service.get("SPY")
        clock[0] = opt.FAILURE_SECONDS
        return first, second, await service.get("SPY")

    failed, cached, recovered = asyncio.run(calls())
    assert failed == cached and failed["reason"] == "chain_unavailable"
    assert failed["volume"]["put_call_ratio"] is None
    assert recovered["volume"]["put_call_ratio"] == 1.5
    assert source.chain_calls == [("SPY", TODAY)] * 2


def test_cached_normalization_and_response_copies(monkeypatch):
    source = Source()
    service = _service(source)
    calls = []
    summary = opt._summary

    def counted(chain):
        calls.append(chain.expiration)
        return summary(chain)

    monkeypatch.setattr(opt, "_summary", counted)

    async def read_twice():
        first = await service.get("SPY")
        first["strikes"][0]["call_volume"] = 999
        first["volume"]["calls"] = 999
        first["spot"]["price"] = 999
        first["top"]["volume"]["calls"][0]["count"] = 999
        return await service.get("SPY", strike_min=100)

    result = asyncio.run(read_twice())
    assert calls == [TODAY]
    assert result["strikes"][0]["call_volume"] == result["volume"]["calls"] == 10
    assert result["spot"]["price"] == 101
    assert result["top"]["volume"]["calls"] == [{"strike": 100, "count": 10}]


def test_contract_limit_reports_an_unavailable_chain_without_truncating_totals(monkeypatch):
    monkeypatch.setattr(opt, "MAX_CONTRACTS", 1)
    got = asyncio.run(_service(Source()).get("SPY"))
    assert got["status"] == "unavailable" and got["volume"]["calls"] is None and not got["strikes"]


def test_cache_coalesces_symbol_and_expiry_and_expires_success():
    source = Source()
    clock = [0.0]
    service = _service(source, clock=lambda: clock[0])

    async def calls():
        default, explicit, another, first_aapl = await asyncio.gather(
            service.get("SPY"), service.get("SPY", TODAY), service.get("SPY", LATER), service.get("AAPL"))
        same_spy = await service.get("SPY")
        clock[0] = opt.CACHE_SECONDS
        await service.get("SPY")
        return default, explicit, another, first_aapl, same_spy

    default, explicit, another, first_aapl, same_spy = asyncio.run(calls())
    assert default == explicit == same_spy
    assert another["expiration"] == LATER.isoformat() and first_aapl["ticker"] == "AAPL"
    assert source.catalog_calls.count("SPY") == 2 and source.catalog_calls.count("AAPL") == 1
    assert source.chain_calls.count(("SPY", TODAY)) == 2
    assert source.chain_calls.count(("SPY", LATER)) == 1
    assert source.chain_calls.count(("AAPL", TODAY)) == 1


def test_origin_parallelism_is_bounded_across_distinct_tickers():
    source = Source()
    service = _service(source)

    async def request_many():
        results = await asyncio.gather(*(service.get(f"FIXTURE{i}") for i in range(10)))
        assert all(result["status"] == "ok" for result in results)

    asyncio.run(request_many())
    assert source.peak == opt.MAX_PARALLEL
    assert len(source.catalog_calls) == len(source.chain_calls) == 10
