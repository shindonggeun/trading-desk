"""#604 the Korean answers on KRX's own KOSPI bars: merge, the Yahoo check, the training report, live scoring."""

from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import insert, select

from desk import day_call as dc
from desk import day_call_krx as dck
from desk import day_call_train as dct
from desk import day_calls
from desk.db import day_calls as t
from desk.db import krx_daily, krx_days
from tests.test_day_call import SERIES
from tests.test_day_calls import DAY, _candles, _kst, _model

DAY_BEFORE = date(2024, 6, 11)


def _row(day: date, o: float, h: float, lo: float, c: float) -> dict:
    """A kospi_dd_trd row as KRX writes it: strings, commas."""
    f = lambda v: f"{v:,.2f}"
    return {"BAS_DD": day.strftime("%Y%m%d"), "IDX_CLSS": "KOSPI", "IDX_NM": "코스피", "CLSPRC_IDX": f(c),
            "CMPPREVDD_IDX": "0.00", "FLUC_RT": "0.00", "OPNPRC_IDX": f(o), "HGPRC_IDX": f(h), "LWPRC_IDX": f(lo),
            "ACC_TRDVOL": "512,334,120", "ACC_TRDVAL": "1", "MKTCAP": "1"}


def _yahoo(day: date):
    return dc.on_day("^KS11", SERIES["^KS11"], day)


def test_a_krx_row_is_a_bar_dated_like_yahoos():
    b = dck.bar_of(DAY, _row(DAY, 2700.5, 2720.0, 2690.25, 2711.75))
    assert (b.open, b.high, b.low, b.close, b.volume) == (2700.5, 2720.0, 2690.25, 2711.75, 512334120)
    assert dc.session_day("^KS11", b) == DAY and b.at == "2024-06-11T15:00:00+00:00"
    assert dck.bar_of(DAY, {**_row(DAY, 1, 1, 1, 1), "OPNPRC_IDX": "-"}) is None


def test_krx_takes_the_days_it_has_and_every_disagreement_is_listed():
    y = _yahoo(DAY)
    rows = {DAY: _row(DAY, y.open, y.high, y.low, y.close * 1.002),               # close 0.2% apart
            DAY_BEFORE: _row(DAY_BEFORE, *(getattr(_yahoo(DAY_BEFORE), f) for f in ("open", "high", "low", "close")))}
    merged, check = dck.merge(SERIES["^KS11"], rows)
    assert len(merged) == len(SERIES["^KS11"])
    assert dc.on_day("^KS11", merged, DAY).close == pytest.approx(y.close * 1.002, abs=0.006)
    assert check["source"] == "krx" and check["krx_days"] == 2
    assert [d["day"] for d in check["diffs"]] == [DAY.isoformat()]
    assert check["diffs"][0]["pct"]["close"] == pytest.approx(0.2, abs=0.001) and check["diffs"][0]["pct"]["open"] == 0
    # a session only KRX has (Yahoo's bar missing, as ^KS11 sometimes is) comes in and is listed
    gone = [b for b in SERIES["^KS11"] if dc.session_day("^KS11", b) != DAY]
    merged, check = dck.merge(gone, rows)
    assert dc.on_day("^KS11", merged, DAY) is not None and check["only_krx"] == [DAY.isoformat()]
    assert dck.merge(SERIES["^KS11"], {})[1]["source"] == "yahoo"


def test_the_answer_is_krxs_open_and_close():
    y = _yahoo(DAY)
    series = dict(SERIES)
    dck.apply(series, {DAY: _row(DAY, y.open - 5, y.high, y.low - 5, y.close + 5)})
    a = dc.label("kr", DAY, series, "oc", _kst(DAY, 16))
    assert (a["open"], a["close"]) == (pytest.approx(y.open - 5, abs=0.006), pytest.approx(y.close + 5, abs=0.006))
    assert dc.label("kr", DAY, SERIES, "oc", _kst(DAY, 16))["open"] == y.open


def test_the_training_report_says_whose_answers_they_are():
    assert dct.answers(None)["kospi"] == "yahoo"
    _m, check = dck.merge(SERIES["^KS11"], {DAY: _row(DAY, 1, 2, 1, 2)})
    a = dct.answers(check)
    assert a["kospi"] == "krx" and a["krx_days"] == 1 and a["diff_days"] == 1 and a["diffs"][0]["day"] == "2024-06-12"
    assert dct.TRAIN_FROM == date(2015, 1, 1) and "day_call_krx.py" in Path(dct.__file__).read_text(encoding="utf-8")


def _store_krx(engine, day: date, row: dict) -> None:
    with engine.begin() as conn:
        conn.execute(insert(krx_daily).values(api="kospi_dd_trd", bas_dd=day, code="코스피", payload=row,
                                              fetched_at=_kst(day + timedelta(days=1), 8, 30)))
        conn.execute(insert(krx_days).values(api="kospi_dd_trd", bas_dd=day, rows=1,
                                             fetched_at=_kst(day + timedelta(days=1), 8, 30)))


def test_export_and_load_round_trip(db_url, tmp_path):
    from desk.db import make_engine, migrate

    migrate(db_url)
    engine = make_engine(db_url)
    try:
        _store_krx(engine, DAY, _row(DAY, 1, 2, 1, 2))
        assert dck.export(engine, tmp_path / "k.json") == 1
        assert dck.load_file(tmp_path / "k.json") == {DAY: _row(DAY, 1, 2, 1, 2)}
        (tmp_path / "x.json").write_text('{"api": "fut_bydd_trd", "code": "x", "rows": {}}', encoding="utf-8")
        with pytest.raises(ValueError):
            dck.load_file(tmp_path / "x.json")
    finally:
        engine.dispose()


@pytest.fixture
def desk(db_url):
    from desk.db import make_engine, migrate

    migrate(db_url)
    engine = make_engine(db_url)
    yield day_calls.DayCalls(engine, _candles, models={"kr": _model("kr"), "us": _model("us")}, reports={})
    engine.dispose()


def _rows(engine) -> dict:
    with engine.connect() as conn:
        return {(r["round"], r["call"]): r for r in conn.execute(select(t).where(t.c.market == "kr")).mappings()}


def test_a_korean_call_scored_on_yahoo_is_scored_again_on_krx_next_morning(desk):
    desk.run(_kst(DAY, 7, 40))
    assert desk.score(_kst(DAY, 16, 5)) == 2
    first = _rows(desk.engine)[(1, "open")]
    assert first["outcome"]["answer_source"] == "yahoo"
    assert desk.recheck_krx(_kst(DAY, 16, 10)) == 0                        # KRX has nothing yet
    y = _yahoo(DAY)
    _store_krx(desk.engine, DAY, _row(DAY, y.open, y.high, y.low * 0.98, y.low * 0.99))   # closes far lower
    assert desk.recheck_krx(_kst(DAY + timedelta(days=1), 8, 40)) == 2
    again = _rows(desk.engine)[(1, "open")]
    assert again["outcome"]["answer_source"] == "krx"
    assert again["outcome"]["close"] == pytest.approx(y.low * 0.99, abs=0.006)
    assert again["outcome"]["yahoo_answer"]["close"] == first["outcome"]["close"]
    assert desk.recheck_krx(_kst(DAY + timedelta(days=1), 9, 30)) == 0     # once


def test_a_session_krx_already_has_is_scored_on_it_at_once(desk):
    y = _yahoo(DAY)
    desk.run(_kst(DAY, 7, 40))
    _store_krx(desk.engine, DAY, _row(DAY, y.open, y.high, y.low, y.close))   # a late first scoring
    desk.score(_kst(DAY + timedelta(days=1), 9, 0))
    rows = _rows(desk.engine)
    assert {r["outcome"]["answer_source"] for r in rows.values()} == {"krx"}
    assert "yahoo_answer" not in rows[(1, "open")]["outcome"]


def test_the_features_never_read_krx_bars():
    y = _yahoo(DAY_BEFORE)
    series = dict(SERIES)
    check = dck.apply(series, {DAY_BEFORE: _row(DAY_BEFORE, y.open * 1.01, y.high * 1.01, y.low, y.close * 0.98)})
    assert check["days"] == {DAY_BEFORE} and series["^KS11"] is SERIES["^KS11"]
    for market, round_ in (("kr", 1), ("kr", 2), ("us", 1)):
        assert dc.features(market, round_, DAY, series)[0] == dc.features(market, round_, DAY, SERIES)[0]
    assert dc.label("kr", DAY_BEFORE, series, "cc", _kst(DAY, 7))["close"] == pytest.approx(y.close * 0.98, abs=0.006)
    no_yahoo = {**SERIES, "^KS11": []}
    dck.apply(no_yahoo, {DAY_BEFORE: _row(DAY_BEFORE, 1, 2, 1, 2)})
    with pytest.raises(dc.NoData):                    # Yahoo down: no call, as before - not a call a day late
        dc.features("kr", 1, DAY, no_yahoo)


def test_a_day_without_krx_prices_or_with_a_zero_yahoo_open_does_not_break_anything():
    series = dict(SERIES)
    check = dck.apply(series, {DAY: {**_row(DAY, 1, 1, 1, 1), "CLSPRC_IDX": "-"}})
    assert check["days"] == set() and "^KS11" + dc.ANSWER not in series      # nothing KRX answers
    y = _yahoo(DAY)
    zero = [b if dc.session_day("^KS11", b) != DAY else type(b)(**{**vars(b), "open": 0.0}) for b in SERIES["^KS11"]]
    _m, check = dck.merge(zero, {DAY: _row(DAY, y.open, y.high, y.low, y.close)})
    assert check["diffs"][0]["pct"]["open"] is None


def test_a_hit_that_flips_keeps_yahoos_answer_whatever_the_gap(desk):
    desk.run(_kst(DAY, 7, 40))
    desk.score(_kst(DAY, 16, 5))
    first = _rows(desk.engine)[(1, "prev_close")]
    o = first["outcome"]
    # KRX's close a hair on the other side of the previous close: the direction flips
    start, close = o["open"], o["close"]
    flipped = start - (close - start) * 0.01 if close != start else start * 0.9999
    y = _yahoo(DAY)
    _store_krx(desk.engine, DAY, _row(DAY, y.open, max(y.high, flipped), min(y.low, flipped), flipped))
    desk.recheck_krx(_kst(DAY + timedelta(days=1), 8, 40))
    again = _rows(desk.engine)[(1, "prev_close")]["outcome"]
    assert again["answer_source"] == "krx" and again["raw_hit_direction"] != o["raw_hit_direction"]
    assert again["yahoo_answer"]["close"] == close


def test_an_intraday_round_is_scored_again_on_krx_from_its_own_anchor(desk):
    """#619's to_close rows are rechecked from their anchor, not as a previous-close call."""
    from datetime import UTC, datetime

    y = _yahoo(DAY)
    anchor = (y.open + y.close) / 2
    probs = {"strong_long": 5.0, "weak_long": 25.0, "flat": 40.0, "weak_short": 25.0, "strong_short": 5.0}
    with desk.engine.begin() as conn:
        conn.execute(insert(t).values(
            market="kr", trade_date=DAY, round=6, call="to_close", model="to_close", model_version="dr-x",
            source="live", called_at=datetime.now(UTC), cutoff_at=datetime.now(UTC), stage="flat", verdict="hold",
            probs=probs, reasons=[], edges_pct={"flat": 0.1, "strong": 0.4}, features={"anchor": anchor}))
    assert desk.score(_kst(DAY, 16, 5)) == 1
    assert _rows(desk.engine)[(6, "to_close")]["outcome"]["open"] == pytest.approx(anchor)
    _store_krx(desk.engine, DAY, _row(DAY, y.open, y.high, y.low * 0.98, y.low * 0.99))
    assert desk.recheck_krx(_kst(DAY + timedelta(days=1), 8, 40)) == 1
    again = _rows(desk.engine)[(6, "to_close")]["outcome"]
    assert again["answer_source"] == "krx" and again["open"] == pytest.approx(anchor)
    assert again["close"] == pytest.approx(y.low * 0.99, abs=0.006)
