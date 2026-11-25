import asyncio
import os
import socket
from collections import Counter
from dataclasses import asdict
from types import SimpleNamespace

import httpx
import pytest

from desk import market_bus, oscillator, shared_cache, sr
from tests import screen_cache_benchmark as bench


def test_screen_matrix_distinguishes_local_bars_from_shared_results(monkeypatch, tmp_path):
    # Even a malformed local .env must not control the benchmark's dependencies or switches.
    monkeypatch.setenv("DESK_DATABASE_URL", "postgresql://unused.invalid/desk")
    monkeypatch.setenv("DESK_REDIS_URL", "redis://unused.invalid")
    monkeypatch.setenv("DESK_ROLE", "invalid")
    monkeypatch.setenv("DESK_FAKE_DELAY", "invalid")
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing-ca.pem"))
    keylog = tmp_path / "tls-keylog"
    monkeypatch.setenv("SSLKEYLOGFILE", str(keylog))
    before = shared_cache.stats()
    bus = market_bus.BUS
    originals = (oscillator._CACHE.local, sr._CACHE.local)
    result = asyncio.run(bench.benchmark(samples=2, upstream_ms=0, redis_ms=0))
    assert shared_cache.stats() == before
    assert market_bus.BUS is bus
    assert (oscillator._CACHE.local, sr._CACHE.local) == originals
    assert os.environ["SSLKEYLOGFILE"] == str(keylog)
    assert not keylog.exists()
    assert result["timing"] == "synthetic in-process ASGI"
    assert result["redis"] == "synthetic in-memory sync and async clients"
    assert all(case["samples"] == 2 and 0 <= case["p50_ms"] <= case["p95_ms"] for case in result["cases"])

    screens = result["screens"]
    assert screens["dashboard"]["requests"] == [
        {"path": "/api/candles", "params": {"ticker": ticker, "interval": "1d", "count": 5}}
        for ticker in ("^KS11", "^KQ11", "NQ=F")
    ]
    assert screens["chart"]["shared_namespaces"] == ["oscillator"]
    assert screens["chart"]["requests"] == [
        {"path": "/api/candles", "params": {"ticker": "QQQ", "interval": "1d", "count": 130,
                                               "indicators": "rsi,stoch"}},
        {"path": "/api/oscillator", "params": {"ticker": "QQQ", "interval": "1d"}},
    ]
    assert screens["market_board"]["shared_namespaces"] == ["sr"]
    assert [req["path"] for req in screens["market_board"]["requests"]] == ["/api/batch/candles", "/api/batch/levels"]

    cases = {(row["screen"], row["redis_enabled"], row["state"]): row for row in result["cases"]}
    assert len(result["cases"]) == len(cases) == 18
    for screen, expected_origins in (
        ("dashboard", 6),
        ("chart", 4),
        ("market_board", 16),        # #874 the tiles' 1d 63 rides the levels' 1d 200 read (one family): 4 + 4 a sample
    ):
        for enabled in (False, True):
            cold = cases[screen, enabled, "cold"]
            peer = cases[screen, enabled, "peer_warm"]
            warm = cases[screen, enabled, "api_warm"]
            assert cold["primer_samples"] == 0
            assert peer["primer_samples"] == 2
            assert warm["primer_samples"] == 1
            assert cold["api_origin_by_method"] == {"yahoo_candles": expected_origins}
            assert peer["primer_origin_by_method"] == cold["api_origin_by_method"]
            assert warm["api_origin_calls"] == 0
            assert warm["redis_calls_by_method"] == {}
            assert cold["redis_stats"]["hits"] == 0
            assert all(case["redis_stats"]["errors"] == case["redis_stats"]["rejected"] == 0
                       for case in (cold, peer, warm))
            if not enabled or screen == "dashboard":
                assert peer["api_origin_by_method"] == cold["api_origin_by_method"]
                for case in (cold, peer, warm):
                    assert not any(case["redis_stats"].values())
                    assert case["redis_calls_by_method"] == {}

    chart_peer = cases["chart", True, "peer_warm"]
    assert chart_peer["api_origin_by_method"] == {"yahoo_candles": 2}
    assert chart_peer["redis_stats"]["hits"] == 2
    assert chart_peer["redis_calls_by_method"] == {"sync_pipeline": 2}
    assert cases["chart", True, "cold"]["redis_calls_by_method"] == {"sync_pipeline": 2, "sync_set": 2}
    board_peer = cases["market_board", True, "peer_warm"]
    # Sharing the computed levels still leaves the per-app gap enrichment and tile candles local.
    assert board_peer["api_origin_calls"] == cases["market_board", True, "cold"]["api_origin_calls"]
    assert board_peer["redis_stats"]["hits"] == 8
    assert board_peer["redis_calls_by_method"] == {"async_pipeline": 8}
    assert cases["market_board", True, "cold"]["redis_calls_by_method"] == {"async_pipeline": 8, "async_set": 8}


@pytest.mark.parametrize("kind", ["candles", "levels"])
def test_payload_validation_rejects_a_nested_batch_failure(kind):
    request = bench.Request(kind, ("^KS11", "^KQ11"), batch=True, interval="1d", count=1)
    if kind == "candles":
        valid = {"ticker": "^KS11", "interval": "1d", "source": "yahoo", "truncated": False,
                 "bars": [asdict(bench._history(2900.0, "1d", 1)[0])], "indicators": []}
    else:
        valid = {"ticker": "^KS11", "reference": "close", "reference_price": 2900, "atr": 12,
                 "as_of": bench.FIXTURE_END.isoformat(), "gaps_error": None, "gaps": [],
                 "levels": [{"price": 2890}]}
    body = {"kind": kind, "items": {"^KS11": valid, "^KQ11": {"error": 503, "detail": "fixture failure"}}}
    response = httpx.Response(200, json=body, request=httpx.Request("GET", f"https://bench.test{request.path}"))
    with pytest.raises(RuntimeError, match=r"\^KQ11"):
        bench._validate_response(request, response)


@pytest.mark.parametrize("field", ["rsi", "stoch_k", "stoch_d"])
def test_payload_validation_requires_chart_indicators_on_latest_bar(field):
    request = bench.Request("candles", ("QQQ",), interval="1d", count=1, indicators=("rsi", "stoch"))
    latest = {**asdict(bench._history(510.0, "1d", 1)[0]), "rsi": 50.0, "stoch_k": 48.0, "stoch_d": 49.0}
    body = {"ticker": "QQQ", "interval": "1d", "source": "yahoo", "truncated": False,
            "bars": [latest], "indicators": ["rsi14", "stoch"]}
    http_request = httpx.Request("GET", f"https://bench.test{request.path}")
    bench._validate_response(request, httpx.Response(200, json=body, request=http_request))

    latest.pop(field)
    with pytest.raises(RuntimeError, match="candles.*QQQ"):
        bench._validate_response(request, httpx.Response(200, json=body, request=http_request))


def test_payload_validation_rejects_a_nested_oscillator_failure():
    request = bench.Request("oscillator", ("QQQ",), interval="1d")
    body = {"ticker": "QQQ", "interval": "1d", "own": {"zone": None, "error": "too few bars"}, "underlying": None}
    response = httpx.Response(200, json=body, request=httpx.Request("GET", f"https://bench.test{request.path}"))
    with pytest.raises(RuntimeError, match="oscillator.*QQQ"):
        bench._validate_response(request, response)


def test_network_guard_detects_an_attempt_even_if_a_fallback_handles_the_exception():
    original = socket.getaddrinfo

    def handled_by_fallback():
        with pytest.raises(RuntimeError, match="blocked"):
            socket.getaddrinfo("external.invalid", 443)

    with pytest.raises(RuntimeError, match="unexpected network access.*getaddrinfo"), bench._block_network():
        handled_by_fallback()
    assert socket.getaddrinfo is original


def test_synthetic_redis_clients_share_pipeline_values_and_expiry():
    async def exercise_clients():
        now = 0.0
        storage = bench._RedisStore(0, clock=lambda: now)
        sync_client, async_client = bench._SyncRedis(storage), bench._AsyncRedis(storage)
        sync_client.set("key", "sync-value", px=500)
        now = .25
        pipe = async_client.pipeline(transaction=False)
        pipe.get("key")
        pipe.pttl("key")
        assert await pipe.execute() == ("sync-value", 250)

        await async_client.set("key", "async-value", px=500)
        sync_pipe = sync_client.pipeline(transaction=False)
        sync_pipe.get("key")
        sync_pipe.pttl("key")
        assert sync_pipe.execute() == ("async-value", 500)
        now = .75
        assert sync_pipe.execute() == (None, -2)

    asyncio.run(exercise_clients())


def test_last_measured_bundle_cannot_leave_a_queued_candle_refresh(monkeypatch):
    source = bench.PriceSource(bench.MockBroker())
    origin = SimpleNamespace(counts=Counter())

    async def stale_batch(_client, _screen):
        source._queued.add(asyncio.get_running_loop().create_future())
        return 1.0

    monkeypatch.setattr(bench, "_request_bundle", stale_batch)
    with bench._isolated_app_state(), pytest.raises(RuntimeError, match="outstanding candle reads"):
        asyncio.run(bench._case(None, source, origin, {}, "market_board", False, "cold", 1, 0))


@pytest.mark.parametrize("samples,upstream_ms,redis_ms", [(0, 20, 1), (1, float("nan"), 1), (1, 20, -1)])
def test_invalid_benchmark_inputs(samples, upstream_ms, redis_ms):
    with pytest.raises(ValueError, match="samples.*delays"):
        asyncio.run(bench.benchmark(samples, upstream_ms, redis_ms))
