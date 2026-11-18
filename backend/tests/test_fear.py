"""#462: the fear gauges - parsing, bands, and one failed source leaving the others."""

import asyncio
import json
from datetime import date, datetime, timedelta
from pathlib import Path
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


# #486 KIS FHPUP02120000 for 0503 (VKOSPI): the shape KIS documents - output1 now, output2 daily closes
KIS_BODY = {"rt_cd": "0", "msg_cd": "MCA00000", "output1": {"bstp_nmix_prpr": "24.51"},
            "output2": [{"stck_bsop_date": d, "bstp_nmix_prpr": v} for d, v in [
                ("20261117", "24.51"), ("20261116", "22.10"), ("20261113", "21.00"), ("20261112", "20.50"),
                ("20261111", "19.80"), ("20261110", "18.20"), ("20261107", "17.90")]]}


def test_vkospi_reads_kis_index_daily_price():
    v = fear.parse_vkospi(KIS_BODY)
    assert (v["value"], v["prev_day"], v["prev_week"], v["as_of"], v["band"]) == (24.51, 22.1, 18.2, "2026-11-17", "fear")
    # after the bell or on a holiday output1 may be empty: the last close stands in
    holiday = {"output1": {}, "output2": list(reversed(KIS_BODY["output2"]))}
    assert fear.parse_vkospi(holiday)["value"] == 24.51
    with pytest.raises(ValueError):
        fear.parse_vkospi({"output1": {"bstp_nmix_prpr": "0"}, "output2": []})


class FakeKis:
    def __init__(self, body=None, error=None):
        self.calls, self.body, self.error = [], body, error

    async def get(self, path, tr_id, params):
        self.calls.append((path, tr_id, params))
        if self.error:
            raise self.error
        return self.body


def test_vkospi_comes_from_kis_when_its_keys_are_there_and_stays_apart_when_it_fails():
    from desk.futures_flow import KisError

    kis = FakeKis(KIS_BODY)
    f = fear.Fear(candles=lambda s, i, n: _bars([20]), get=_get({fear.CNN_URL: CNN, fear.CRYPTO_URL: CRYPTO}),
                  kis=lambda: kis)
    items = {x["key"]: x for x in asyncio.run(f.get())["items"]}
    assert items["vkospi"]["value"] == 24.51 and "error" not in items["vkospi"]
    _path, tr, params = kis.calls[0]
    assert tr == "FHPUP02120000" and params["FID_INPUT_ISCD"] == "0503" and params["FID_COND_MRKT_DIV_CODE"] == "U"

    down = fear.Fear(candles=lambda s, i, n: _bars([20]), get=_get({fear.CNN_URL: CNN, fear.CRYPTO_URL: CRYPTO}),
                     kis=lambda: FakeKis(error=KisError("FHPUP02120000: HTTP 500 EGW00201")))
    items = {x["key"]: x for x in asyncio.run(down.get())["items"]}
    assert items["vkospi"]["value"] is None and items["vkospi"]["error"].startswith("KisError")
    assert items["cnn"]["value"] == 31.02                                 # the others are served

    none = fear.Fear(candles=lambda s, i, n: _bars([20]), get=_get({fear.CNN_URL: CNN, fear.CRYPTO_URL: CRYPTO}))
    vk = {x["key"]: x for x in asyncio.run(none.get())["items"]}["vkospi"]
    assert vk["value"] is None and "키가 없어" in vk["error"]


# #486 the estimate when KIS gives no VKOSPI: KODEX 200's realised volatility × ESTIMATE_SCALE
def _kodex(n=40, swing=0.01):
    """Closes up and down ``swing`` in turn, dated at the Seoul close (06:30 UTC)."""
    from datetime import date, timedelta

    closes, c = [], 30000.0
    for i in range(n):
        c *= (1 + swing) if i % 2 else (1 - swing)
        closes.append(c)
    return [SimpleNamespace(at=f"{date(2026, 10, 1) + timedelta(days=i)}T06:30:00+00:00", close=x)
            for i, x in enumerate(closes)]


def _expected(bars, end):
    import math
    import statistics

    c = [b.close for b in bars]
    rets = [math.log(c[i] / c[i - 1]) for i in range(end - fear.ESTIMATE_DAYS + 1, end + 1)]
    return round(statistics.stdev(rets) * math.sqrt(252) * 100 * fear.ESTIMATE_SCALE, 2)


def test_the_estimate_is_kodex_200s_realised_volatility_named_an_estimate():
    bars, asked = _kodex(), []

    def candles(symbol, interval, count):
        asked.append((symbol, interval))
        return bars

    v = fear.vkospi_estimate(candles, "왜")
    assert asked == [(fear.ESTIMATE_SYMBOL, "1d")]
    assert v["value"] == _expected(bars, 39) and v["prev_day"] == _expected(bars, 38) and v["prev_week"] == _expected(bars, 34)
    assert v["name"] == "VKOSPI(추정)" and "실현 변동성" in v["source"] and v["fallback_reason"] == "왜"
    assert v["estimated"] == ["value", "prev_day", "prev_week"] and "error" not in v
    assert v["as_of"] == "2026-11-09" and v["band"] == fear.band_of("vkospi", v["value"])
    calm = fear.vkospi_estimate(lambda s, i, n: _kodex(swing=0.002))
    assert calm["value"] < v["value"] and "fallback_reason" not in calm
    with pytest.raises(ValueError):
        fear.vkospi_estimate(lambda s, i, n: _kodex(fear.ESTIMATE_DAYS))   # 19 returns: no 20-session window
    # ±1% each day: a 20-day sample stdev of 1.026%, × √252 = 16.29% annualised, × 0.9
    assert abs(v["value"] - 14.66) < 0.01
    # Yahoo dates a Korean daily bar at midnight Seoul time, 15:00 UTC the day before
    yahoo = [SimpleNamespace(at=b.at.replace("T06:30", "T15:00"), close=b.close) for b in bars]
    assert fear.vkospi_estimate(lambda s, i, n: yahoo)["as_of"] == "2026-11-10"


def test_without_kis_or_when_it_fails_the_gauge_shows_the_estimate_and_asks_kis_again_soon():
    from desk.futures_flow import KisError

    routes = {fear.CNN_URL: CNN, fear.CRYPTO_URL: CRYPTO}
    none = fear.Fear(candles=lambda s, i, n: _kodex(), get=_get(routes))
    vk = {x["key"]: x for x in asyncio.run(none.get())["items"]}["vkospi"]
    assert vk["name"] == "VKOSPI(추정)" and vk["value"] > 0 and vk["fallback_reason"] == fear.NO_SOURCE

    now, kis = [0.0], FakeKis(error=KisError("FHPUP02120000: HTTP 500 EGW00201"))
    down = fear.Fear(candles=lambda s, i, n: _kodex(), get=_get(routes), clock=lambda: now[0], kis=lambda: kis)
    vk = {x["key"]: x for x in asyncio.run(down.get())["items"]}["vkospi"]
    assert vk["name"] == "VKOSPI(추정)" and "error" not in vk and "KisError" in vk["fallback_reason"]
    now[0] = fear.FAIL_TTL_SEC + 1
    kis.error = None
    kis.body = KIS_BODY
    vk = {x["key"]: x for x in asyncio.run(down.get())["items"]}["vkospi"]
    assert len(kis.calls) == 2 and vk["name"] == "VKOSPI" and vk["value"] == 24.51 and "estimated" not in vk
    # anything but a KisError says its class only (its text could carry anything)
    odd = fear.Fear(candles=lambda s, i, n: _kodex(), get=_get(routes),
                    kis=lambda: FakeKis(error=RuntimeError("Bearer abc")))
    vk = {x["key"]: x for x in asyncio.run(odd.get())["items"]}["vkospi"]
    assert "RuntimeError" in vk["fallback_reason"] and "abc" not in vk["fallback_reason"]


def test_without_kis_the_estimate_is_kept_as_long_as_a_kis_value_would_be():
    now, asked = [0.0], []

    def candles(symbol, interval, count):
        asked.append(symbol)
        return _kodex()

    f = fear.Fear(candles=candles, get=_get({fear.CNN_URL: CNN, fear.CRYPTO_URL: CRYPTO}), clock=lambda: now[0])
    asyncio.run(f.get())
    now[0] = fear.FAIL_TTL_SEC + 1
    asyncio.run(f.get())
    assert asked.count(fear.ESTIMATE_SYMBOL) == 1
    now[0] = fear.TTL_SEC["vkospi"] + 1
    asyncio.run(f.get())
    assert asked.count(fear.ESTIMATE_SYMBOL) == 2


def test_a_kis_answer_without_the_week_before_takes_it_from_the_estimate():
    short = {**KIS_BODY, "output2": KIS_BODY["output2"][:3]}
    f = fear.Fear(candles=lambda s, i, n: _kodex(), get=_get({fear.CNN_URL: CNN, fear.CRYPTO_URL: CRYPTO}),
                  kis=lambda: FakeKis(short))
    vk = {x["key"]: x for x in asyncio.run(f.get())["items"]}["vkospi"]
    assert (vk["value"], vk["prev_day"], vk["name"]) == (24.51, 22.1, "VKOSPI")      # KIS' own
    assert vk["prev_week"] == _expected(_kodex(), 34) and vk["estimated"] == ["prev_week"]
    assert vk["source"].startswith("한국투자증권") and "지난주 값은 추정" in vk["source"]   # the tooltip says so
    full = fear.Fear(candles=lambda s, i, n: _kodex(), get=_get({fear.CNN_URL: CNN, fear.CRYPTO_URL: CRYPTO}),
                     kis=lambda: FakeKis(KIS_BODY))
    assert "estimated" not in {x["key"]: x for x in asyncio.run(full.get())["items"]}["vkospi"]


def test_a_refetch_ahead_that_only_gets_the_estimate_keeps_kis_value_in_date():
    from desk.futures_flow import KisError

    now, kis = [0.0], FakeKis(KIS_BODY)
    f = fear.Fear(candles=lambda s, i, n: _kodex(), get=_get({fear.CNN_URL: CNN, fear.CRYPTO_URL: CRYPTO}),
                  clock=lambda: now[0], kis=lambda: kis)
    asyncio.run(f.get())
    kis.error = KisError("FHPUP02120000: HTTP 500 EGW00201")
    now[0] = fear.TTL_SEC["vkospi"] - 30                  # due within the prewarm's lead, still in date
    vk = {x["key"]: x for x in asyncio.run(f.get(ahead=60))["items"]}["vkospi"]
    assert vk["name"] == "VKOSPI" and vk["value"] == 24.51 and len(kis.calls) == 2


# #602 the VKOSPI's real history from KRX (desk.krx, krx_daily); the fixture is the hand-made one of #601
KRX_FIX = Path(__file__).parent / "fixtures" / "krx" / "drvprod_dd_trd.json"


def _hist(n, last=None, base=20.0):
    """n KRX closes on consecutive days ending ``last`` (yesterday): 20.0, 20.1, ..."""
    last = last or datetime.now(fear.KST).date() - timedelta(days=1)
    return [((last - timedelta(days=n - 1 - i)).isoformat(), round(base + i / 10, 2)) for i in range(n)]


def _fear(kis=None, history=None, candles=None, clock=None):
    def no_estimate(*a):
        raise AssertionError("the estimate is not asked while KRX has the value")
    return fear.Fear(candles=candles or no_estimate, get=_get({fear.CNN_URL: CNN, fear.CRYPTO_URL: CRYPTO}),
                     kis=kis, history=history, **({"clock": clock} if clock else {}))


def _vkospi(f, ahead=0.0):
    return {x["key"]: x for x in asyncio.run(f.get(ahead))["items"]}["vkospi"]


def test_krx_history_reads_the_vkospi_closes_from_krx_daily(db_url):
    from desk import krx
    from desk.db import make_engine, migrate

    migrate(db_url)
    engine = make_engine(db_url)
    try:
        store = krx.KrxDaily(engine, client=None)
        rows = json.loads(KRX_FIX.read_text(encoding="utf-8"))["OutBlock_1"]
        store.store(fear.KRX_API, date(2026, 11, 17), rows)
        store.store(fear.KRX_API, date(2026, 11, 16), [{**rows[1], "BAS_DD": "20261116",
                                                         "IDX_NM": "코스피200변동성지수", "CLSPRC_IDX": "1,023.10"}])
        store.store(fear.KRX_API, date(2026, 11, 13), [{**rows[1], "BAS_DD": "20261113", "CLSPRC_IDX": "-"}])
        store.store(fear.KRX_API, date(2026, 11, 12), [{**rows[1], "BAS_DD": "20261112",          # spaced apart
                                                         "IDX_NM": "코스피200 변동성 지수", "CLSPRC_IDX": "22.00"},
                                                        {**rows[1], "IDX_NM": "코스피 200 변동성지수 선물"}])
        store.store("kospi_dd_trd", date(2026, 11, 12), [{**rows[1], "BAS_DD": "20261112"}])   # another service
        assert fear.krx_history(engine, today=date(2026, 11, 18)) == [
            ("2026-11-12", 22.0), ("2026-11-16", 1023.1), ("2026-11-17", 24.18)]
        assert fear.krx_history(engine, today=date(2026, 11, 16))[-1] == ("2026-11-16", 1023.1)   # nothing ahead
        assert fear.krx_history(engine, today=date(2026, 11, 18), days=1) == [("2026-11-17", 24.18)]
    finally:
        engine.dispose()


def test_the_percentile_needs_most_of_a_year_of_closes():
    assert fear.percentile(30.0, [20.0] * (fear.PCT_MIN - 1)) is None
    assert fear.percentile(30.0, [20.0] * fear.PCT_MIN) == 100
    past = [float(i) for i in range(1, 400)]                       # only the last PCT_WINDOW - 1 count
    assert fear.percentile(past[-252], past) == round(100 * 1 / 252)
    assert fear.percentile(0.5, past) == round(100 * 1 / 252)       # itself only


def test_kis_gives_today_and_krx_the_days_it_lacks_and_the_percentile():
    short = {**KIS_BODY, "output2": KIS_BODY["output2"][:3]}       # 11-17 · 11-16 · 11-13: no week before
    history = [*_hist(260, last=date(2026, 11, 16)), ("2026-11-17", 99.0)]   # KIS' own day is not history
    vk = _vkospi(_fear(kis=lambda: FakeKis(short), history=lambda: history))
    past = [c for _, c in history[:-1]]
    assert (vk["value"], vk["prev_day"], vk["prev_week"], vk["as_of"]) == (24.51, 22.1, past[-5], "2026-11-17")
    assert vk["origin"] == {"value": "kis", "prev_day": "kis", "prev_week": "krx"} and "estimated" not in vk
    assert vk["source"].startswith("한국투자증권") and f"지난주 값은 {fear.KRX_SOURCE}" in vk["source"]
    assert vk["percentile_1y"] == fear.percentile(24.51, past) == round(100 * 38 / 252)   # 20.9 .. 24.5 and itself

    # no KRX rows yet: the week before comes from the estimate as before (#486), and no percentile
    vk = _vkospi(_fear(kis=lambda: FakeKis(short), history=list, candles=lambda s, i, n: _kodex()))
    assert vk["prev_week"] == _expected(_kodex(), 34) and vk["estimated"] == ["prev_week"]
    assert vk["origin"]["prev_week"] == "estimate" and vk["percentile_1y"] is None


def test_without_kis_krx_last_close_stands_in_before_the_estimate():
    from desk.futures_flow import KisError

    history = _hist(260)
    vk = _vkospi(_fear(history=lambda: history))
    assert (vk["name"], vk["value"], vk["as_of"]) == ("VKOSPI", history[-1][1], history[-1][0])
    assert (vk["prev_day"], vk["prev_week"]) == (history[-2][1], history[-6][1])
    assert vk["origin"] == {"value": "krx", "prev_day": "krx", "prev_week": "krx"} and "estimated" not in vk
    assert vk["percentile_1y"] == 100 and vk["source"].startswith("KRX") and vk["fallback_reason"] == fear.NO_SOURCE

    now = [0.0]
    down = _fear(kis=lambda: FakeKis(error=KisError("FHPUP02120000: HTTP 500 EGW00201")),
                 history=lambda: history, clock=lambda: now[0])
    vk = _vkospi(down)
    assert vk["origin"]["value"] == "krx" and "KisError" in vk["fallback_reason"]
    now[0] = fear.FAIL_TTL_SEC + 1                                    # KIS is asked again soon, as before
    assert _vkospi(down)["origin"]["value"] == "krx"

    # KRX stale (the collector stopped) or empty: the estimate, as before
    stale = _hist(30, last=datetime.now(fear.KST).date() - timedelta(days=fear.KRX_STALE_DAYS + 1))
    for h in (stale, []):
        vk = _vkospi(_fear(history=lambda h=h: h, candles=lambda s, i, n: _kodex()))
        assert vk["name"] == fear.ESTIMATE_NAME and vk["origin"]["value"] == "estimate"


def test_the_krx_history_is_read_once_an_hour_and_a_failed_read_leaves_the_estimate():
    now, reads = [0.0], []
    history = _hist(30)
    f = _fear(history=lambda: reads.append(1) or history, clock=lambda: now[0])
    _vkospi(f)
    now[0] = fear.TTL_SEC["vkospi"] + 1                               # the gauge refetches, the history is cached
    _vkospi(f)
    assert len(reads) == 1
    now[0] = fear.HISTORY_TTL_SEC + 1
    _vkospi(f)
    assert len(reads) == 2

    def broken():
        raise RuntimeError("db down")
    vk = _vkospi(_fear(history=broken, candles=lambda s, i, n: _kodex()))
    assert vk["name"] == fear.ESTIMATE_NAME


def test_a_refetch_ahead_that_only_gets_krx_keeps_kis_value_in_date():
    from desk.futures_flow import KisError

    now, kis = [0.0], FakeKis(KIS_BODY)
    f = _fear(kis=lambda: kis, history=lambda: _hist(30), clock=lambda: now[0])
    asyncio.run(f.get())
    kis.error = KisError("FHPUP02120000: HTTP 500 EGW00201")
    now[0] = fear.TTL_SEC["vkospi"] - 30
    vk = _vkospi(f, ahead=60)
    assert vk["origin"]["value"] == "kis" and vk["value"] == 24.51 and len(kis.calls) == 2


def test_kis_with_only_its_day_takes_both_days_before_from_krx_and_no_value_still_has_the_fields():
    from desk.futures_flow import KisError

    one = {**KIS_BODY, "output2": KIS_BODY["output2"][:1]}
    history = _hist(260, last=date(2026, 11, 16))
    vk = _vkospi(_fear(kis=lambda: FakeKis(one), history=lambda: history))
    assert (vk["prev_day"], vk["prev_week"]) == (history[-1][1], history[-5][1])
    assert vk["origin"] == {"value": "kis", "prev_day": "krx", "prev_week": "krx"}
    assert "어제 · 지난주 값은 KRX" in vk["source"]

    def no_estimate(*a):
        raise ValueError("no bars")
    for kis in (None, lambda: FakeKis(error=KisError("FHPUP02120000: HTTP 500 EGW00201"))):
        vk = _vkospi(_fear(kis=kis, history=list, candles=no_estimate))
        assert vk["value"] is None and vk["error"] and (vk["origin"], vk["percentile_1y"]) == ({}, None)


def test_a_refetch_ahead_that_fails_keeps_the_estimate_in_date():
    now, bars = [0.0], [_kodex()]

    def candles(s, i, n):
        if not bars[0]:
            raise ValueError("no bars")
        return bars[0]
    f = _fear(history=list, candles=candles, clock=lambda: now[0])
    assert _vkospi(f)["origin"]["value"] == "estimate"
    bars[0] = []
    now[0] = fear.TTL_SEC["vkospi"] - 30
    vk = _vkospi(f, ahead=60)
    assert vk["origin"]["value"] == "estimate" and "error" not in vk
