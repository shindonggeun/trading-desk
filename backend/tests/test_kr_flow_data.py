import asyncio
import json
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import event, insert

from desk import kr_flow_data, kr_flow_import
from desk.db import krx_daily, krx_days, make_engine
from desk.krx_etp import isin

MON = date(2026, 11, 23)
TUE = date(2026, 11, 24)
NOW = datetime(2026, 11, 24, 10, tzinfo=UTC)
API = kr_flow_data.STOCK_APIS["kospi"]


@pytest.fixture
def engine(db_url):
    engine = make_engine(db_url)
    krx_days.create(engine)
    krx_daily.create(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def _stock(day, code, amount, market="KOSPI"):
    return {"api": API, "bas_dd": day, "code": code, "fetched_at": NOW,
            "payload": {"BAS_DD": day.strftime("%Y%m%d"), "ISU_CD": code, "ISU_NM": "합성 종목",
                        "MKT_NM": market, "ACC_TRDVAL": amount}}


def test_rank_comes_from_one_complete_market_publication_and_history_is_batched(engine):
    rows = [_stock(MON, isin("005930"), "2,000"), _stock(MON, "000660", "0"),
            _stock(TUE, "005930", "100"), _stock(TUE, "000660", "200", market="NXT")]
    with engine.begin() as conn:
        conn.execute(insert(krx_days), [{"api": API, "bas_dd": day, "rows": 2, "fetched_at": NOW}
                                       for day in (MON, TUE)])
        conn.execute(insert(krx_daily), rows)

    statements = []

    @event.listens_for(engine, "before_cursor_execute")
    def count(_conn, _cursor, statement, *_):
        statements.append(statement)

    observed = kr_flow_data.publication(engine, "kospi", (MON, TUE), ["005930", "000660"])
    assert [(r.as_of, r.source) for r in observed.rankings] == [(MON, "krx_open_api")]
    assert {(r.as_of, r.ticker): r.value_krw for r in observed.turnover} == {
        (MON, "005930"): 2000, (MON, "000660"): 0, (TUE, "005930"): 100,
    }
    assert len(statements) == 4
    absent = kr_flow_data.publication(engine, "kosdaq", (MON, TUE), ["005930"])
    assert absent.turnover is None and absent.rankings is None


def test_incomplete_count_and_invalid_value_cannot_rank(engine):
    with engine.begin() as conn:
        conn.execute(insert(krx_days), {"api": API, "bas_dd": TUE, "rows": 2, "fetched_at": NOW})
        conn.execute(insert(krx_daily), _stock(TUE, "005930", "-"))
    result = kr_flow_data.publication(engine, "kospi", (TUE,), ["005930"])
    assert result.rankings == []
    assert result.turnover[0].value_krw is None


def test_older_verified_day_survives_two_invalid_markers(engine):
    fri = date(2026, 11, 20)
    markers = [
        {"api": "stk_bydd_trd", "bas_dd": TUE, "rows": 1, "fetched_at": NOW},
        {"api": API, "bas_dd": TUE, "rows": 0, "fetched_at": NOW},
        {"api": API, "bas_dd": MON, "rows": 2, "fetched_at": NOW},
        {"api": API, "bas_dd": fri, "rows": 1, "fetched_at": NOW},
    ]
    with engine.begin() as conn:
        conn.execute(insert(krx_days), markers)
        conn.execute(insert(krx_daily), _stock(fri, "005930", "200"))
    observed = kr_flow_data.publication(engine, "kospi", (fri, MON, TUE), [])
    assert [r.as_of for r in observed.rankings] == [fri]


def test_import_rejects_dropped_or_duplicate_rows_and_keeps_the_last_good_publication(engine):
    original = _stock(TUE, "005930", "1200")["payload"]
    assert kr_flow_import.save(engine, "kospi", TUE, {"OutBlock_1": [original]}, NOW) == 1
    for bad in (
        {**original, "ISU_CD": None},
        {**original, "ISU_CD": "A12345", "ACC_TRDVAL": "9000"},
        {**original, "ACC_TRDVAL": "-"},
        {**original, "BAS_DD": "20261123"},
        {**original, "MKT_NM": "NXT"},
        {**original, "ISU_CD": isin("005930")},
    ):
        with pytest.raises(ValueError, match="row 2"):
            kr_flow_import.save(engine, "kospi", TUE, {"OutBlock_1": [original, bad]}, NOW)
    observed = kr_flow_data.publication(engine, "kospi", (TUE,), [])
    assert [r.value_krw for r in observed.turnover] == [1200]
    assert [r.as_of for r in observed.rankings] == [TUE]
    assert kr_flow_import.save(engine, "kospi", TUE, {"OutBlock_1": [{**original, "ACC_TRDVAL": "0"}]}, NOW) == 1
    assert kr_flow_data.publication(engine, "kospi", (TUE,), []).turnover[0].value_krw == 0


def test_import_cli_defaults_to_validation_without_a_database(tmp_path, monkeypatch, capsys):
    def unexpected_engine(*_args):
        raise AssertionError("validation mode must not connect to a database")

    monkeypatch.setattr(kr_flow_import, "make_engine", unexpected_engine)
    path = tmp_path / "approved.json"
    path.write_text(json.dumps({"OutBlock_1": [_stock(MON, "005930", "0")["payload"]]}), encoding="utf-8")
    assert kr_flow_import.main(["--market", "kospi", "--date", MON.isoformat(), "--input", str(path)]) == 0
    assert "validated kospi 2026-11-23: 1 rows" in capsys.readouterr().out


def test_index_snapshot_reads_only_the_requested_krx_series_and_dates(engine):
    def index(day, code, value):
        return {"api": "kospi_dd_trd", "bas_dd": day, "code": code, "fetched_at": NOW,
                "payload": {"CLSPRC_IDX": value}}

    with engine.begin() as conn:
        conn.execute(insert(krx_daily), [index(MON, "코스피", "2,300.25"),
                                         index(TUE, "코스피 200", "300")])
    assert kr_flow_data.index_snapshot(engine, "kospi", (MON, TUE)) == {
        "as_of": MON.isoformat(), "source": "krx_open_api", "published_at": None,
        "unit": "index_points", "value": 2300.25,
    }
    assert kr_flow_data.index_snapshot(engine, "kosdaq", (MON, TUE)) is None


def test_market_summary_reuses_sealed_page_and_skips_provisional_records(monkeypatch):
    def flow(day, provisional):
        return {"date": day.isoformat(), "updated_at": None, "in_progress": provisional,
                "provisional": provisional, "investors": {"foreigner": {"buy": 10, "sell": 2, "net": 8}},
                "institution_breakdown": {}}

    async def get(namespace, key):
        assert (namespace, key) == ("market_flow", ("KOSPI", "1d"))
        return ({"value": {"market": "KOSPI", "interval": "1d", "source": "toss", "error": None,
                           "supported": True, "index": None, "fetched_at": NOW.isoformat(),
                           "records": [flow(TUE, True), flow(MON, False)]}, "ttl": 30.0}, 90.0, 90.0)

    monkeypatch.setattr(kr_flow_data.shared_cache, "aget", get)
    result = asyncio.run(kr_flow_data.market_summary("kospi", (MON, TUE), NOW, index=None))
    assert result == {"scope": "market", "status": "partial", "index": None,
                      "flow": {"as_of": MON.isoformat(), "source": "toss", "published_at": None, "unit": "KRW",
                               "investors": {"foreigner": {"buy": 10, "sell": 2, "net": 8}}}}
