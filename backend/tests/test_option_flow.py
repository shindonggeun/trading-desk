"""#1007 KOSPI200 call / put options by investor class on the futures view (KIS [0403], K2I · OC01 / OP01)."""
import asyncio
from datetime import UTC, datetime

import httpx

from desk import futures_flow as ff
from tests.test_futures_flow import INVESTOR_OUT, KEY, PRICE_OUT, SECRET

# million won: a premium of ~0.05 mn a call contract, ~0.1 a put - far below a futures contract (412 x 0.25 mn)
CALL_OUT = {"output": [{"frgn_ntby_qty": "30000", "frgn_ntby_tr_pbmn": "1500", "prsn_ntby_qty": "-21000",
                        "prsn_ntby_tr_pbmn": "-900", "orgn_ntby_qty": "-9000", "orgn_ntby_tr_pbmn": "-600"}],
            "rt_cd": "0", "msg_cd": "MCA00000", "msg1": "ok"}
PUT_OUT = {"output": [{"frgn_ntby_qty": "25000", "frgn_ntby_tr_pbmn": "2400", "prsn_ntby_qty": "-15000",
                       "prsn_ntby_tr_pbmn": "-1700", "orgn_ntby_qty": "-10000", "orgn_ntby_tr_pbmn": "-700"}],
           "rt_cd": "0", "msg_cd": "MCA00000", "msg1": "ok"}
EMPTY_OUT = {"output": [], "rt_cd": "0", "msg_cd": "MCA00000", "msg1": "ok"}


def _rest(calls, answers: dict):
    """KIS by the investor screen's sector code (FID_INPUT_ISCD_2): F001 futures, OC01 calls, OP01 puts."""
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == ff.TOKEN_PATH:
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 86400})
        if request.url.path == ff.PRICE_PATH:
            return httpx.Response(200, json=PRICE_OUT)
        got = answers[request.url.params["FID_INPUT_ISCD_2"]]
        if isinstance(got, int):
            return httpx.Response(got, json={"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수 초과"})
        return httpx.Response(200, json=got)
    return ff.KisRest(KEY, SECRET, "https://kis.example", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                      clock=lambda: 100.0)


def _flow(answers, calls=None, clock=None):
    clock = clock or [0.0]
    return ff.FuturesFlow(_rest(calls if calls is not None else [], answers), None, clock=lambda: clock[0],
                          now=lambda: datetime(2026, 11, 6, 2, 0, tzinfo=UTC))       # 11:00 KST


ANSWERS = {"F001": INVESTOR_OUT, "OC01": CALL_OUT, "OP01": PUT_OUT}


def test_calls_and_puts_by_class_and_the_put_minus_call_value():
    calls: list = []
    out = asyncio.run(_flow(ANSWERS, calls).get())
    asked = sorted((r.url.params.get("FID_INPUT_ISCD"), r.url.params.get("FID_INPUT_ISCD_2"))
                   for r in calls if r.url.path == ff.INVESTOR_PATH)
    assert asked == [("K2I", "F001"), ("K2I", "OC01"), ("K2I", "OP01")]     # KIS' list: one market, three sectors
    opt = out["options"]
    assert opt["call"]["foreign"] == {"net_contracts": 30000, "net_value_mn": 1500}
    assert opt["put"]["individual"] == {"net_contracts": -15000, "net_value_mn": -1700}
    assert opt["foreign_put_minus_call_mn"] == 900.0 and opt["institution_put_minus_call_mn"] == -100.0
    assert opt["as_of"] == out["fetched_at"] and "단정" in opt["note"] and out["options_error"] is None
    # the futures part is as before
    assert out["investors"]["foreign"]["net_contracts"] == -4100 and out["error"] is None


def test_an_option_code_answering_like_futures_is_not_shown_and_the_futures_stand():
    out = asyncio.run(_flow({**ANSWERS, "OC01": INVESTOR_OUT}).get())          # the futures answer again
    assert out["options"] is None and "확인" in out["options_error"]
    assert out["investors"]["foreign"]["net_contracts"] == -4100
    # a value per contract near a futures contract's notional is not a premium either
    dear = {"output": [{"frgn_ntby_qty": "1000", "frgn_ntby_tr_pbmn": "90000"}], "rt_cd": "0"}
    assert not ff._looks_like_options(ff.normalize_investors(dear["output"]), {"price": 412.35}, None)
    # few contracts say nothing either way
    thin = {"foreign": {"net_contracts": 10, "net_value_mn": 900}}
    assert ff._looks_like_options(thin, {"price": 412.35}, None)


def test_the_same_answer_for_calls_and_puts_is_a_wrong_code():
    out = asyncio.run(_flow({**ANSWERS, "OP01": CALL_OUT}).get())
    assert out["options"] is None and "같게" in out["options_error"]


def test_a_failed_option_read_keeps_the_last_good_one_and_never_fails_the_view(monkeypatch):
    published: list = []
    monkeypatch.setattr(ff.market_bus, "publish", lambda *a, **k: published.append(a))
    clock = [0.0]
    answers = dict(ANSWERS)
    flow = _flow(answers, clock=clock)
    first = asyncio.run(flow.get())
    answers["OP01"] = 500                                                       # KIS refuses the puts
    clock[0] += 1000.0
    again = asyncio.run(flow.get(fresh=True))
    assert again["options"] == first["options"] and again["options"]["as_of"] == first["fetched_at"]
    assert "받지 못했습니다" in again["options_error"]
    assert again["error"] is None and again["investors"] is not None
    assert len(published) == 2                  # both reads were good views: shared and streamed (#689 · #690)


def test_before_the_open_empty_answers_show_nothing_and_say_nothing():
    out = asyncio.run(_flow({**ANSWERS, "OC01": EMPTY_OUT, "OP01": EMPTY_OUT}).get())
    assert out["options"] is None and out["options_error"] is None


def test_without_keys_the_options_are_absent_on_the_same_keys():
    none = asyncio.run(ff.FuturesFlow(None, None, now=lambda: datetime(2026, 11, 6, 2, 0, tzinfo=UTC)).get())
    assert none["supported"] is False and none["options"] is None and none["options_error"] is None
