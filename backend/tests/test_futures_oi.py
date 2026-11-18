"""#603 KOSPI200 futures open interest · basis · put/call from the KRX rows in krx_daily. The rows are
hand-made in KRX's documented shape (docs/krx.md) - no KRX call, no key."""

import logging
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, insert, select

from desk import cli, futures_oi
from desk.db import futures_oi_daily, krx_daily, make_engine, migrate

AT = datetime(2026, 12, 30, 0, 0, tzinfo=UTC)


def fut(day: date, month: str, oi: float, close: float = 560.0, spot: float = 558.5, prod: str = "코스피200 선물",
        mkt: str = "정규") -> dict:
    return {"BAS_DD": day.strftime("%Y%m%d"), "PROD_NM": prod, "MKT_NM": mkt, "ISU_CD": f"KR4101{month[2:]}0000",
            "ISU_NM": f"코스피200 F {month}", "TDD_CLSPRC": f"{close:,.2f}", "SETL_PRC": f"{close + 0.05:,.2f}",
            "SPOT_PRC": f"{spot:,.2f}", "ACC_TRDVOL": "201,334", "ACC_OPNINT_QTY": f"{oi:,.0f}"}


def opt(day: date, right: str, month: str, strike: float, oi: float, iv: float, prod: str = "코스피200 옵션") -> dict:
    tag = "C" if right == "CALL" else "P"
    return {"BAS_DD": day.strftime("%Y%m%d"), "PROD_NM": prod, "RGHT_TP_NM": right,
            "ISU_CD": f"KR4{tag}{month}{strike:.0f}", "ISU_NM": f"코스피200 {tag} {month} {strike:.1f}",
            "TDD_CLSPRC": "9.85", "IMP_VOLT": f"{iv:.2f}", "ACC_OPNINT_QTY": f"{oi:,.0f}"}


@pytest.fixture
def engine(db_url):
    migrate(db_url)
    eng = make_engine(db_url)
    yield eng
    eng.dispose()


def _store(engine, api: str, day: date, rows: list[dict]) -> None:
    with engine.begin() as conn:
        conn.execute(insert(krx_daily), [{"api": api, "bas_dd": day, "code": f"{r['ISU_CD']}|{i}", "payload": r,
                                          "fetched_at": AT} for i, r in enumerate(rows)])


def test_the_front_month_holds_through_its_expiry_day_and_rolls_the_day_after():
    assert futures_oi.expiry_of(2026, 12) == date(2026, 12, 10)          # the second Thursday
    futs = futures_oi.futures([fut(date(2026, 12, 10), "202612", 100_000), fut(date(2026, 12, 10), "202603", 1),
                               fut(date(2026, 12, 10), "202703", 200_000)])
    assert futures_oi.front(futs, date(2026, 12, 10))["month"] == (2026, 12)
    assert futures_oi.front(futs, date(2026, 12, 11))["month"] == (2027, 3)
    # the mini, the night session and a non-quarterly month are not the KOSPI200 quarterly futures
    assert futures_oi.futures([fut(date(2026, 12, 10), "202612", 1, prod="미니코스피200 선물"),
                               fut(date(2026, 12, 10), "202612", 1, mkt="야간"),
                               fut(date(2026, 12, 10), "202701", 1)]) == []
    # a spread names two months: not a contract; a repeated month keeps the larger open interest
    d = date(2026, 12, 10)
    spread = {**fut(d, "202612", 5), "ISU_NM": "코스피200 SP 202612-202703", "ISU_CD": "KR4401"}
    assert [f["oi"] for f in futures_oi.futures([spread, fut(d, "202612", 100_000)])] == [100_000]
    twice = futures_oi.futures([fut(d, "202612", 7), fut(d, "202612", 100_000), fut(d, "202612", 9)])
    assert [f["oi"] for f in twice] == [100_000]
    # spaces in the product name do not matter
    assert len(futures_oi.futures([fut(date(2026, 12, 10), "202612", 1, prod="코스피200선물")])) == 1


def test_a_roll_compares_each_contract_with_itself_and_the_total_does_not_break(engine):
    wed, thu, fri = date(2026, 12, 9), date(2026, 12, 10), date(2026, 12, 11)
    _store(engine, "fut_bydd_trd", wed, [fut(wed, "202612", 150_000), fut(wed, "202703", 120_000)])
    _store(engine, "fut_bydd_trd", thu, [fut(thu, "202612", 90_000), fut(thu, "202703", 185_000)])
    _store(engine, "fut_bydd_trd", fri, [fut(fri, "202703", 270_000), fut(fri, "202706", 2_000)])
    assert futures_oi.FuturesOi(engine, clock=lambda: AT).derive(date(2026, 12, 9), date(2026, 12, 11)) == 3
    by = {p["date"]: p for p in futures_oi.FuturesOi(engine).history(10)}
    assert by["2026-12-10"]["expiry"] == "2026-12-10" and by["2026-12-10"]["oi_change"] == -60_000
    assert by["2026-12-10"]["rolling"] is True
    # Friday's front is March: 270k against March's own 185k, not against December's 90k
    assert by["2026-12-11"]["expiry"] == "2027-03-11" and by["2026-12-11"]["oi_change"] == 85_000
    # December's last 90k leaving the list is not a fall: only March, listed both days, counts
    assert by["2026-12-11"]["oi_total"] == 272_000 and by["2026-12-11"]["oi_total_change"] == 85_000
    assert by["2026-12-10"]["oi_total_change"] == (90_000 + 185_000) - (150_000 + 120_000)
    assert by["2026-12-11"]["rolling"] is False
    # the first day has no previous business day stored: no change rather than a made-up one
    assert by["2026-12-09"]["oi_change"] is None and by["2026-12-09"]["oi_total_change"] is None
    assert by["2026-12-09"]["basis"] == 1.5 and by["2026-12-09"]["settle"] == 560.05


def test_a_holiday_is_skipped_but_a_missing_business_day_leaves_the_change_empty(engine):
    thu, mon = date(2026, 12, 24), date(2026, 12, 28)       # Christmas (Fri) closed, then the weekend
    wed = date(2026, 12, 30)                                # Tuesday 29th not collected (31st: KRX year-end close)
    for d, oi in ((thu, 200_000), (mon, 210_000), (wed, 220_000)):
        _store(engine, "fut_bydd_trd", d, [fut(d, "202703", oi)])
    futures_oi.FuturesOi(engine, clock=lambda: AT).derive(date(2026, 12, 24), date(2026, 12, 31))
    by = {p["date"]: p for p in futures_oi.FuturesOi(engine).history(10)}
    assert by["2026-12-28"]["oi_change"] == 10_000           # against Thursday the 24th
    assert by["2026-12-30"]["oi_change"] is None             # the 29th is missing: not two days as one
    assert set(by) == {"2026-12-24", "2026-12-28", "2026-12-30"}


def test_put_call_and_the_atm_iv_skip_weeklies_and_a_month_about_to_expire(engine):
    day = date(2026, 12, 7)                                  # December expires on the 10th: 3 days left
    _store(engine, "fut_bydd_trd", day, [fut(day, "202612", 100_000, spot=552.0)])
    _store(engine, "opt_bydd_trd", day, [
        opt(day, "CALL", "202612", 550, 40_000, 30.0), opt(day, "PUT", "202612", 550, 50_000, 31.0),
        opt(day, "CALL", "202701", 550, 10_000, 20.0), opt(day, "PUT", "202701", 550, 12_000, 22.0),
        opt(day, "CALL", "202701", 555, 8_000, 19.0), opt(day, "PUT", "202701", 555, 9_000, 21.5),
        opt(day, "PUT", "202612", 550, 999_999, 50.0, prod="코스피200 위클리 옵션(목)"),
    ])
    futures_oi.FuturesOi(engine, clock=lambda: AT).derive(day, day)
    p = futures_oi.FuturesOi(engine).history(1)[0]
    assert p["pc_oi_ratio"] == round(71_000 / 58_000, 4)     # the weekly put is not counted
    # January (not December, 3 days left) at 550, nearest 552
    assert p["atm_iv"] == 21.0 and p["atm_iv_expiry"] == "2027-01-14"


def test_a_rerun_replaces_the_days_rows(engine):
    d = date(2026, 12, 9)
    _store(engine, "fut_bydd_trd", d, [fut(d, "202612", 150_000)])
    oi = futures_oi.FuturesOi(engine, clock=lambda: AT)
    assert oi.derive(d, d) == 1 and oi.derive(d, d) == 1
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(futures_oi_daily)).scalar() == 1
    assert oi.derive(date(2026, 12, 12), date(2026, 12, 13)) == 0   # a weekend: nothing


def test_features_use_only_days_before_the_call(engine):
    days = futures_oi.open_days(date(2026, 11, 2), date(2026, 12, 9))
    for i, d in enumerate(days):
        _store(engine, "fut_bydd_trd", d, [fut(d, "202612", 100_000 + 1_000 * i + (5_000 if i % 2 else 0))])
    oi = futures_oi.FuturesOi(engine, clock=lambda: AT)
    oi.derive(days[0], days[-1])
    # more days than one chunk: the previous business day across a chunk boundary is still read
    assert len(days) > futures_oi.CHUNK_DAYS
    assert all(p["oi_change"] is not None for p in oi.history(100)[:-1])
    f = oi.features_before(date(2026, 12, 9))
    assert f["date"] == "2026-12-08"                          # not the 9th's own row (published on the 10th)
    assert f["oi_total_change"] is not None and f["oi_total_change_z"] is not None
    assert oi.features_before(days[0]) is None


def test_kis_and_krx_are_compared_for_the_same_contract_only(engine, caplog):
    d = date(2026, 12, 9)
    _store(engine, "fut_bydd_trd", d, [fut(d, "202612", 150_000, close=560.0, spot=558.5)])
    oi = futures_oi.FuturesOi(engine, clock=lambda: AT)
    oi.derive(d, d)
    near = {"date": "2026-12-09", "code": "A01612", "open_interest": 150_250, "market_basis": 1.4}
    with caplog.at_level(logging.WARNING, logger="desk.futures_oi"):
        assert oi.check([near]) == []
        assert oi.check([{**near, "open_interest": 152_000}]) == ["2026-12-09"]
        assert oi.check([{**near, "market_basis": 2.4}]) == ["2026-12-09"]
        assert oi.check([{**near, "code": "A01703", "open_interest": 1}]) == []   # another contract
    assert "open interest KIS 152000 vs KRX 150000" in caplog.text
    assert futures_oi.kis_month("A01612", d) == (2026, 12) and futures_oi.kis_month("A01703", d) == (2027, 3)


def test_the_api_needs_a_session_and_says_why_it_is_empty(engine, db_url):
    from tests.test_api import OWNER, PASSWORD, _client

    with _client(db_url, login=False) as client:
        assert client.get("/api/market/futures/oi-history").status_code == 401
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        empty = client.get("/api/market/futures/oi-history").json()
        assert empty["items"] == [] and empty["reason"] in ("no_key", "no_history") and empty["source"] == "krx"
        d = date(2026, 12, 9)
        _store(engine, "fut_bydd_trd", d, [fut(d, "202612", 150_000)])
        futures_oi.FuturesOi(engine, clock=lambda: AT).derive(d, d)
        got = client.get("/api/market/futures/oi-history?days=5").json()
        assert got["last_date"] == "2026-12-09" and got["items"][0]["oi"] == 150_000 and got["reason"] is None
        assert client.get("/api/market/futures/oi-history?days=0").status_code == 422
        assert client.get("/api/market/futures/oi-history?days=1001").status_code == 422


def test_derive_from_the_cli_needs_no_key(engine, monkeypatch, db_url, capsys):
    d = date(2026, 12, 9)
    _store(engine, "fut_bydd_trd", d, [fut(d, "202612", 150_000)])
    monkeypatch.setenv("DESK_DATABASE_URL", db_url)
    monkeypatch.delenv("DESK_KRX_API_KEY", raising=False)
    assert cli.main(["krx", "derive", "--from", "2026-12-09", "--to", "2026-12-09"]) == 0
    assert "1 day(s) written" in capsys.readouterr().out
