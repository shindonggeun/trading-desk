import asyncio

from desk.futures_flow import OPTION_MARKETS
from tests.flow_cache_benchmark import URLS, benchmark


def test_benchmark_separates_cold_collector_and_local_warm_requests():
    samples = 2
    report = asyncio.run(benchmark(samples=samples, upstream_ms=0, redis_ms=0))
    assert report["transport"] == "in-process ASGI"
    assert report["redis"] == "in-memory synthetic"
    cases = {(c["endpoint"], c["redis_enabled"], c["state"]): c for c in report["cases"]}
    assert len(cases) == len(report["cases"]) == 12
    for endpoint, methods in (
        ("market_flow", {"spot_flow", "spot_index"}),
        ("futures", {"spot_flow", "spot_index", "futures_price", "futures_investors"}),
    ):
        full = dict.fromkeys(methods, samples)
        if "futures_investors" in full:   # #1007 the investor screen read for futures, calls and puts
            full["futures_investors"] = samples * (1 + len(OPTION_MARKETS))
        for enabled in (False, True):
            cold = cases[URLS[endpoint], enabled, "cold"]
            assert cold["api_origin_by_method"] == full
            assert cold["redis_hits"] == 0
            collector = cases[URLS[endpoint], enabled, "collector_warm"]
            assert collector["api_origin_by_method"] == ({} if enabled else full)
            assert collector["redis_hits"] == (samples if enabled else 0)
            local = cases[URLS[endpoint], enabled, "api_warm"]
            assert local["api_origin_calls"] == local["redis_hits"] == 0
    assert all(c["samples"] == samples and 0 <= c["p50_ms"] <= c["p95_ms"] for c in cases.values())
