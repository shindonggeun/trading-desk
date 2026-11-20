"""KOSPI / KOSDAQ trading value by investor class (#76): Toss parsing, net and provisional marks,
caching, and the API."""

import asyncio
from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest

from desk.broker import MockBroker, TossBroker
from desk.broker.base import INSTITUTION_KINDS, INVESTORS, FlowRecord, IndexPrice
from desk.market_flow import MarketFlow, market_open, normalize
from tests.test_api import _client

KST = timezone(timedelta(hours=9))


def _pair(buy, sell):
    return {"buyAmount": str(buy), "sellAmount": str(sell)}


# Shape of GET /api/v1/market-indicators/KOSPI/investor-trading (spec 1.2.14).
TOSS_RECORD = {
    "date": "2026-11-05",
    "updatedAt": "2026-11-05T13:20:00+09:00",
    "individual": _pair(5_200_000_000_000, 5_350_000_000_000),
    "foreigner": _pair(3_000_000_000_000, 2_700_000_000_000),
    "institution": {**_pair(2_100_000_000_000, 2_180_000_000_000), "breakdown": {
        "financialInvestment": _pair(900_000_000_000, 950_000_000_000),
        "insurance": _pair(100_000_000_000, 90_000_000_000),
        "trust": _pair(200_000_000_000, 210_000_000_000),
        "privateEquityFund": _pair(50_000_000_000, 60_000_000_000),
        "bank": _pair(20_000_000_000, 30_000_000_000),
        "otherFinancialInstitution": _pair(30_000_000_000, 40_000_000_000),
        "pensionFund": _pair(800_000_000_000, 800_000_000_000),
    }},
    "otherCorporation": _pair(300_000_000_000, 370_000_000_000),
}


class FakeToss:
    def __init__(self, records=None, prices=None, status=200):
        self.records = records if records is not None else [TOSS_RECORD]
        self.prices = prices if prices is not None else [{"symbol": "KOSPI", "lastPrice": "2812.45",
                                                          "timestamp": "2026-11-05T13:20:00+09:00"}]
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"code": "unsupported-symbol", "message": "secret detail"}})
        if request.url.path.endswith("/investor-trading"):
            return httpx.Response(200, json={"result": {"records": self.records, "nextUntil": "2026-11-04"}})
        return httpx.Response(200, json={"result": self.prices})


def _toss(fake: FakeToss) -> TossBroker:
    return TossBroker("id", "secret", http=httpx.AsyncClient(base_url="https://toss.test", transport=httpx.MockTransport(fake)))


def test_toss_records_are_parsed_into_integers_with_the_institution_breakdown():
    fake = FakeToss()
    records, next_until = asyncio.run(_toss(fake).investor_flow("KOSPI", "1d", 20))
    req = fake.requests[0]
    assert req.url.path == "/api/v1/market-indicators/KOSPI/investor-trading"
    assert dict(req.url.params) == {"interval": "1d", "count": "20"}
    assert "x-tossinvest-account" not in req.headers        # market data: token only, no account
    assert next_until == "2026-11-04"
    rec = records[0]
    assert rec.investors["individual"] == (5_200_000_000_000, 5_350_000_000_000)
    assert rec.investors["other_corporation"] == (300_000_000_000, 370_000_000_000)
    assert set(rec.institution_breakdown) == set(INSTITUTION_KINDS)
    assert rec.institution_breakdown["pension_fund"] == (800_000_000_000, 800_000_000_000)
    # the four classes balance market-wide
    assert sum(b for b, _ in rec.investors.values()) == sum(s for _, s in rec.investors.values())


def test_a_broken_toss_record_is_skipped_and_the_rest_still_shows():
    missing = {k: v for k, v in TOSS_RECORD.items() if k != "foreigner"}
    garbage = {**TOSS_RECORD, "date": "2026-11-03", "individual": {"buyAmount": "abc", "sellAmount": "1"}}
    negative = {**TOSS_RECORD, "date": "2026-11-02", "individual": _pair(-1, 5)}
    no_breakdown = {**TOSS_RECORD, "date": "2026-11-01", "institution": _pair(1, 2)}
    fake = FakeToss(records=[TOSS_RECORD, missing, garbage, negative, no_breakdown, "junk"])
    records, _ = asyncio.run(_toss(fake).investor_flow("KOSPI", "1d", 20, until=datetime(2026, 11, 5).date()))
    assert [r.date for r in records] == ["2026-11-05", "2026-11-01"]
    assert records[1].institution_breakdown == {}
    assert fake.requests[0].url.params["until"] == "2026-11-05"


def test_toss_index_prices():
    prices = asyncio.run(_toss(FakeToss()).index_prices(["KOSPI"]))
    assert prices == [IndexPrice("KOSPI", 2812.45, "2026-11-05T13:20:00+09:00")]


def _record(day="2026-11-05", updated="2026-11-05T13:20:00+09:00"):
    return FlowRecord(day, updated, {"individual": (100, 160), "foreigner": (90, 40), "institution": (50, 45),
                                     "other_corporation": (10, 5)}, {"pension_fund": (30, 20)})


def test_net_is_buy_minus_sell_and_todays_row_is_provisional_until_the_evening():
    noon = datetime(2026, 11, 5, 13, 30, tzinfo=KST)
    row = normalize(_record(), "1d", noon)
    assert row["investors"]["individual"] == {"buy": 100, "sell": 160, "net": -60}
    assert row["investors"]["foreigner"]["net"] == 50
    assert sum(v["net"] for v in row["investors"].values()) == 0
    assert row["institution_breakdown"] == {"pension_fund": {"buy": 30, "sell": 20, "net": 10}}
    assert row["in_progress"] and row["provisional"]
    confirmed = normalize(_record(updated="2026-11-05T18:10:00+09:00"), "1d", datetime(2026, 11, 5, 19, tzinfo=KST))
    assert confirmed["in_progress"] and not confirmed["provisional"]
    older = normalize(_record("2026-11-04", "2026-11-04T18:10:00+09:00"), "1d", noon)
    assert not older["in_progress"] and not older["provisional"]


def test_the_current_week_month_and_year_are_in_progress():
    noon = datetime(2026, 11, 5, 13, 30, tzinfo=KST)                     # a Thursday
    assert normalize(_record("2026-11-02", "2026-11-04T18:10:00+09:00"), "1w", noon)["in_progress"]
    assert not normalize(_record("2026-10-26"), "1w", noon)["in_progress"]
    assert normalize(_record("2026-11-01"), "1mo", noon)["in_progress"]
    assert normalize(_record("2026-01-01"), "1y", noon)["in_progress"]
    # yesterday's confirmed numbers: the week is still going (in progress) but what is in it is final
    week = normalize(_record("2026-11-02", "2026-11-04T18:10:00+09:00"), "1w", noon)
    assert week["in_progress"] and not week["provisional"]
    # updated today during the session: provisional
    assert normalize(_record("2026-11-02", "2026-11-05T13:20:00+09:00"), "1w", noon)["provisional"]
    # ISO week across the new year: Thu 2026-12-31 and Fri 2027-01-01 are the same week
    assert normalize(_record("2026-12-31"), "1w", datetime(2027, 1, 1, 10, tzinfo=KST))["in_progress"]


def test_a_confirmed_period_stays_final_over_the_weekend():
    saturday = datetime(2026, 11, 7, 11, tzinfo=KST)
    friday_close = "2026-11-06T18:10:00+09:00"
    for interval, day in (("1w", "2026-11-02"), ("1mo", "2026-11-01"), ("1y", "2026-01-01")):
        row = normalize(_record(day, friday_close), interval, saturday)
        assert row["in_progress"] and not row["provisional"], interval
    friday = normalize(_record("2026-11-06", "2026-11-06T15:45:00+09:00"), "1d", saturday)
    assert not friday["in_progress"] and not friday["provisional"]


def test_a_missing_or_broken_update_time_counts_as_provisional_while_in_progress():
    noon = datetime(2026, 11, 5, 13, 30, tzinfo=KST)
    for updated in ("", "not a time"):
        row = normalize(_record(updated=updated), "1d", noon)
        assert row["updated_at"] is None and row["provisional"]
    assert not normalize(_record("2026-11-04", ""), "1d", noon)["provisional"]


def test_market_hours_are_the_krx_session_on_weekdays():
    assert market_open(datetime(2026, 11, 5, 10, tzinfo=KST))
    assert market_open(datetime(2026, 11, 5, 15, 40, tzinfo=KST))
    assert not market_open(datetime(2026, 11, 5, 15, 41, tzinfo=KST))
    assert not market_open(datetime(2026, 11, 5, 8, 59, tzinfo=KST))
    assert not market_open(datetime(2026, 11, 7, 11, tzinfo=KST))        # Saturday


class CountingBroker(MockBroker):
    def __init__(self, fail=False, delay=0.0):
        self.calls = 0
        self.fail = fail
        self.delay = delay

    async def investor_flow(self, market, interval, count, until=None):
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("Toss GET /api/v1/market-indicators failed 500: {'secret': 'body'}")
        return await super().investor_flow(market, interval, count, until)


def test_one_broker_call_per_key_while_cached_even_when_requests_overlap():
    clock = [0.0]
    broker = CountingBroker(delay=0.01)
    flow = MarketFlow(broker, clock=lambda: clock[0], now=lambda: datetime(2026, 11, 5, 10, tzinfo=KST).astimezone(UTC))

    async def scenario():
        first = await asyncio.gather(*[flow.get("KOSPI", "1d", 20) for _ in range(5)])
        assert broker.calls == 1 and all(r == first[0] for r in first)
        await flow.get("KOSDAQ", "1d", 20)
        assert broker.calls == 2                          # another key, another call
        clock[0] += 59
        await flow.get("KOSPI", "1d", 20)
        assert broker.calls == 2                          # 60 s during the session
        clock[0] += 2
        await flow.get("KOSPI", "1d", 20)
        assert broker.calls == 3
    asyncio.run(scenario())


def test_cache_is_longer_after_the_close():
    clock = [0.0]
    broker = CountingBroker()
    flow = MarketFlow(broker, clock=lambda: clock[0], now=lambda: datetime(2026, 11, 5, 20, tzinfo=KST))

    async def scenario():
        await flow.get("KOSPI", "1w", 20)
        clock[0] += 300
        await flow.get("KOSPI", "1w", 20)
        assert broker.calls == 1
        clock[0] += 301
        await flow.get("KOSPI", "1w", 20)
        assert broker.calls == 2
    asyncio.run(scenario())


def test_a_failure_is_short_and_remembered_briefly():
    clock = [0.0]
    broker = CountingBroker(fail=True)
    flow = MarketFlow(broker, clock=lambda: clock[0], now=lambda: datetime(2026, 11, 5, 20, tzinfo=KST))

    async def scenario():
        out = await flow.get("KOSPI", "1d", 20)
        assert out["records"] == [] and out["supported"]
        assert out["error"] == "RuntimeError: Toss GET /api/v1/market-indicators failed 500"
        assert "secret" not in str(out)
        clock[0] += 29
        await flow.get("KOSPI", "1d", 20)
        assert broker.calls == 1
        clock[0] += 2
        await flow.get("KOSPI", "1d", 20)
        assert broker.calls == 2                          # failures are cached 30 s, not 10 min
    asyncio.run(scenario())


def test_bad_arguments_are_rejected_and_count_is_clamped():
    flow = MarketFlow(CountingBroker())
    with pytest.raises(ValueError):
        asyncio.run(flow.get("NASDAQ", "1d", 20))
    with pytest.raises(ValueError):
        asyncio.run(flow.get("KOSPI", "1m", 20))
    assert len(asyncio.run(flow.get("KOSPI", "1d", 5000))["records"]) == 100


def test_a_missing_index_price_does_not_hide_the_flow():
    class NoIndex(MockBroker):
        async def index_prices(self, symbols):
            raise RuntimeError("down")
    out = asyncio.run(MarketFlow(NoIndex()).get("KOSPI", "1d", 3))
    assert out["index"] is None and len(out["records"]) == 3 and out["error"] is None


def test_mock_sample_keeps_tosss_invariants():
    for market in ("KOSPI", "KOSDAQ"):
        for interval in ("1d", "1w", "1mo", "1y"):
            records, next_until = asyncio.run(MockBroker().investor_flow(market, interval, 100))
            assert len(records) == 100 and next_until
            days = [r.date for r in records]
            assert days == sorted(days, reverse=True) and len(set(days)) == 100
            for r in records:
                assert set(r.investors) == set(INVESTORS)
                assert sum(b for b, _ in r.investors.values()) == sum(s for _, s in r.investors.values())
                assert r.investors["institution"] == (sum(b for b, _ in r.institution_breakdown.values()),
                                                       sum(s for _, s in r.institution_breakdown.values()))
                assert min(min(p) for p in [*r.investors.values(), *r.institution_breakdown.values()]) >= 0


def test_api_returns_the_flow_with_the_index_and_rejects_bad_input(db_url):
    with _client(db_url) as client:
        r = client.get("/api/market/flow?market=kospi&interval=1d&count=20")
        assert r.status_code == 200
        body = r.json()
        assert body["market"] == "KOSPI" and body["source"] == "mock" and body["supported"]
        assert body["index"]["price"] == 2812.45
        assert len(body["records"]) == 20
        rec = body["records"][0]
        assert set(rec["investors"]) == set(INVESTORS)
        assert sum(v["net"] for v in rec["investors"].values()) == 0
        assert len(rec["institution_breakdown"]) == 7
        assert "premarket" in body and body["premarket"] is None          # #677 no pre-market snapshot today
        assert "program" in body and body["program"] is None              # #677 no program trading snapshot today
        assert "premarket" not in client.get("/api/market/flow?market=kospi&interval=1w").json()
        assert client.get("/api/market/flow?market=NASDAQ").status_code == 422
        assert client.get("/api/market/flow?interval=1m").status_code == 422
        assert client.get("/api/market/flow?count=abc").status_code == 422


def test_api_needs_a_session(db_url):
    with _client(db_url, login=False) as client:
        assert client.get("/api/market/flow").status_code == 401


def test_a_broker_without_market_data_says_unsupported():
    class Bare(MockBroker):
        async def investor_flow(self, *args, **kwargs):
            raise NotImplementedError

    out = asyncio.run(MarketFlow(Bare()).get("KOSPI", "1d", 5))
    assert out["supported"] is False and out["records"] == [] and out["error"] is None


def test_every_count_is_cut_from_one_full_page():
    broker = CountingBroker()
    flow = MarketFlow(broker, now=lambda: datetime(2026, 11, 5, 10, tzinfo=KST))

    async def scenario():
        five = await flow.get("KOSPI", "1d", 5)
        twenty = await flow.get("KOSPI", "1d", 20)
        full = await flow.get("KOSPI", "1d", 100)
        assert (len(five["records"]), len(twenty["records"]), len(full["records"])) == (5, 20, 100)
        assert five["records"] == twenty["records"][:5]
    asyncio.run(scenario())
    assert broker.calls == 1


def test_a_pre_open_answer_expires_at_the_open():
    clock = [0.0]
    broker = CountingBroker()
    flow = MarketFlow(broker, clock=lambda: clock[0], now=lambda: datetime(2026, 11, 5, 7, 58, tzinfo=KST))

    async def scenario():
        await flow.get("KOSPI", "1d", 5)
        clock[0] += 119
        await flow.get("KOSPI", "1d", 5)
        assert broker.calls == 1
        clock[0] += 2                                      # 08:00 (pre-market) passed: not 10 more minutes
        await flow.get("KOSPI", "1d", 5)
        assert broker.calls == 2
    asyncio.run(scenario())


def test_toss_odd_shapes_do_not_fail_the_whole_call():
    class Odd(FakeToss):
        def __call__(self, request):
            if request.url.path.endswith("/investor-trading"):
                self.requests.append(request)
                if request.url.params.get("count") == "1":
                    return httpx.Response(200, json={"result": {"records": None}})
                rows = [{**TOSS_RECORD, "institution": {**_pair(1, 2), "breakdown": "oops"}},
                        {**TOSS_RECORD, "date": "2026-11-04", "foreigner": {"buyAmount": "NaN", "sellAmount": "1"}},
                        {**TOSS_RECORD, "date": "2026-11-03", "foreigner": {"buyAmount": "inf", "sellAmount": "1"}},
                        {**TOSS_RECORD, "date": "2026-11-02", "foreigner": _pair("1,000", "900")}]
                return httpx.Response(200, json={"result": {"records": rows}})
            if request.url.path.endswith("/prices"):
                return httpx.Response(200, json={"result": ["junk", {"symbol": "KOSPI", "lastPrice": "2800", "timestamp": 5}]})
            return super().__call__(request)

    broker = _toss(Odd())
    empty, nxt = asyncio.run(broker.investor_flow("KOSPI", "1d", 1))
    assert empty == [] and nxt is None
    records, _ = asyncio.run(broker.investor_flow("KOSPI", "1d", 20))
    assert [r.date for r in records] == ["2026-11-05", "2026-11-02"]
    assert records[0].institution_breakdown == {}
    assert records[1].investors["foreigner"] == (1000, 900)
    assert asyncio.run(broker.index_prices(["KOSPI"])) == [IndexPrice("KOSPI", 2800.0, None)]


def test_exact_integer_amounts_beyond_float_precision():
    big = 9_007_199_254_740_993                           # 2**53 + 1
    fake = FakeToss(records=[{**TOSS_RECORD, "individual": _pair(big, big - 1)}])
    records, _ = asyncio.run(_toss(fake).investor_flow("KOSPI", "1y", 1))
    assert records[0].investors["individual"] == (big, big - 1)


def test_a_toss_error_reaches_the_api_as_a_short_message():
    fake = FakeToss(status=400)
    out = asyncio.run(MarketFlow(_toss(fake), now=lambda: datetime(2026, 11, 5, 10, tzinfo=KST)).get("KOSPI", "1d", 5))
    assert out["records"] == [] and out["supported"]
    assert out["error"] == "TossError: Toss GET /api/v1/market-indicators/KOSPI/investor-trading failed 400"
    assert "secret detail" not in str(out)


def test_toss_index_failure_leaves_the_flow():
    class NoPrices(FakeToss):
        def __call__(self, request):
            if request.url.path.endswith("/prices"):
                return httpx.Response(503, json={"error": {"code": "down"}})
            return super().__call__(request)

    out = asyncio.run(MarketFlow(_toss(NoPrices())).get("KOSPI", "1d", 5))
    assert out["index"] is None and out["error"] is None and len(out["records"]) == 1


# #673 -------------------------------------------------------------------------------------------


class ScriptedBroker(MockBroker):
    """Today's KOSPI record with the given (updatedAt, foreign buy) per call."""

    def __init__(self, script):
        self.script, self.calls = list(script), 0

    async def investor_flow(self, market, interval, count, until=None):
        updated, buy = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        pair = (1_000, 900)
        return [FlowRecord(date="2026-11-05", updated_at=updated,
                           investors={"individual": pair, "foreigner": (buy, 900), "institution": pair,
                                      "other_corporation": pair}, institution_breakdown={})], None


def _at(h, m, s=0):
    return datetime(2026, 11, 5, h, m, s, tzinfo=KST)


def test_figures_moving_under_a_stuck_update_time_carry_the_fetch_before_the_change(caplog):
    caplog.set_level("INFO", logger="desk.market_flow")
    stuck = "2026-11-05T09:01:00+09:00"
    broker = ScriptedBroker([(stuck, 1_000), (stuck, 1_000), (stuck, 1_500), (stuck, 1_500)])
    now = [_at(9, 5)]
    flow = MarketFlow(broker, now=lambda: now[0])

    async def scenario():
        first = await flow.get("KOSPI", "1d", 1, fresh=True)
        assert first["freshness"]["source_updated_at"] == "2026-11-05T00:01:00+00:00"   # updatedAt only
        now[0] = _at(9, 6)
        same = await flow.get("KOSPI", "1d", 1, fresh=True)
        assert same["freshness"]["source_updated_at"] == "2026-11-05T00:01:00+00:00"    # nothing moved yet
        now[0] = _at(9, 7)
        moved = await flow.get("KOSPI", "1d", 1, fresh=True)
        # the change came after the 09:06 fetch: that time, never the 09:07 one (not fresher than known)
        assert moved["changed_at"] == "2026-11-05T09:06:00+09:00"
        assert moved["freshness"]["source_updated_at"] == "2026-11-05T00:06:00+00:00"
        assert moved["freshness"]["source_delay_sec"] == 60
        now[0] = _at(9, 8)
        later = await flow.get("KOSPI", "1d", 1, fresh=True)
        assert later["changed_at"] == "2026-11-05T09:06:00+09:00"                     # kept while unchanged
        assert broker.calls == 4                                                     # fresh skips the cache
    asyncio.run(scenario())
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("toss flow")]
    assert lines == ["toss flow KOSPI: updatedAt 09:01:00 figures changed (since last change first)"]


def test_a_newer_update_time_wins_over_the_seen_change():
    broker = ScriptedBroker([("2026-11-05T09:01:00+09:00", 1_000), ("2026-11-05T09:01:00+09:00", 1_500),
                             ("2026-11-05T09:09:30+09:00", 1_500)])
    now = [_at(9, 5)]
    flow = MarketFlow(broker, now=lambda: now[0])

    async def scenario():
        await flow.get("KOSPI", "1d", 1, fresh=True)
        now[0] = _at(9, 8)
        await flow.get("KOSPI", "1d", 1, fresh=True)
        now[0] = _at(9, 10)
        got = await flow.get("KOSPI", "1d", 1, fresh=True)
        assert got["changed_at"] == "2026-11-05T09:05:00+09:00"
        assert got["freshness"]["source_updated_at"] == "2026-11-05T00:09:30+00:00"
    asyncio.run(scenario())


def test_another_day_or_interval_is_not_watched():
    broker = ScriptedBroker([("2026-11-04T18:10:00+09:00", 1_000), ("2026-11-04T18:10:00+09:00", 1_500)])
    flow = MarketFlow(broker, now=lambda: _at(9, 5) + timedelta(days=1))

    async def scenario():
        await flow.get("KOSPI", "1d", 1, fresh=True)
        got = await flow.get("KOSPI", "1d", 1, fresh=True)
        assert got["changed_at"] is None and got["freshness"]["source_updated_at"] is None
        week = await flow.get("KOSPI", "1w", 1, fresh=True)
        assert week["changed_at"] is None
    asyncio.run(scenario())


def test_pre_market_is_cached_as_the_session_and_says_so():
    clock = [0.0]
    broker = CountingBroker()
    flow = MarketFlow(broker, clock=lambda: clock[0], now=lambda: _at(8, 20))

    async def scenario():
        got = await flow.get("KOSPI", "1d", 5)
        assert got["session"] == "pre" and got["freshness"]["live"] is True
        clock[0] += 61
        await flow.get("KOSPI", "1d", 5)
        assert broker.calls == 2                          # 60 s, as in the session
    asyncio.run(scenario())


def test_a_failed_fresh_read_answers_the_snapshot_but_keeps_the_good_cache():
    clock = [0.0]
    broker = CountingBroker()
    flow = MarketFlow(broker, clock=lambda: clock[0], now=lambda: _at(10, 0))

    async def scenario():
        good = await flow.get("KOSPI", "1d", 5)
        broker.fail = True
        clock[0] += 30
        bad = await flow.get("KOSPI", "1d", 5, fresh=True)
        assert bad["error"] and bad["records"] == []
        card = await flow.get("KOSPI", "1d", 5)
        assert card["records"] == good["records"] and broker.calls == 2    # still the 30 s old good answer
    asyncio.run(scenario())


def test_only_a_new_update_time_keeps_the_seen_change(caplog):
    caplog.set_level("INFO", logger="desk.market_flow")
    broker = ScriptedBroker([("2026-11-05T09:01:00+09:00", 1_000), ("2026-11-05T09:01:00+09:00", 1_500),
                             ("2026-11-05T09:04:00+09:00", 1_500)])
    now = [_at(9, 2)]
    flow = MarketFlow(broker, now=lambda: now[0])

    async def scenario():
        for at in (_at(9, 2), _at(9, 3), _at(9, 5)):
            now[0] = at
            got = await flow.get("KOSPI", "1d", 1, fresh=True)
        assert got["changed_at"] == "2026-11-05T09:02:00+09:00"
    asyncio.run(scenario())
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("toss flow")]
    assert lines[-1] == "toss flow KOSPI: updatedAt 09:04:00 figures same (since last change -)"


def test_sessions_by_the_clock():
    from desk.market_flow import session
    assert session(_at(7, 59)) == "closed"
    assert session(_at(8, 0)) == "pre"
    assert session(_at(8, 59)) == "pre"
    assert session(_at(9, 0)) == "regular"
    assert session(_at(15, 41)) == "closed"
    assert session(datetime(2026, 11, 7, 8, 30, tzinfo=KST)) == "closed"            # Saturday
