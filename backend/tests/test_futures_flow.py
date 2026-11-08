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
INVESTOR_OUT = {"output": [{"frgn_ntby_qty": "-4100", "frgn_ntby_tr_pbmn": "-422670", "prsn_ntby_qty": "2300",
                            "prsn_ntby_tr_pbmn": "237100", "orgn_ntby_qty": "1800", "orgn_ntby_tr_pbmn": "185560"}],
                "rt_cd": "0", "msg_cd": "MCA00000", "msg1": "ok"}


def test_price_and_investors_are_normalized():
    front = ff.front_month(date(2026, 11, 6))
    p = ff.normalize_price(PRICE_OUT["output1"], PRICE_OUT["output3"], front)
    assert p["price"] == 412.35 and p["change"] == -3.1 and p["change_pct"] == -0.75
    assert p["open_interest"] == 285000 and p["open_interest_change"] == -4200
    assert p["market_basis"] == -0.85 and p["structure"] == "backwardation" and p["expiry"] == "2026-12-10"
    inv = ff.normalize_investors(INVESTOR_OUT["output"])
    assert inv["foreign"] == {"net_contracts": -4100, "net_value_mn": -422670}
    assert ff._looks_like_futures(inv, p)
    # a stock-market answer (millions of shares) is not taken for futures
    assert not ff._looks_like_futures({"foreign": {"net_contracts": 9_000_000, "net_value_mn": 1}}, p)
    # before the open the volume is 0: the value per contract still tells shares from contracts
    shares = {"foreign": {"net_contracts": 1_200_000, "net_value_mn": 84_000}}          # ~70,000 won a share
    mini = {"foreign": {"net_contracts": 4000, "net_value_mn": -4000 * 412.35 * 0.05}}   # mini futures
    assert not ff._looks_like_futures(shares, {**p, "volume": 0}) and not ff._looks_like_futures(mini, p)
    assert ff._looks_like_futures(inv, {**p, "volume": 0})


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
                            "split": True, "kind": "spot_buy_futures_sell", "reason": None}


def test_a_split_needs_the_same_day_and_real_size():
    inv = {"foreign": {"net_contracts": -4100, "net_value_mn": -422670}}
    spot = lambda d, n: {"records": [{"date": d, "investors": {"foreign": {"net": n}}}]}
    today = date(2026, 11, 6)
    assert ff.foreign_split(spot("2026-11-05", 3.2e11), inv, today)["reason"] == "spot_not_today"
    assert ff.foreign_split(spot("2026-11-06", 1e8), inv, today)["reason"] == "small"
    assert ff.foreign_split(spot("2026-11-06", -3.2e11), inv, today)["split"] is False   # both selling


def test_without_keys_or_with_a_failure_the_flow_says_why():
    none = asyncio.run(_flow(None).get())
    assert none["supported"] is False and "키" in none["error"]
    assert set(none) == set(asyncio.run(_flow(_rest([])).get()))          # the same keys on every path
    broken = asyncio.run(_flow(_rest([], price={"rt_cd": "1", "msg_cd": "OPSQ0002", "msg1": "없는 종목"})).get())
    assert broken["supported"] and broken["price"] is None and "OPSQ0002" in broken["error"]
    wrong = {"output": [{"frgn_ntby_qty": "9000000", "frgn_ntby_tr_pbmn": "1"}], "rt_cd": "0"}
    odd = asyncio.run(_flow(_rest([], investors=wrong)).get())
    assert odd["investors"] is None and "확인" in odd["investors_error"] and odd["price"]["price"] == 412.35


def test_an_empty_quote_or_a_failed_part_is_retried_soon():
    clock = [0.0]
    empty = {"output1": {}, "rt_cd": "0"}
    flow = ff.FuturesFlow(_rest([], price=empty), None, clock=lambda: clock[0],
                          now=lambda: datetime(2026, 11, 6, 9, 0, tzinfo=UTC))       # 18:00 KST, closed
    out = asyncio.run(flow.get())
    assert out["price"] is None and "101X12" in out["error"]
    assert flow._cache[1] == ff.CACHE_FAIL_SEC
    part = ff.FuturesFlow(_rest([], investors={"rt_cd": "1", "msg_cd": "EGW00201"}), None, clock=lambda: 0.0,
                          now=lambda: datetime(2026, 11, 6, 9, 0, tzinfo=UTC))
    got = asyncio.run(part.get())
    assert got["price"]["price"] == 412.35 and got["investors_error"] and part._cache[1] == ff.CACHE_FAIL_SEC


def test_the_derivatives_session_and_off_reasons():
    assert ff.futures_open(datetime(2026, 11, 5, 23, 50, tzinfo=UTC))          # 08:50 KST
    assert not ff.futures_open(datetime(2026, 11, 6, 6, 50, tzinfo=UTC))       # 15:50 KST
    s = SimpleNamespace(fake_runs=False, kis_app_key=KEY, kis_app_secret=SECRET, kis_rest_url="http://x")
    assert "https" in ff.off_reason(s)
    assert "가짜" in ff.off_reason(SimpleNamespace(fake_runs=True, kis_app_key="", kis_app_secret="", kis_rest_url=""))
    assert ff.off_reason(SimpleNamespace(fake_runs=False, kis_app_key=KEY, kis_app_secret=SECRET, kis_rest_url="")) is None


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


def test_kis_messages_stay_in_the_log_and_never_carry_the_key():
    leak = {"rt_cd": "1", "msg_cd": "EGW00105", "msg1": f"유효하지 않은 AppKey {KEY}"}
    out = asyncio.run(_flow(_rest([], price=leak)).get())
    assert "EGW00105" in out["error"] and KEY not in out["error"] and "AppKey" not in out["error"]


def test_refused_issues_back_off():
    calls, now = [], [100.0]
    rest = _rest(calls, clock=lambda: now[0], token_status=403)
    issued = lambda: sum(1 for c in calls if c.url.path == ff.TOKEN_PATH)

    def ask(after):
        now[0] += after
        with pytest.raises(ff.KisError) as err:
            asyncio.run(rest.token())
        return str(err.value)

    assert "HTTP 403" in ask(0) and issued() == 1
    assert "waiting" in ask(61) and issued() == 1            # refused once: ten minutes, not one
    assert "HTTP 403" in ask(540) and issued() == 2          # 601 s after the first ask
    assert "waiting" in ask(900) and issued() == 2           # refused twice: thirty minutes
    assert "HTTP 403" in ask(901) and issued() == 3


# --- #160: the note for the agents of a Korean run -------------------------------------------------


def test_the_note_reads_price_basis_investors_and_a_split():
    view = asyncio.run(_flow(_rest([]), _Spot()).get())
    note = ff.prompt_note(view)
    assert note.startswith("KOSPI200 futures, front month 101X12") and "open interest 285,000" in note
    assert "foreign -4,100" in note and "foreigners BUY stocks but SELL futures" in note and "+320 bn KRW" in note
    assert "session closed" not in note                           # 11:00 KST: the session is open
    # investors that did not read as futures are not shown; no quote = no note
    assert "Net futures contracts" not in ff.prompt_note({**view, "investors": None, "split": None})
    assert ff.prompt_note({**view, "price": {**view["price"], "price": None}}) is None
    assert ff.prompt_note(asyncio.run(_flow(None).get())) is None and ff.prompt_note(None) is None
    closed = ff.prompt_note({**view, "market_open": False})
    assert "last session" in closed and len(note) < 700


class _StubFutures:
    def __init__(self, view, delay=0.0):
        self.view, self.delay, self.calls = view, delay, 0

    async def get(self):
        self.calls += 1
        await asyncio.sleep(self.delay)
        return self.view

    async def aclose(self):
        pass


def _korean_run(db_url, monkeypatch, futures, ticker="005930.KS", first=True):
    from datetime import date as d
    from datetime import timedelta as td

    from fastapi.testclient import TestClient

    from desk import api
    from desk.auth import Auth
    from desk.db import make_engine
    from desk.runner import make_fake_runner
    from desk.settings import Settings
    from tests.test_api import OWNER, PASSWORD, _wait_done

    monkeypatch.setattr(api, "collect_related", lambda ticker, day: None)
    seen = []
    fake = make_fake_runner(0)

    def graph(request, emit, portfolio):
        seen.append(portfolio.render(request.ticker) if portfolio else "")
        return fake(request, emit, portfolio)

    client = TestClient(api.create_app(Settings(database_url=db_url, fake_delay=0), runner=graph, futures=futures),
                        base_url="https://testserver")
    if first:
        engine = make_engine(db_url)
        Auth(engine, td(hours=1), td(hours=1)).create_user(OWNER, PASSWORD)
        engine.dispose()
    with client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        run = client.post("/api/runs", json={"ticker": ticker, "trade_date": d.today().isoformat()}).json()
        done = _wait_done(client, run["id"])
    return done, seen[0]


def test_a_korean_same_day_run_hands_the_futures_note_to_the_agents(db_url, monkeypatch):
    view = asyncio.run(_flow(_rest([]), _Spot()).get())
    stub = _StubFutures(view)
    done, note = _korean_run(db_url, monkeypatch, stub)
    assert done["status"] == "done" and "KOSPI200 futures, front month" in note and stub.calls == 1
    us = _StubFutures(view)
    done, note = _korean_run(db_url, monkeypatch, us, ticker="NVDA", first=False)
    assert done["status"] == "done" and "KOSPI200" not in note and us.calls == 0     # a US ticker never asks


def test_a_slow_kis_does_not_hold_the_run(db_url, monkeypatch):
    from desk import runner

    monkeypatch.setattr(runner, "NOTES_WAIT_SEC", {})
    monkeypatch.setattr(runner, "NOTES_WAIT_DEFAULT", 0.05)
    view = asyncio.run(_flow(_rest([]), _Spot()).get())
    done, note = _korean_run(db_url, monkeypatch, _StubFutures(view, delay=0.5))
    assert done["status"] == "done" and "KOSPI200" not in note
    assert "futures" in done["plan"]["timing"]["late_notes"]
