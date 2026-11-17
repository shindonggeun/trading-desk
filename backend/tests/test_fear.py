"""#462: the fear gauges - parsing, bands, and one failed source leaving the others."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from desk import fear

CNN = {"fear_and_greed": {"score": 31.02, "rating": "fear", "timestamp": "2026-11-16T23:59:54+00:00",
                          "previous_close": 38.6, "previous_1_week": 31.51, "previous_1_month": 18.2}}
CRYPTO = {"name": "Fear and Greed Index",
          "data": [{"value": str(v), "value_classification": "x", "timestamp": str(1794873600 - i * 86400)}
                   for i, v in enumerate([12, 10, 10, 15, 20, 22, 30, 41])]}


def _bars(closes):
    return [SimpleNamespace(at=f"2026-11-{i + 1:02d}T21:00:00+00:00", close=c) for i, c in enumerate(closes)]


def _get(routes, calls=None):
    def get(url, params=None, headers=None, timeout=None):
        if calls is not None:
            calls.append(url)
        body = routes[url]
        if isinstance(body, Exception):
            raise body
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))
    return get


@pytest.mark.parametrize(("key", "value", "band"), [
    ("cnn", 0, "extreme_fear"), ("cnn", 24.9, "extreme_fear"), ("cnn", 25, "fear"), ("cnn", 45, "neutral"),
    ("cnn", 55.9, "neutral"), ("cnn", 56, "greed"), ("cnn", 76, "extreme_greed"), ("crypto", 100, "extreme_greed"),
    ("vix", 35, "extreme_fear"), ("vix", 30, "fear"), ("vix", 22.9, "fear"), ("vix", 22, "neutral"),
    ("vix", 17, "greed"), ("vix", 12, "extreme_greed"), ("vix", None, None),
])
def test_bands(key, value, band):
    assert fear.band_of(key, value) == band


def test_each_source_is_parsed_with_its_day_and_week_before():
    v = fear.vix(lambda s, i, n: _bars([20, 21, 22, 23, 24, 25, 26, 27, 28, 31.5]))
    assert (v["value"], v["prev_day"], v["prev_week"], v["band"]) == (31.5, 28, 24, "extreme_fear")   # a week = five sessions back
    c = fear.parse_cnn(CNN)
    assert (c["value"], c["prev_day"], c["prev_week"], c["band"], c["band_ko"]) == (31.02, 38.6, 31.51, "fear", "공포")
    k = fear.parse_crypto(CRYPTO)
    assert (k["value"], k["prev_day"], k["prev_week"], k["band"]) == (12, 10, 41, "extreme_fear")
    assert k["as_of"].startswith("2026-11-17")
    vk = fear.vkospi()
    assert vk["value"] is None and vk["band"] is None and "error" in vk


def test_a_failed_source_is_its_items_error_and_the_rest_are_served():
    routes = {fear.CNN_URL: httpx.ConnectError("down"), fear.CRYPTO_URL: CRYPTO}
    f = fear.Fear(candles=lambda s, i, n: _bars([20, 21]), get=_get(routes))
    items = {x["key"]: x for x in asyncio.run(f.get())["items"]}
    assert list(items) == ["vix", "cnn", "vkospi", "crypto"]
    assert items["cnn"]["value"] is None and items["cnn"]["error"].startswith("ConnectError")
    assert items["vix"]["value"] == 21 and items["vix"]["prev_week"] is None and items["crypto"]["value"] == 12


def test_each_source_is_cached_on_its_own():
    now, calls = [0.0], []
    f = fear.Fear(candles=lambda s, i, n: _bars([20]), get=_get({fear.CNN_URL: CNN, fear.CRYPTO_URL: CRYPTO}, calls),
                  clock=lambda: now[0])
    asyncio.run(f.get())
    asyncio.run(f.get())
    assert calls.count(fear.CNN_URL) == 1 and calls.count(fear.CRYPTO_URL) == 1
    now[0] = fear.TTL_SEC["cnn"] + 1                # CNN (30 min) is due, crypto (1 h) is not
    asyncio.run(f.get())
    assert calls.count(fear.CNN_URL) == 2 and calls.count(fear.CRYPTO_URL) == 1


def test_api_needs_a_session(db_url):
    from tests.test_api import OWNER, PASSWORD, _client

    with _client(db_url, login=False) as client:
        assert client.get("/api/market/fear").status_code == 401
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        body = client.get("/api/market/fear").json()
        assert body["items"] == []                   # fake runs ask no outside source
