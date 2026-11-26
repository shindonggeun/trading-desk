"""#916 KIS per-stock foreign trading and ownership for the Korean flow board (desk.kis_stock_flow): amounts in won
only when their unit checks out, rows final from 20:10 KST, holdings dated to the previous close and read as
ownership only without a foreign limit, one fetch per stock and session, and a bounded wait per request."""

import asyncio
import logging
import time
from datetime import date, datetime
from itertools import pairwise

import fakeredis
import pytest

from desk import kis_stock_flow as ksf
from desk import kr_flow_board as board
from desk import shared_cache as sc
from desk.futures_flow import KisError
from desk.market_calendar import KST

SECRET = "test-cache-key-0123456789abcdef"
WED_BEFORE, THU, FRI = date(2026, 11, 18), date(2026, 11, 19), date(2026, 11, 20)
SUN, MON, TUE, WED = date(2026, 11, 22), date(2026, 11, 23), date(2026, 11, 24), date(2026, 11, 25)
SEC, HYNIX = "005930", "000660"
TRS = {ksf.INVESTOR_TR, ksf.PRICE_TR, ksf.DAILY_TR}


def kst(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=KST)


def investor_row(day, *, low=59_000, high=61_000, volume=1_000_000, turnover=60_000_000_000,
                 buy_vol=200_000, buy=12_000_000_000, sell_vol=150_000, sell=9_000_000_000):
    return {"stck_bsop_date": day.strftime("%Y%m%d"), "stck_clpr": "60000", "stck_hgpr": str(high),
            "stck_lwpr": str(low), "acml_vol": str(volume), "acml_tr_pbmn": str(turnover),
            "frgn_shnu_vol": str(buy_vol), "frgn_shnu_tr_pbmn": str(buy),
            "frgn_seln_vol": str(sell_vol), "frgn_seln_tr_pbmn": str(sell), "frgn_ntby_tr_pbmn": str(buy - sell)}


def investor(*rows):
    return {"rt_cd": "0", "output1": {}, "output2": list(rows)}


def price(held=500_000_000, listed=1_000_000_000, exhaustion="50.00"):
    return {"rt_cd": "0", "output": {"frgn_hldn_qty": str(held), "lstn_stcn": str(listed),
                                     "hts_frgn_ehrt": exhaustion}}


def daily(*rows):
    return {"rt_cd": "0", "output": [{"stck_bsop_date": day.strftime("%Y%m%d"), "hts_frgn_ehrt": pct}
                                     for day, pct in rows]}


class FakeKis:
    """KIS answers per TR (``per_code`` overrides); records each call and the most calls in flight at once."""

    def __init__(self):
        self.answers = {ksf.INVESTOR_TR: investor(investor_row(TUE), investor_row(MON)),
                        ksf.PRICE_TR: price(),
                        ksf.DAILY_TR: daily((TUE, "50.00"), (MON, "49.50"), (FRI, "49.00"))}
        self.per_code = {}
        self.calls = []
        self.gates = {}
        self.active = self.peak = 0

    async def get(self, path, tr_id, params):
        code = params["FID_INPUT_ISCD"]
        self.calls.append((path, tr_id, dict(params)))
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0)
            if code in self.gates:
                await self.gates[code].wait()
            answer = self.per_code.get((tr_id, code), self.answers[tr_id])
            if isinstance(answer, Exception):
                raise answer
            return answer
        finally:
            self.active -= 1


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class Wall:
    def __init__(self, at: datetime):
        self.at = at

    def __call__(self) -> datetime:
        return self.at


def service(kis, at: datetime, clock=None) -> ksf.KisStockFlow:
    return ksf.KisStockFlow(kis, clock=clock or Clock(), now=Wall(at) if isinstance(at, datetime) else at,
                            min_gap_sec=0)


def collect(kis, at: datetime, codes=(SEC,), market="kospi", session=TUE):
    return asyncio.run(service(kis, at).collect(list(codes), market, session))


def view(rows, at: datetime, codes=(SEC,), days=2) -> dict:
    turnover, flows, owned = rows
    publication = board.Publication(turnover=turnover, foreign_flow=flows, ownership=owned)
    return board.build_board("kospi", board.sessions(at, days), publication=publication, now=at,
                             favorites=tuple({"ticker": code + ".KS"} for code in codes))


def test_won_and_million_won_answers_both_normalize_to_won_each_amount_on_its_own():
    expected = [{"date": "2026-11-24", "turnover": 60_000_000_000, "buy": 12_000_000_000, "sell": 9_000_000_000}]
    assert ksf.parse_trades(investor(investor_row(TUE)), SEC, TUE) == expected
    million = investor_row(TUE, turnover=60_000, buy=12_000, sell=9_000)
    assert ksf.parse_trades(investor(million), SEC, TUE) == expected
    mixed = investor_row(TUE, turnover=60_000, buy=12_000_000_000, sell=9_000_000)   # million · won · thousand
    assert ksf.parse_trades(investor(mixed), SEC, TUE) == expected


def test_an_amount_no_single_scale_explains_is_dropped_not_guessed(caplog):
    caplog.set_level(logging.WARNING, logger=ksf.__name__)
    row = investor_row(TUE, turnover=6_000_000, buy=1_200)   # averages of 6 / 6,000 / 6,000,000 won a share
    assert ksf.parse_trades(investor(row), SEC, TUE) == [
        {"date": "2026-11-24", "turnover": None, "buy": None, "sell": 9_000_000_000}]
    assert [r.getMessage() for r in caplog.records] == [(
        "KIS stock flow 005930: 2 values without a fitting unit, dropped "
        "(2026-11-24 acml_tr_pbmn, 2026-11-24 frgn_shnu_tr_pbmn)")]
    # Two scales fitting one range (a range wider than 800x) is ambiguous: no value either
    assert ksf.won(5_000, 1_000, 1, 10_000) is None
    # After-hours single-price trades may sit 10% off the regular range, no further
    assert ksf.won(63_000 * 10, 10, 55_000, 58_000) == 630_000
    assert ksf.won(64_000 * 10, 10, 55_000, 58_000) is None
    assert ksf.won(600_000, 10, None, 61_000) is None


def test_zero_value_on_zero_volume_is_zero_but_a_value_without_volume_is_missing():
    assert ksf.won(0, 0, 59_000, 61_000) == 0
    assert ksf.won(0, 0, 0, 0) == 0                      # a halted day
    assert ksf.won(0, 5, 59_000, 61_000) is None
    assert ksf.won(300_000, 0, 59_000, 61_000) is None
    assert ksf.won(None, 5, 59_000, 61_000) is None
    at = kst(TUE, 21)
    kis = FakeKis()
    kis.answers[ksf.INVESTOR_TR] = investor(investor_row(TUE, buy_vol=0, buy=0, sell_vol=0, sell=0),
                                            investor_row(MON, buy_vol=10, buy=0))
    history = view(collect(kis, at), at)["favorites"]["items"][0]["history"]
    assert history[-1]["foreign_flow"]["buy_krw"] == history[-1]["foreign_flow"]["sell_krw"] == 0
    assert history[-1]["participation_pct"] == 0
    assert history[-1]["imbalance_status"] == "zero_denominator"
    assert history[0]["foreign_flow"]["status"] == "partial"     # Monday's buy: value 0 on 10 shares
    assert history[0]["foreign_flow"]["buy_krw"] is None


@pytest.mark.parametrize("at, final", [(kst(TUE, 20, 9), False), (kst(TUE, 20, 10), True), (kst(WED, 8), True)])
def test_a_row_is_published_only_when_fetched_from_20_10_kst_that_day(at, final):
    history = view(collect(FakeKis(), at), at)["favorites"]["items"][0]
    latest, monday = history["history"][-1], history["history"][0]
    assert monday["foreign_flow"]["status"] == "available"        # Monday's row was fetched on Tuesday evening
    if not final:
        assert latest["turnover"]["status"] == latest["foreign_flow"]["status"] == "not_published"
        assert latest["participation_pct"] is None and latest["participation_status"] == "not_published"
        return
    assert latest["turnover"] | {"status": "available", "source": "kis", "venue": "krx", "unit": "KRW",
                                 "value_krw": 60_000_000_000} == latest["turnover"]
    assert (latest["foreign_flow"]["buy_krw"], latest["foreign_flow"]["sell_krw"]) == (12_000_000_000, 9_000_000_000)
    assert latest["participation_pct"] == pytest.approx((12 + 9) / (2 * 60) * 100)     # 17.5%
    assert latest["buy_sell_imbalance"] == pytest.approx((12 - 9) / (12 + 9))
    assert latest["participation_status"] == latest["imbalance_status"] == "available"


def test_rows_for_closed_days_and_later_sessions_are_skipped():
    trades = ksf.parse_trades(investor(investor_row(WED), investor_row(TUE), investor_row(SUN), investor_row(FRI)),
                              SEC, TUE)
    assert [row["date"] for row in trades] == ["2026-11-24", "2026-11-20"]
    exhaustion = ksf.parse_exhaustion(daily((TUE, "1.00"), (SUN, "2.00"), (FRI, "3.00"), (MON, "abc")))
    assert exhaustion == [{"date": "2026-11-24", "pct": 1.0}, {"date": "2026-11-20", "pct": 3.0},
                          {"date": "2026-11-23", "pct": None}]
    with pytest.raises(TypeError):
        ksf.parse_trades({"rt_cd": "0"}, SEC, TUE)


def test_ownership_is_holding_over_listing_and_the_history_counts_only_without_a_limit():
    at = kst(TUE, 21)        # holdings read on Tuesday are Monday's close
    _, _, owned = collect(FakeKis(), at)
    assert [(o.as_of, o.ratio_pct, o.denominator, o.source) for o in owned] == [
        (MON, 50.0, "listed_shares", "kis"),          # the snapshot; Tuesday's daily row (also Monday's) is dropped
        (FRI, 49.5, "listed_shares", "kis"), (THU, 49.0, "listed_shares", "kis")]
    item = view((None, None, owned), at, days=3)["favorites"]["items"][0]
    assert [p["ownership"]["ratio_pct"] for p in item["history"]] == [49.5, 50.0, None]
    assert item["latest_ownership"] | {"as_of": "2026-11-23", "ratio_pct": 50.0} == item["latest_ownership"]

    limited = FakeKis()        # a 30% holding of a 50% limit shows a 60% exhaustion rate: the rates are not ratios
    limited.answers[ksf.PRICE_TR] = price(held=300_000_000, exhaustion="60.00")
    _, _, owned = collect(limited, at)
    item = view((None, None, owned), at, days=3)["favorites"]["items"][0]
    assert [p["ownership"]["status"] for p in item["history"]] == ["no_data", "available", "no_data"]
    assert item["history"][1]["ownership"]["ratio_pct"] == 30.0
    assert item["latest_ownership"]["ratio_pct"] == 30.0

    for held, listed in ((2, 1), (1, 0)):          # more held than listed, or no listing: no snapshot, no basis
        broken = FakeKis()
        broken.answers[ksf.PRICE_TR] = price(held=held, listed=listed)
        _, _, owned = collect(broken, at)
        assert {(o.as_of, o.denominator) for o in owned} == {(FRI, None), (THU, None), (MON, None)}
        item = view((None, None, owned), at, days=3)["favorites"]["items"][0]
        assert item["latest_ownership"]["status"] == "no_data"


@pytest.mark.parametrize("at, session, as_of", [
    (kst(TUE, 10), TUE, MON),
    (kst(TUE, 8, 59), MON, FRI),                                          # before the open: yesterday's session
    (kst(date(2026, 11, 28), 12), date(2026, 11, 27), date(2026, 11, 26)),  # Saturday
    (kst(date(2026, 11, 30), 8), date(2026, 11, 27), date(2026, 11, 26)),   # Monday before the open
    (kst(date(2026, 11, 30), 9), date(2026, 11, 30), date(2026, 11, 27)),   # Monday from the open
    (kst(THU, 9, 30), WED_BEFORE, date(2026, 11, 17)),                     # the CSAT day opens at 10:00
])
def test_holdings_read_during_a_session_are_dated_to_the_previous_close(at, session, as_of):
    assert ksf.started_session(at) == session
    assert ksf.ownership_as_of(session) == as_of
    assert ksf.parse_holding(price(), at)["session"] == session.isoformat()


def test_one_failing_call_leaves_only_its_own_series_missing():
    at = kst(TUE, 21)
    kis = FakeKis()
    kis.answers[ksf.INVESTOR_TR] = KisError("FHPTJ04160001: HTTP 500 EGW00201")
    turnover, flows, owned = collect(kis, at)
    assert turnover is None and flows is None and len(owned) == 3
    assert {tr for _, tr, _ in kis.calls} == TRS and len(kis.calls) == 3
    sources = view((None, flows, owned), at)["sources"]
    assert sources["foreign_flow"]["status"] == "source_unavailable"
    assert sources["ownership"]["status"] == "available"

    kis = FakeKis()
    kis.answers[ksf.PRICE_TR] = {"rt_cd": "0"}               # an answer without its output: that series only
    turnover, flows, owned = collect(kis, at)
    assert len(flows) == 2 and {(o.as_of, o.denominator) for o in owned} == {(MON, None), (FRI, None), (THU, None)}


def test_the_calls_ask_krx_for_the_latest_session_whatever_the_market():
    kis = FakeKis()
    turnover, flows, owned = collect(kis, kst(TUE, 21), codes=("247540",), market="kosdaq")
    assert {row.ticker for row in [*turnover, *flows, *owned]} == {"247540.KQ"}
    assert {row.market for row in [*turnover, *flows, *owned]} == {"kosdaq"}
    asked = {tr: (path, params) for path, tr, params in kis.calls}
    assert asked[ksf.INVESTOR_TR] == (ksf.INVESTOR_PATH, {
        "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "247540", "FID_INPUT_DATE_1": "20261124",
        "FID_ORG_ADJ_PRC": "", "FID_ETC_CLS_CODE": ""})
    assert asked[ksf.PRICE_TR] == (ksf.PRICE_PATH, {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "247540"})
    assert asked[ksf.DAILY_TR] == (ksf.DAILY_PATH, {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "247540",
                                                    "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "1"})
    assert {row.trade_scope for row in [*turnover, *flows]} == {"all_sessions"}


def test_concurrent_requests_share_one_fetch_per_stock_and_session():
    async def run():
        kis = FakeKis()
        flow = service(kis, kst(TUE, 21))
        first, second = await asyncio.gather(flow.collect([SEC], "kospi", TUE), flow.collect([SEC], "kospi", TUE))
        assert first == second and first[1]
        assert len(kis.calls) == 3
    asyncio.run(run())


def test_at_most_two_kis_calls_run_at_once():
    async def run():
        kis = FakeKis()
        codes = ["005930", "000660", "035420", "005380", "051910"]
        _, flows, _ = await service(kis, kst(TUE, 21)).collect(codes, "kospi", TUE)
        assert len(kis.calls) == 15 and kis.peak == ksf.KIS_PARALLEL == 2
        assert {row.ticker[:6] for row in flows} == set(codes)
    asyncio.run(run())


def test_kis_calls_are_spaced_by_the_minimum_gap():
    async def run():
        kis, starts = FakeKis(), []
        plain = kis.get

        async def timed(path, tr_id, params):
            starts.append(time.monotonic())
            return await plain(path, tr_id, params)

        kis.get = timed
        flow = ksf.KisStockFlow(kis, clock=Clock(), now=Wall(kst(TUE, 21)), min_gap_sec=0.05)
        await flow.collect(["005930", "000660"], "kospi", TUE, budget_sec=5)
        gaps = [later - earlier for earlier, later in pairwise(starts)]
        assert len(starts) == 6 and min(gaps) >= 0.04
    asyncio.run(run())


async def settle(flow: ksf.KisStockFlow) -> None:
    """Let the fetches running behind the requests finish."""
    await asyncio.gather(*flow._flights.values(), return_exceptions=True)


def test_answers_are_cached_by_finality_and_a_failure_is_retried_after_a_minute():
    async def run():
        clock = Clock()
        kis = FakeKis()
        flow = service(kis, kst(TUE, 21), clock)
        await flow.collect([SEC], "kospi", TUE)
        clock.t += ksf.FINAL_TTL - 1
        await flow.collect([SEC], "kospi", TUE)
        assert len(kis.calls) == 3                       # a final day: six hours from memory
        clock.t += 2
        await flow.collect([SEC], "kospi", TUE)
        await settle(flow)                               # the stale answer is served; its refresh runs behind
        assert len(kis.calls) == 6

        pending = FakeKis()
        flow = service(pending, kst(TUE, 20), clock)
        await flow.collect([SEC], "kospi", TUE)
        clock.t += ksf.PENDING_TTL - 1
        await flow.collect([SEC], "kospi", TUE)
        assert len(pending.calls) == 3                   # not final yet: ten minutes
        clock.t += 2
        await flow.collect([SEC], "kospi", TUE)
        await settle(flow)                               # the stale answer is served; its refresh runs behind
        assert len(pending.calls) == 6

        morning = FakeKis()                              # final, but the holdings move on at 09:00
        flow = service(morning, kst(WED, 8, 50), clock)
        await flow.collect([SEC], "kospi", TUE)
        clock.t += 599
        await flow.collect([SEC], "kospi", TUE)
        assert len(morning.calls) == 3
        clock.t += 2
        await flow.collect([SEC], "kospi", TUE)
        await settle(flow)                               # the stale answer is served; its refresh runs behind
        assert len(morning.calls) == 6

        broken = FakeKis()
        broken.answers = {tr: KisError(f"{tr}: HTTP 500 EGW00201") for tr in TRS}
        flow = service(broken, kst(TUE, 21), clock)
        assert await flow.collect([SEC], "kospi", TUE) == (None, None, None)
        clock.t += ksf.FAILURE_TTL - 1
        await flow.collect([SEC], "kospi", TUE)
        assert len(broken.calls) == 3                    # a failure: one minute
        clock.t += 2
        await flow.collect([SEC], "kospi", TUE)
        await settle(flow)                               # the stale answer is served; its refresh runs behind
        assert len(broken.calls) == 6
    asyncio.run(run())


def test_a_failed_refresh_keeps_the_last_rows_with_their_own_fetch_time():
    async def run():
        clock, wall = Clock(), Wall(kst(TUE, 20))
        kis = FakeKis()
        flow = service(kis, wall, clock)
        _, flows, _ = await flow.collect([SEC], "kospi", TUE)
        assert {row.as_of: row.confirmed for row in flows} == {TUE: False, MON: True}
        clock.t += ksf.PENDING_TTL + 1
        wall.at = kst(TUE, 20, 15)
        kis.answers[ksf.INVESTOR_TR] = KisError("FHPTJ04160001: HTTP 500 EGW00201")
        await flow.collect([SEC], "kospi", TUE)
        await settle(flow)
        _, flows, owned = await flow.collect([SEC], "kospi", TUE)
        # Fetched at 20:00, Tuesday's row stays unpublished even though this refresh ran at 20:15
        assert {row.as_of: row.confirmed for row in flows} == {TUE: False, MON: True}
        assert owned
        assert len(kis.calls) == 6
    asyncio.run(run())


@pytest.fixture
def redis_server():
    server = fakeredis.FakeServer()
    yield server
    sc.use(None, None)


def test_a_restarted_process_reads_the_shared_value_without_calling_kis(redis_server):
    at = kst(TUE, 21)

    async def settle(flow):
        await asyncio.gather(*list(flow._background))

    async def run():
        sc.use(fakeredis.FakeRedis(server=redis_server, decode_responses=True),
               fakeredis.FakeAsyncRedis(server=redis_server, decode_responses=True), SECRET)
        first = service(FakeKis(), at)
        expected = await first.collect([SEC], "kospi", TUE)
        await settle(first)
        kis = FakeKis()
        assert await service(kis, at).collect([SEC], "kospi", TUE) == expected
        assert kis.calls == []

        partial = FakeKis()                              # a fetch with a failed call is not shared
        partial.answers[ksf.PRICE_TR] = KisError("FHKST01010100: HTTP 500 EGW00201")
        flow = service(partial, at)
        await flow.collect([HYNIX], "kospi", TUE)
        await settle(flow)
        assert len(fakeredis.FakeRedis(server=redis_server).keys(f"td:{ksf.SHARED_NS}:*")) == 1     # Samsung's
        kis = FakeKis()
        await service(kis, at).collect([HYNIX], "kospi", TUE)
        assert len(kis.calls) == 3

        # A shared value of another shape is a miss
        await sc.aput(ksf.SHARED_NS, ("035420", "2026-11-24"),
                      {"code": "035420", "session": "2026-11-24", "fetched_at": at.isoformat(), "trades": "x"}, 60)
        kis = FakeKis()
        _, flows, _ = await service(kis, at).collect(["035420"], "kospi", TUE)
        assert len(kis.calls) == 3 and len(flows) == 2
    asyncio.run(run())


def test_the_budget_returns_what_is_ready_and_a_slow_fetch_fills_the_cache_later():
    async def run():
        kis = FakeKis()
        kis.gates[HYNIX] = asyncio.Event()
        flow = service(kis, kst(TUE, 21))
        _, flows, _ = await flow.collect([SEC, HYNIX], "kospi", TUE, budget_sec=0.05)
        assert {row.ticker for row in flows} == {"005930.KS"}
        kis.gates[HYNIX].set()
        await flow._flights[(HYNIX, "2026-11-24")]     # the fetch went on after the request returned
        _, flows, _ = await flow.collect([SEC, HYNIX], "kospi", TUE, budget_sec=0)
        assert {row.ticker for row in flows} == {"005930.KS", "000660.KS"}
        assert len(kis.calls) == 6
    asyncio.run(run())


def test_without_a_kis_client_the_source_is_unavailable():
    assert asyncio.run(ksf.KisStockFlow(None).collect([SEC], "kospi", TUE)) is None
    sources = view((None, None, None), kst(TUE, 21))["sources"]
    assert sources["foreign_flow"]["status"] == sources["ownership"]["status"] == "source_unavailable"
    assert asyncio.run(service(FakeKis(), kst(TUE, 21)).collect([], "kospi", TUE)) == ([], [], [])


def test_a_small_amount_rounded_to_its_unit_still_finds_that_unit():
    # 300 shares at about 2,000 won published as "1" (million won): 600,000 won rounds to 1
    assert ksf.won(1, 300, 1_950, 2_050) == 1_000_000
    # A rounded zero cannot be told from a real zero on a day with trades
    assert ksf.won(0, 300, 1_950, 2_050) is None


def test_the_final_time_follows_a_late_close():
    late = date(2026, 11, 19)      # the CSAT day: the market opens and closes an hour late
    assert ksf._final(TUE, kst(TUE, 20, 10)) and not ksf._final(TUE, kst(TUE, 20, 9))
    assert not ksf._final(late, kst(late, 20, 30)) and ksf._final(late, kst(late, 21, 10))
    assert not ksf._final(SUN, kst(MON, 9))


def test_an_expired_answer_is_served_at_once_while_its_refresh_runs():
    async def run():
        clock, kis = Clock(), FakeKis()
        flow = service(kis, kst(TUE, 21), clock)
        await flow.collect([SEC], "kospi", TUE)
        clock.t += ksf.FINAL_TTL + 1
        kis.gates[SEC] = asyncio.Event()                 # the refresh hangs
        _, flows, _ = await flow.collect([SEC], "kospi", TUE, budget_sec=5)
        assert {row.ticker for row in flows} == {"005930.KS"}   # no wait for the hung refresh
        assert (SEC, "2026-11-24") in flow._flights
        kis.gates[SEC].set()
        await settle(flow)
        assert len(kis.calls) == 6
    asyncio.run(run())


def test_a_new_session_shows_the_previous_sessions_history_until_its_own_answer_lands():
    async def run():
        kis = FakeKis()
        flow = service(kis, kst(TUE, 21))
        await flow.collect([SEC], "kospi", MON)
        kis.gates[SEC] = asyncio.Event()
        _, flows, _ = await flow.collect([SEC], "kospi", TUE, budget_sec=0.01)
        assert flows and {row.ticker for row in flows} == {"005930.KS"}
        kis.gates[SEC].set()
        await settle(flow)
    asyncio.run(run())


def test_fetches_still_running_read_as_no_data_not_as_a_missing_source():
    async def run():
        kis = FakeKis()
        kis.gates[SEC] = asyncio.Event()
        flow = service(kis, kst(TUE, 21))
        assert await flow.collect([SEC], "kospi", TUE, budget_sec=0.01) == ([], [], [])
        kis.gates[SEC].set()
        await settle(flow)
    asyncio.run(run())


def test_fetches_queued_at_once_are_capped(monkeypatch):
    monkeypatch.setattr(ksf, "MAX_FLIGHTS", 2)

    async def run():
        kis = FakeKis()
        codes = ["005930", "000660", "035420"]
        for code in codes:
            kis.gates[code] = asyncio.Event()
        flow = service(kis, kst(TUE, 21))
        assert await flow.collect(codes, "kospi", TUE, budget_sec=0.01) == ([], [], [])
        assert set(flow._flights) == {(code, "2026-11-24") for code in codes[:2]}
        for gate in kis.gates.values():
            gate.set()
        await settle(flow)
        assert {call[2]["FID_INPUT_ISCD"] for call in kis.calls} == set(codes[:2])
    asyncio.run(run())
