import asyncio
from datetime import UTC, date, datetime
from types import SimpleNamespace

import httpx
import pytest

from desk import futures_flow as ff
from tests.test_api import _client

KEY, SECRET = "app-key-not-real", "app-secret-not-real"


def test_the_front_month_rolls_after_the_second_thursday():
    assert ff.second_thursday(2026, 12) == date(2026, 12, 10)
    assert ff.front_month(date(2026, 11, 6)) == {"code": "101X12", "expiry": "2026-12-10", "days_left": 34}
    assert ff.front_month(date(2026, 12, 10))["code"] == "101X12"           # the last trading day itself
    assert ff.front_month(date(2026, 12, 11))["code"] == "101Y03"           # next quarter, next year's letter
    assert ff.front_month(date(2025, 9, 1))["code"] == "101W09"             # KIS sample's code
    assert ff.front_month(date(2026, 3, 13))["code"] == "101X06"


PRICE_OUT = {
    "output1": {"hts_kor_isnm": "F 202612", "futs_prpr": "412.35", "futs_prdy_vrss": "3.10", "prdy_vrss_sign": "5",
                "futs_prdy_ctrt": "0.75", "acml_vol": "210000", "hts_otst_stpl_qty": "285000",
                "otst_stpl_qty_icdc": "-4200", "basis": "1.20", "mrkt_basis": "-0.85", "hts_thpr": "414.40",
                "futs_last_tr_date": "20261210", "hts_rmnn_dynu": "34"},
    "output3": {"bstp_nmix_prpr": "413.20"},
    "rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상처리 되었습니다.",
}
INVESTOR_OUT = {"output": [{"frgn_ntby_qty": "-4100", "frgn_ntby_tr_pbmn": "-845000", "prsn_ntby_qty": "2300",
                            "prsn_ntby_tr_pbmn": "474000", "orgn_ntby_qty": "1800", "orgn_ntby_tr_pbmn": "371000"}],
                "rt_cd": "0", "msg_cd": "MCA00000", "msg1": "ok"}


def test_price_and_investors_are_normalized():
    front = ff.front_month(date(2026, 11, 6))
    p = ff.normalize_price(PRICE_OUT["output1"], PRICE_OUT["output3"], front)
    assert p["price"] == 412.35 and p["change"] == -3.1 and p["change_pct"] == -0.75
    assert p["open_interest"] == 285000 and p["open_interest_change"] == -4200
    assert p["market_basis"] == -0.85 and p["structure"] == "backwardation" and p["expiry"] == "2026-12-10"
    inv = ff.normalize_investors(INVESTOR_OUT["output"])
    assert inv["foreign"] == {"net_contracts": -4100, "net_value_mn": -845000}
    assert ff._looks_like_futures(inv, p)
    # a stock-market answer (millions of shares) is not taken for futures
    assert not ff._looks_like_futures({"foreign": {"net_contracts": 9_000_000, "net_value_mn": 1}}, p)


def _transport(calls, *, token_status=200, price=PRICE_OUT, investors=INVESTOR_OUT, expire_once=False):
    state = {"expired": expire_once}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == ff.TOKEN_PATH:
            if token_status != 200:
                return httpx.Response(token_status, json={"error_description": "no"})
            return httpx.Response(200, json={"access_token": f"tok{len(calls)}", "expires_in": 86400})
        if state["expired"]:
            state["expired"] = False
            return httpx.Response(500, json={"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "기간이 만료된 token"})
        if request.url.path == ff.PRICE_PATH:
            return httpx.Response(200, json=price)
        return httpx.Response(200, json=investors)
    return httpx.MockTransport(handler)


def _rest(calls, clock=None, **kw):
    return ff.KisRest(KEY, SECRET, "https://kis.example", http=httpx.AsyncClient(transport=_transport(calls, **kw)),
                      clock=clock or (lambda: 100.0))


def test_the_token_is_cached_and_never_shown():
    calls = []
    rest = _rest(calls)

    async def go():
        await rest.get(ff.PRICE_PATH, ff.PRICE_TR, {})
        await rest.get(ff.PRICE_PATH, ff.PRICE_TR, {})
    asyncio.run(go())
    assert sum(1 for c in calls if c.url.path == ff.TOKEN_PATH) == 1
    sent = next(c for c in calls if c.url.path == ff.PRICE_PATH)
    assert sent.headers["tr_id"] == ff.PRICE_TR and sent.headers["authorization"].startswith("Bearer ")
    assert KEY not in repr(rest) and SECRET not in repr(rest)


def test_an_expired_token_is_renewed_once_and_issues_are_spaced():
    calls, now = [], [100.0]
    rest = _rest(calls, clock=lambda: now[0], expire_once=True)

    async def go():
        await rest.token()
        now[0] += 61                                 # past the one-a-minute issue gap
        return await rest.get(ff.PRICE_PATH, ff.PRICE_TR, {})
    assert asyncio.run(go())["rt_cd"] == "0"
    assert sum(1 for c in calls if c.url.path == ff.TOKEN_PATH) == 2
    # a refused token: the error carries no key, and a retry inside the minute does not ask again
    calls2 = []
    bad = _rest(calls2, token_status=403)
    with pytest.raises(ff.KisError) as err:
        asyncio.run(bad.token())
    assert KEY not in str(err.value) and SECRET not in str(err.value)
    with pytest.raises(ff.KisError, match="waiting"):
        asyncio.run(bad.token())
    assert sum(1 for c in calls2 if c.url.path == ff.TOKEN_PATH) == 1


class _Spot:
    async def get(self, market, interval, count):
        return {"records": [{"date": "2026-11-06", "investors": {"foreign": {"buy": 9, "sell": 4, "net": 320_000_000_000}}}]}


def _flow(rest, spot=None):
    return ff.FuturesFlow(rest, spot, now=lambda: datetime(2026, 11, 6, 2, 0, tzinfo=UTC))   # 11:00 KST


def test_the_flow_shows_futures_and_flags_a_foreign_split():
    out = asyncio.run(_flow(_rest([]), _Spot()).get())
    assert out["supported"] and out["error"] is None and out["front"]["code"] == "101X12"
    assert out["price"]["open_interest"] == 285000 and out["investors"]["foreign"]["net_contracts"] == -4100
    assert out["split"] == {"spot_net": 320_000_000_000, "spot_date": "2026-11-06", "futures_net_contracts": -4100,
                            "split": True, "kind": "spot_buy_futures_sell"}


def test_without_keys_or_with_a_failure_the_flow_says_why():
    none = asyncio.run(_flow(None).get())
    assert none["supported"] is False and "키" in none["error"]
    broken = asyncio.run(_flow(_rest([], price={"rt_cd": "1", "msg_cd": "OPSQ0002", "msg1": "없는 종목"})).get())
    assert broken["supported"] and broken["price"] is None and "OPSQ0002" in broken["error"]
    wrong = {"output": [{"frgn_ntby_qty": "9000000", "frgn_ntby_tr_pbmn": "1"}], "rt_cd": "0"}
    odd = asyncio.run(_flow(_rest([], investors=wrong)).get())
    assert odd["investors"] is None and "확인" in odd["investors_error"] and odd["price"]["price"] == 412.35


def test_make_needs_both_keys_and_https():
    s = SimpleNamespace(kis_app_key=KEY, kis_app_secret=SECRET, kis_rest_url="")
    assert ff.make(s) is not None
    assert ff.make(SimpleNamespace(kis_app_key="", kis_app_secret=SECRET, kis_rest_url="")) is None
    assert ff.make(SimpleNamespace(kis_app_key=KEY, kis_app_secret=SECRET, kis_rest_url="http://x")) is None


def test_api_answers_unsupported_without_keys(db_url):
    with _client(db_url) as client:
        body = client.get("/api/market/futures").json()
        assert body["supported"] is False and body["front"]["code"].startswith("101")


def test_api_needs_a_session(db_url):
    with _client(db_url, login=False) as client:
        assert client.get("/api/market/futures").status_code == 401
