"""#689 the broker-call counts in /api/status (desk.outbound): the totals as before, and what each was for."""

import asyncio
from datetime import date

import httpx

from desk import outbound
from tests.test_futures_flow import _rest
from tests.test_prices import _toss


def _kinds_add_up(snap: dict) -> None:
    for name, kinds in snap["by_kind"].items():
        assert sum(kinds.values()) == snap[name], name


def test_the_totals_stay_and_the_kinds_add_up_to_them():
    outbound.reset()
    outbound.count("toss", "holdings")
    outbound.count("toss", "holdings")
    outbound.count("toss", "quote")
    outbound.count("toss")                       # a caller that names no kind still counts in the total
    outbound.count("kis", "FHKIF03020100")
    snap = outbound.snapshot()
    assert snap["toss"] == 4 and snap["kis"] == 1 and snap["since"].endswith("+00:00")
    assert snap["by_kind"] == {"toss": {"holdings": 2, "quote": 1, "other": 1}, "kis": {"FHKIF03020100": 1}}
    _kinds_add_up(snap)
    outbound.reset()
    assert outbound.snapshot()["by_kind"] == {} and "toss" not in outbound.snapshot()


def test_a_label_that_is_not_a_code_constant_counts_as_other():
    outbound.reset()
    for label in ("005930.KS", "NVDA price", "x" * 40, "", "계좌"):
        outbound.count("toss", label)
    snap = outbound.snapshot()
    assert snap["by_kind"] == {"toss": {"other": 5}} and snap["toss"] == 5


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
    snap = outbound.snapshot()
    kinds = snap["by_kind"]["toss"]
    assert kinds == {"quote": 1, "candles_1m": 1, "candles": 1, "usd_krw": 1, "market_calendar": 1,
                     "investor_flow": 1}
    _kinds_add_up(snap)
    assert not any(word in label for label in kinds for word in ("005930", "KR", "KOSPI"))


def test_kis_calls_are_labelled_by_tr_id():
    outbound.reset()

    async def go():
        rest = _rest([])
        await rest.get("/x", "FHKIF03020100", {"FID_INPUT_ISCD": "101S12"})
        await rest.get("/x", "FHKIF03020100", {})
    asyncio.run(go())
    snap = outbound.snapshot()
    assert snap["kis"] == 2 and snap["by_kind"]["kis"] == {"FHKIF03020100": 2}
