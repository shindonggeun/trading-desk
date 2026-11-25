"""#689 the broker-call counts in /api/status (desk.outbound): the totals as before, and what each was for."""

import asyncio
from datetime import date

import httpx

from desk import outbound
from desk.broker import toss as toss_mod
from tests.test_futures_flow import _rest
from tests.test_prices import _toss


def _kinds_add_up() -> None:
    totals, kinds = outbound.report()
    assert {name: sum(k.values()) for name, k in kinds.items()} == {k: v for k, v in totals.items() if k != "since"}


def test_the_totals_stay_and_the_kinds_add_up_to_them():
    outbound.reset()
    outbound.count("toss", "holdings")
    outbound.count("toss", "holdings")
    outbound.count("toss", "quote")
    outbound.count("toss")                       # a caller that names no kind still counts in the total
    outbound.count("kis", "FHKIF03020100")
    snap = outbound.snapshot()
    assert snap == {"since": snap["since"], "toss": 4, "kis": 1} and snap["since"].endswith("+00:00")
    assert outbound.by_kind() == {"toss": {"holdings": 2, "quote": 1, "other": 1}, "kis": {"FHKIF03020100": 1}}
    _kinds_add_up()
    outbound.reset()
    assert outbound.by_kind() == {} and "toss" not in outbound.snapshot()


def test_only_the_known_labels_are_kept_anything_else_is_other():
    outbound.reset()
    for label in ("NVDA", "KOSPI", "005930.KS", "005930", "candles_5m", "NVDA price", "", "계좌", None):
        outbound.count("toss", label)
    for label in ("NVDA", "KOSPI", "005930", "holdings", "FHKIF0302", "fhkif03020100", "FHKIF03020100X"):
        outbound.count("kis", label)
    assert outbound.by_kind() == {"toss": {"other": 9}, "kis": {"other": 7}}
    _kinds_add_up()


def test_every_toss_path_label_is_a_known_label():
    assert set(toss_mod._KINDS.values()) | {"investor_flow", "market_calendar", "candles_1m"} <= outbound.TOSS_KINDS
    assert toss_mod._kind("/api/v1/market-indicators/KOSPI/investor-trading", None) == "investor_flow"
    assert toss_mod._kind("/api/v1/candles", {"symbol": "005930", "interval": "1m"}) == "candles_1m"
    assert toss_mod._kind("/api/v1/unknown/NVDA", {"symbol": "NVDA"}) == "other"


def test_toss_calls_are_labelled_by_endpoint_never_by_ticker_or_market():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/exchange-rate":
            return httpx.Response(200, json={"result": {"midRate": "1375"}})
        if path == "/api/v1/candles":
            return httpx.Response(200, json={"result": {"candles": [
                {"timestamp": "2026-11-13T10:00:00+09:00", "openPrice": "1", "highPrice": "2", "lowPrice": "1",
                 "closePrice": "2", "volume": "3"}]}})
        if path == "/api/v1/market-calendar/KR":
            return httpx.Response(200, json={"result": {"days": []}})
        if path == "/api/v1/market-indicators/KOSPI/investor-trading":
            return httpx.Response(200, json={"result": {"items": []}})
        return httpx.Response(200, json={"result": [{"symbol": "005930", "lastPrice": "75000", "currency": "KRW"}]})

    toss = _toss(handler)
    outbound.reset()

    async def go():
        await toss.quote("005930")
        await toss.candles("005930", "1m", 30)
        await toss.candles("005930", "1d", 30)
        await toss.usd_krw()
        assert await toss.market_calendar("KR", date(2026, 11, 13)) == []
        assert await toss.investor_flow("KOSPI", "day", 5) == ([], None)
    asyncio.run(go())
    kinds = outbound.by_kind()["toss"]
    assert kinds == {"quote": 1, "candles_1m": 1, "candles": 1, "usd_krw": 1, "market_calendar": 1,
                     "investor_flow": 1}
    _kinds_add_up()
    assert not any(word in label for label in kinds for word in ("005930", "KR", "KOSPI"))


def test_kis_calls_are_labelled_by_tr_id():
    from desk import fear, futures_flow, futures_quote, kr_short_sales, program_trade

    for tr in (futures_flow.PRICE_TR, futures_flow.INVESTOR_TR, futures_quote.CANDLE_TR, fear.KIS_INDEX_DAILY_TR,
               kr_short_sales.KIS_SHORT_TR, program_trade.TR):    # every TR id the desk sends
        assert outbound._label("kis", tr) == tr
    outbound.reset()

    async def go():
        rest = _rest([])
        await rest.get("/x", "FHKIF03020100", {"FID_INPUT_ISCD": "101S12"})
        await rest.get("/x", "FHKIF03020100", {})
    asyncio.run(go())
    assert outbound.snapshot()["kis"] == 2 and outbound.by_kind()["kis"] == {"FHKIF03020100": 2}
