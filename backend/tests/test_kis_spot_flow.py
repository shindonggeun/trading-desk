"""#676 today's spot investor flow from KIS first (desk.kis_spot_flow), Toss behind it and as the fallback."""
import asyncio
from datetime import UTC, datetime, timedelta, timezone

import pytest

from desk import kis_spot_flow
from desk.broker import MockBroker
from desk.broker.base import FlowRecord
from desk.futures_flow import KisError
from desk.market_flow import MarketFlow

KST = timezone(timedelta(hours=9))
JO = 1_000_000_000_000          # 1조 KRW


@pytest.fixture(autouse=True)
def _kis_spot_on(monkeypatch):
    """These tests are the KIS path's (off in production since #711: KIS counts a narrower market)."""
    monkeypatch.setattr(kis_spot_flow, "ENABLED", True)


def _cols(prefix, buy, sell):
    return {f"{prefix}_shnu_tr_pbmn": str(buy), f"{prefix}_seln_tr_pbmn": str(sell),
            f"{prefix}_ntby_tr_pbmn": str(buy - sell), f"{prefix}_ntby_qty": "0"}


def _row(individual_buy_mn=3_400_000):
    """The FHPTJ04030000 answer's row (million won, numbers as strings): the futures' fixture's columns plus
    the buy / sell values of each class and the institution's kinds."""
    return {**_cols("prsn", individual_buy_mn, 3_100_000), **_cols("frgn", 2_000_000, 2_250_000),
            **_cols("orgn", 1_200_000, 1_230_000), **_cols("etc_corp", 90_000, 110_000),
            **_cols("scrt", 500_000, 520_000), **_cols("insu", 40_000, 30_000), **_cols("ivtr", 60_000, 70_000),
            **_cols("pe_fund", 10_000, 12_000), **_cols("bank", 5_000, 6_000), **_cols("mrbn", 1_000, 2_000),
            **_cols("fund", 300_000, 290_000)}


def _answer(individual_buy_mn=3_400_000):
    return {"rt_cd": "0", "output": [_row(individual_buy_mn)]}


class FakeKis:
    def __init__(self, answer=None, fail=False, delay=0.0):
        self.answer, self.fail, self.delay, self.calls = answer or _answer(), fail, delay, []

    async def get(self, path, tr_id, params):
        self.calls.append((path, tr_id, params))
        await asyncio.sleep(self.delay)
        if self.fail:
            raise KisError("FHPTJ04030000: HTTP 500 EGW00201")
        return self.answer


class TossBroker(MockBroker):
    """Toss' KOSPI 1d records: today's (individual buy ``buy``) and yesterday's (``before``)."""

    name = "toss"

    def __init__(self, buy=3 * JO, before=7 * JO, fail=False, updated="2026-11-05T09:01:00+09:00"):
        self.buy, self.before, self.fail, self.updated, self.calls = buy, before, fail, updated, 0

    async def investor_flow(self, market, interval, count, until=None):
        self.calls += 1
        if self.fail:
            raise RuntimeError("Toss GET failed 503: {'secret': 'body'}")
        pair = (JO, JO + JO // 10)
        today = FlowRecord(date="2026-11-05", updated_at=self.updated,
                           investors={"individual": (self.buy, self.buy - JO // 10), "foreigner": pair,
                                      "institution": pair, "other_corporation": pair}, institution_breakdown={})
        before = FlowRecord(date="2026-11-04", updated_at="2026-11-04T18:10:00+09:00",
                            investors={"individual": (self.before, self.before), "foreigner": pair,
                                       "institution": pair, "other_corporation": pair}, institution_breakdown={})
        return ([today] if self.buy else []) + [before], None


def _at(h, m):
    return datetime(2026, 11, 5, h, m, tzinfo=KST).astimezone(UTC)


def _notes(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("kis spot flow")]


def test_the_kis_answer_becomes_a_toss_shaped_record_in_won():
    got = kis_spot_flow.record(_answer()["output"], "2026-11-05")
    assert got["source"] == "kis" and got["updated_at"] is None and got["provisional"] is True
    assert got["investors"]["foreigner"] == {"buy": 2 * JO, "sell": 2_250_000_000_000, "net": -250_000_000_000}
    assert got["investors"]["other_corporation"]["net"] == -20_000_000_000
    assert got["institution_breakdown"]["pension_fund"]["net"] == 10_000_000_000
    assert set(got["institution_breakdown"]) == set(kis_spot_flow.KINDS)


def test_a_kis_answer_without_a_main_class_or_trades_is_no_record():
    no_foreign = {k: v for k, v in _row().items() if not k.startswith("frgn")}
    assert kis_spot_flow.record([no_foreign], "2026-11-05") is None
    assert kis_spot_flow.record([{k: "0" for k in _row()}], "2026-11-05") is None
    assert kis_spot_flow.record([], "2026-11-05") is None
    assert kis_spot_flow.record(None, "2026-11-05") is None


@pytest.mark.parametrize(("market", "code", "sector", "buy_mn", "sell_mn", "won"), [
    ("KOSPI", "KSP", "0001", 1, 0, {"buy": 1_000_000, "sell": 0, "net": 1_000_000}),
    ("KOSDAQ", "KSQ", "1001", 0, 1, {"buy": 0, "sell": 1_000_000, "net": -1_000_000}),
])
def test_kis_spot_market_request_and_amount_boundary(market, code, sector, buy_mn, sell_mn, won):
    row = {**_row(), **_cols("frgn", buy_mn, sell_mn), "frgn_ntby_qty": "42"}
    kis = FakeKis({"rt_cd": "0", "output": [row]})

    answer = asyncio.run(kis_spot_flow.fetch(kis, market))

    # The published [0403] TR, using the repository's spot market/sector mappings.
    assert kis.calls == [("/uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market",
                          "FHPTJ04030000", {"FID_INPUT_ISCD": code, "FID_INPUT_ISCD_2": sector})]
    got = kis_spot_flow.record(answer["output"], "2026-11-05")
    assert got["investors"]["foreigner"] == won


def test_the_check():
    kis = kis_spot_flow.record(_answer(3_400_000)["output"], "2026-11-05")         # 3.4조
    today = {"date": "2026-11-05", "investors": {"individual": {"buy": 3 * JO}}}
    before = {"date": "2026-11-04", "investors": {"individual": {"buy": 7 * JO}}}
    assert kis_spot_flow.check(kis, today, before, False) is None
    # Toss far behind (its updatedAt stuck early): only a floor - KIS well above it is fine
    early = {"date": "2026-11-05", "investors": {"individual": {"buy": 30_000_000_000}}}
    assert kis_spot_flow.check(kis, early, before, False) is None
    assert "under Toss" in kis_spot_flow.check(kis, {**today, "investors": {"individual": {"buy": 7 * JO}}},
                                               before, False)
    small = {"date": "2026-11-04", "investors": {"individual": {"buy": JO}}}
    assert "over 3x" in kis_spot_flow.check(kis, today, small, False)            # 1000x unit or another market
    six = {"date": "2026-11-04", "investors": {"individual": {"buy": 6 * JO}}}
    assert "yesterday's totals" in kis_spot_flow.check(kis, None, six, True)     # 3.4조 before 09:00
    assert kis_spot_flow.check(kis, None, None, True) is None                    # nothing to check against
    tiny = {"date": "2026-11-05", "investors": {"individual": {"buy": 5_000_000_000}}}   # 50억: says nothing
    assert kis_spot_flow.verified(kis, today) and not kis_spot_flow.verified(kis, tiny)


def test_kis_comes_first_with_tosss_history_behind_it(caplog):
    caplog.set_level("INFO", logger="desk.market_flow")
    kis = FakeKis()
    flow = MarketFlow(TossBroker(), now=lambda: _at(10, 0), kis=kis)
    got = asyncio.run(flow.get("KOSPI", "1d", 5))
    assert [r["date"] for r in got["records"]] == ["2026-11-05", "2026-11-04"]
    assert got["records"][0]["source"] == "kis" and got["records"][1].get("source") is None
    assert got["records"][0]["investors"]["individual"]["buy"] == 3_400_000_000_000
    assert got["freshness"]["source_updated_at"] is None          # KIS has no time; no change seen yet
    assert kis.calls[0][2] == {"FID_INPUT_ISCD": "KSP", "FID_INPUT_ISCD_2": "0001"}
    assert _notes(caplog) == ["kis spot flow KOSPI: individual buy KIS 3400000000000 vs Toss 3000000000000 - shown"]


def test_a_stuck_early_toss_record_does_not_push_kis_out():
    flow = MarketFlow(TossBroker(buy=30_000_000_000), now=lambda: _at(9, 20), kis=FakeKis(_answer(1_500_000)))
    got = asyncio.run(flow.get("KOSPI", "1d", 5))
    assert got["records"][0]["source"] == "kis"                    # 1.5조 against Toss' 09:01 300억


def test_kis_failing_leaves_toss_and_says_so_once_a_day(caplog):
    caplog.set_level("INFO", logger="desk.market_flow")
    flow = MarketFlow(TossBroker(), now=lambda: _at(10, 0), kis=FakeKis(fail=True))

    async def scenario():
        for _ in range(3):
            got = await flow.get("KOSPI", "1d", 5, fresh=True)
            assert got["records"][0].get("source") is None and got["error"] is None
    asyncio.run(scenario())
    assert _notes(caplog) == ["kis spot flow KOSPI: failed: KisError: FHPTJ04030000 - Toss serves today's record"]


def test_a_slow_kis_does_not_hold_tosss_answer(monkeypatch):
    monkeypatch.setattr(kis_spot_flow, "TIMEOUT_SEC", 0.05)
    flow = MarketFlow(TossBroker(), now=lambda: _at(10, 0), kis=FakeKis(delay=5))

    async def scenario():
        started = asyncio.get_running_loop().time()
        got = await flow.get("KOSPI", "1d", 5)
        assert asyncio.get_running_loop().time() - started < 1
        assert got["records"][0].get("source") is None
    asyncio.run(scenario())


def test_a_brief_kis_failure_keeps_the_last_kis_record_not_tosss_older_one():
    kis = FakeKis()
    now = [_at(10, 0)]
    flow = MarketFlow(TossBroker(), now=lambda: now[0], kis=kis)

    async def scenario():
        await flow.get("KOSPI", "1d", 5, fresh=True)
        kis.fail, now[0] = True, _at(10, 1)
        held = await flow.get("KOSPI", "1d", 5, fresh=True)
        assert held["records"][0]["source"] == "kis" and held["records"][0]["held"] is True
        now[0] = _at(10, 6)                                       # past HOLD_SEC: Toss again
        later = await flow.get("KOSPI", "1d", 5, fresh=True)
        assert later["records"][0].get("source") is None
    asyncio.run(scenario())


def test_kis_failing_the_check_is_not_shown_nor_held(caplog):
    caplog.set_level("INFO", logger="desk.market_flow")
    toss = TossBroker()
    kis = FakeKis()
    now = [_at(10, 0)]
    flow = MarketFlow(toss, now=lambda: now[0], kis=kis)

    async def scenario():
        await flow.get("KOSPI", "1d", 5, fresh=True)
        kis.answer, now[0] = _answer(30_000_000), _at(10, 1)       # 30조: over 3x yesterday's 7조
        bad = await flow.get("KOSPI", "1d", 5, fresh=True)
        assert bad["records"][0].get("source") is None
        kis.fail, now[0] = True, _at(10, 2)
        after = await flow.get("KOSPI", "1d", 5, fresh=True)
        assert after["records"][0].get("source") is None           # nothing held after a failed check
    asyncio.run(scenario())
    assert any("not shown: individual buy 30000000000000 over 3x" in n for n in _notes(caplog))


def test_toss_failing_shows_kis_only_once_it_passed_a_check_today():
    toss = TossBroker()
    flow = MarketFlow(toss, now=lambda: _at(10, 0), kis=FakeKis())

    async def scenario():
        toss.fail = True
        unchecked = await flow.get("KOSPI", "1d", 5, fresh=True)
        assert unchecked["records"] == [] and unchecked["error"]   # nothing to check an unknown code against
        toss.fail = False
        await flow.get("KOSPI", "1d", 5, fresh=True)               # checked against Toss' 3조
        toss.fail = True
        got = await flow.get("KOSPI", "1d", 5, fresh=True)
        assert got["error"].startswith("RuntimeError") and "secret" not in got["error"]
        assert [r["source"] for r in got["records"]] == ["kis"]
    asyncio.run(scenario())


def test_pre_market_kis_is_used_but_not_yesterdays_totals(caplog):
    caplog.set_level("INFO", logger="desk.market_flow")
    pre = asyncio.run(MarketFlow(TossBroker(buy=20_000_000_000), now=lambda: _at(8, 30),
                                 kis=FakeKis(_answer(25_000))).get("KOSPI", "1d", 5))
    assert pre["records"][0]["source"] == "kis" and pre["session"] == "pre"
    repeat = asyncio.run(MarketFlow(TossBroker(buy=0), now=lambda: _at(8, 5),
                                    kis=FakeKis(_answer(6_900_000))).get("KOSPI", "1d", 5))
    assert repeat["records"][0].get("source") is None and repeat["records"][0]["date"] == "2026-11-04"
    quiet = asyncio.run(MarketFlow(TossBroker(buy=0), now=lambda: _at(8, 1),
                                   kis=FakeKis({"rt_cd": "0", "output": [{k: "0" for k in _row()}]})).get("KOSPI", "1d", 5))
    assert quiet["records"][0]["date"] == "2026-11-04"
    assert not any("no figures" in n for n in _notes(caplog))      # nothing traded yet is normal before 09:00


@pytest.mark.parametrize(("at", "interval"), [((16, 0), "1d"), ((7, 59), "1d"), ((10, 0), "1w")])
def test_kis_is_asked_only_from_the_pre_market_to_the_closing_snapshot_for_today(at, interval):
    kis = FakeKis()
    asyncio.run(MarketFlow(TossBroker(), now=lambda: _at(*at), kis=kis).get("KOSPI", interval, 5))
    assert kis.calls == []


def test_the_mock_brokers_sample_gets_no_real_figures():
    kis = FakeKis()
    asyncio.run(MarketFlow(MockBroker(), now=lambda: _at(10, 0), kis=kis).get("KOSPI", "1d", 5))
    assert kis.calls == []


def test_kis_figures_moving_set_the_source_time_and_toss_is_still_watched(caplog):
    caplog.set_level("INFO", logger="desk.market_flow")
    kis = FakeKis()
    toss = TossBroker()
    now = [_at(10, 0)]
    flow = MarketFlow(toss, now=lambda: now[0], kis=kis)

    async def scenario():
        await flow.get("KOSPI", "1d", 1, fresh=True)
        now[0] = _at(10, 1)
        kis.answer, toss.updated = _answer(3_500_000), "2026-11-05T10:00:30+09:00"
        got = await flow.get("KOSPI", "1d", 1, fresh=True)
        assert got["changed_at"] == "2026-11-05T10:00:00+09:00"
        assert got["freshness"]["source_updated_at"] == "2026-11-05T01:00:00+00:00"
    asyncio.run(scenario())
    lines = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("kis flow KOSPI: updatedAt - figures changed") for m in lines)
    assert any(m.startswith("toss flow KOSPI: updatedAt 10:00:30") for m in lines)   # #673 cadence still logged


def test_the_desk_loggers_info_lines_reach_one_handler():
    import logging

    from desk import log_setup
    desk = logging.getLogger("desk")
    before = (desk.level, list(desk.handlers))
    try:
        assert log_setup.configure("") == logging.INFO           # compose passes an empty value
        assert log_setup.configure("warning") == logging.WARNING
        assert log_setup.configure("nonsense") == logging.INFO
        assert sum(getattr(h, "desk_handler", False) for h in desk.handlers) == 1   # once, however often
        assert logging.getLogger("desk.market_flow").isEnabledFor(logging.INFO)
    finally:
        desk.setLevel(before[0])
        desk.handlers[:] = before[1]


@pytest.mark.parametrize("market", ["KOSPI", "KOSDAQ"])
def test_off_kis_is_not_asked_and_toss_serves_today(monkeypatch, market):
    """#711: KIS' [0403] is KRX regular-market stocks only, Toss' record wider (NXT; ETFs on KOSPI)."""
    monkeypatch.setattr(kis_spot_flow, "ENABLED", False)
    kis = FakeKis()
    got = asyncio.run(MarketFlow(TossBroker(), now=lambda: _at(10, 0), kis=kis).get(market, "1d", 5))
    assert kis.calls == []
    assert got["records"][0]["date"] == "2026-11-05" and got["records"][0].get("source") is None
