"""#677 KRX program trading from KIS' summary by time (desk.program_trade), kept in the futures snapshots."""

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta

from desk import program_trade
from desk.db import make_engine, migrate
from desk.futures_history import FuturesHistory


def _kst(h, m, s=0, day=10):            # Tuesday 2026-11-10
    return datetime(2026, 11, day, h, m, s, tzinfo=UTC) - timedelta(hours=9)


def _row(hms, arbt, nabt, whol=None):
    whol = arbt + nabt if whol is None else whol
    return {"bsop_hour": hms, "arbt_smtn_ntby_tr_pbmn": str(arbt), "nabt_smtn_ntby_tr_pbmn": str(nabt),
            "whol_smtn_ntby_tr_pbmn": str(whol), "arbt_smtn_ntby_qty": "1"}


def _answer(*rows):
    return {"rt_cd": "0", "output1": list(rows)}


def _fresh():
    program_trade._shown.clear()
    program_trade._trouble.clear()


def test_the_newest_row_of_today_in_100_million_won():
    answer = _answer(_row("101000", -1200, 35000), _row("101100", -1500, 41000), _row("100900", 0, 0))
    got = program_trade.latest(answer, _kst(10, 11, 20))
    assert got == {"at": "10:11:00", "arbitrage": -15.0, "non_arbitrage": 410.0, "total": 395.0}


def test_yesterdays_close_is_not_todays_figure():
    # just after the bell KIS still answers yesterday's last half hour (15:00 ~ 17:00, the close repeated)
    yesterday = _answer(_row("150000", 10, 20), _row("153000", 30, 40), _row("170000", 30, 40))
    assert program_trade.latest(yesterday, _kst(9, 1)) is None
    mixed = _answer(_row("170000", 30, 40), _row("090100", 1, 2))
    assert program_trade.latest(mixed, _kst(9, 1, 30))["total"] == 0.03
    # after 15:30 KIS repeats today's close up to 17:00: any of those is today's
    closing = _answer(_row("152900", 5, 6), _row("153000", 7, 8), _row("170000", 7, 8))
    assert program_trade.latest(closing, _kst(15, 46))["at"] == "17:00:00"
    # the 15:30 edge: just before it a later row is still not ours, from it the close repeated is
    assert program_trade.latest(closing, _kst(15, 29, 30))["at"] == "15:30:00"      # within AHEAD_SEC
    assert program_trade.latest(closing, _kst(15, 28))["at"] == "15:29:00"
    assert program_trade.latest(closing, _kst(15, 30))["at"] == "17:00:00"


def test_empty_rows_and_odd_answers_are_nothing():
    assert program_trade.latest({"output1": [{"bsop_hour": "101000", "arbt_smtn_ntby_tr_pbmn": "",
                                              "nabt_smtn_ntby_tr_pbmn": " ", "whol_smtn_ntby_tr_pbmn": ""}]},
                                _kst(10, 11)) is None
    assert program_trade.latest({"output1": [{"bsop_hour": "1010"}]}, _kst(10, 11)) is None
    assert program_trade.latest({}, _kst(10, 11)) is None and program_trade.latest(None, _kst(10, 11)) is None
    # ``output`` instead of ``output1``, one object instead of a list
    assert program_trade.latest({"output": _row("101000", 1, 2)}, _kst(10, 11))["total"] == 0.03


def test_a_unit_or_shape_other_than_assumed_is_dropped(caplog):
    _fresh()
    won = _answer(_row("101000", -1_200_000_000, 3_500_000_000))        # won, not million won
    with caplog.at_level(logging.INFO, logger="desk.program_trade"):
        assert program_trade.read(won, "kospi", _kst(10, 11)) is None
        assert program_trade.read(won, "kospi", _kst(10, 12)) is None
    assert caplog.text.count("unit") == 1                               # the same trouble: one warning
    # the shape is logged once: the answer's keys, the row's keys, its time and figures (to match with HTS)
    assert "answer keys ['output1', 'rt_cd']" in caplog.text and "101000" in caplog.text
    assert "-1200000000" in caplog.text
    odd = _answer(_row("101000", 100, 200, whol=900))
    assert program_trade.read(odd, "kospi", _kst(10, 11)) is None
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="desk.program_trade"):
        assert program_trade.read(_answer(_row("101000", 100, 200)), "kospi", _kst(10, 11))["total"] == 3.0
    assert "program trade kospi back" in caplog.text
    # known gap: a thousand-won answer under CEILING_100M passes (the shape log is how it is caught)
    small = _answer(_row("091000", 2_000_000, 1_500_000))
    assert program_trade.read(small, "kospi", _kst(9, 11))["total"] == 35_000.0


def test_rows_under_another_key_are_logged_not_silent(caplog):
    _fresh()
    with caplog.at_level(logging.INFO, logger="desk.program_trade"):
        assert program_trade.read({"rt_cd": "0", "output2": [_row("101000", 1, 2)]}, "kosdaq", _kst(10, 11)) is None
    assert "answer keys ['output2', 'rt_cd'], 0 row(s)" in caplog.text and "kosdaq answered no rows" in caplog.text


def test_fetch_asks_both_markets_on_krx_and_keeps_one_when_the_other_fails(caplog):
    asked = []

    class Rest:
        async def get(self, path, tr_id, params):
            asked.append((path, tr_id, params["FID_COND_MRKT_DIV_CODE"], params["FID_MRKT_CLS_CODE"]))
            if params["FID_MRKT_CLS_CODE"] == "Q":
                raise RuntimeError("FHPPG04600101: HTTP 500")
            return _answer(_row("101000", -1200, 35000))

    _fresh()
    with caplog.at_level(logging.INFO, logger="desk.program_trade"):
        got = asyncio.run(program_trade.fetch(Rest(), _kst(10, 11)))
    assert sorted(asked) == [(program_trade.PATH, "FHPPG04600101", "J", "K"), (program_trade.PATH, "FHPPG04600101", "J", "Q")]
    assert got == {"kospi": {"at": "10:10:00", "arbitrage": -12.0, "non_arbitrage": 350.0, "total": 338.0},
                   "kosdaq": None}
    assert "program trade kospi: answer keys" in caplog.text and "program trade kosdaq failed" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="desk.program_trade"):
        asyncio.run(program_trade.fetch(Rest(), _kst(10, 12)))
    assert caplog.text == ""                            # the shape once, the same failure once


def test_a_slow_kis_is_left_behind_but_not_cut_off(monkeypatch):
    _fresh()
    done = []

    class Rest:
        async def get(self, path, tr_id, params):
            await asyncio.sleep(0.2)                    # e.g. a token refresh inside the call
            done.append(params["FID_MRKT_CLS_CODE"])
            return _answer(_row("101000", 1, 2))

    monkeypatch.setattr(program_trade, "TIMEOUT_SEC", 0.05)

    async def run():
        got = await program_trade.fetch(Rest(), _kst(10, 11))
        await asyncio.sleep(0.3)
        return got

    assert asyncio.run(run()) == {"kospi": None, "kosdaq": None}
    assert sorted(done) == ["K", "Q"]                   # shielded: the calls themselves finished


def test_hours_start_at_the_spot_bell():
    assert not program_trade.hours(_kst(8, 59)) and program_trade.hours(_kst(9, 0))
    assert not program_trade.hours(_kst(10, 0, day=14))                 # Saturday
    assert not program_trade.hours(_kst(10, 0), is_open=lambda market, d: False)   # a KRX holiday on a weekday


def _view():
    return {"front": {"code": "A01612"}, "price": {"code": "A01612", "price": 702.35, "open_interest": 245000},
            "investors": {c: {"net_contracts": 1.0, "net_value_mn": 175.0}
                          for c in ("foreign", "individual", "institution")}}


def test_the_snapshots_keep_it_and_the_day_reads_the_newest(db_url):
    migrate(db_url)
    engine = make_engine(db_url)
    history = FuturesHistory(engine)
    day = date(2026, 11, 10)
    assert history.program(day) == {"kospi": None, "kosdaq": None}
    assert history.record(_view(), _kst(8, 50))                         # before the bell: none
    p1 = {"kospi": {"at": "10:00:00", "arbitrage": -10.0, "non_arbitrage": 300.0, "total": 290.0}, "kosdaq": None}
    p2 = {"kospi": {"at": "10:01:00", "arbitrage": -11.0, "non_arbitrage": 310.0, "total": 299.0},
          "kosdaq": {"at": "10:01:00", "arbitrage": 0.0, "non_arbitrage": -20.0, "total": -20.0}}
    assert history.record(_view(), _kst(10, 0), None, p1)
    assert history.record(_view(), _kst(10, 1), None, p2)
    assert history.record(_view(), _kst(10, 2), None, {"kospi": None, "kosdaq": None})   # KIS failed: kept as is
    points = history.intraday(day)
    assert points[0]["program"]["kospi"]["total"] is None and points[1]["program"]["kospi"]["total"] == 290.0
    got = history.program(day)
    assert got["kospi"]["at"].startswith("2026-11-10T01:01") and got["kospi"]["total"] == 299.0
    assert got["kosdaq"] == {"at": got["kospi"]["at"], "arbitrage": 0.0, "non_arbitrage": -20.0, "total": -20.0}
    # the newest snapshot without KOSDAQ (its call timed out): KOSDAQ keeps the earlier figures
    p3 = {"kospi": {"at": "10:03:00", "arbitrage": -12.0, "non_arbitrage": 320.0, "total": 308.0}, "kosdaq": None}
    assert history.record(_view(), _kst(10, 3), None, p3)
    got = history.program(day)
    assert got["kospi"]["total"] == 308.0 and got["kospi"]["at"].startswith("2026-11-10T01:03")
    assert got["kosdaq"]["total"] == -20.0 and got["kosdaq"]["at"].startswith("2026-11-10T01:01")
    engine.dispose()
