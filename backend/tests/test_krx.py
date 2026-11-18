"""#601 KRX OPEN API collector. The fixtures are hand-made in KRX's documented shape (no real call, no key)."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from desk import cli, krx
from desk.db import krx_daily, krx_days, make_engine, migrate

FIX = Path(__file__).parent / "fixtures" / "krx"
KEY = "test-key-not-real"
TUE = date(2026, 11, 17)                                   # a KRX business day
WED_0900 = datetime(2026, 11, 18, 0, 0, tzinfo=UTC)        # 09:00 KST the next day: TUE is published


def _fixture(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


class Krx:
    """A fake KRX: answers per (api, basDd) from `answers` (default: the api's fixture), records calls."""

    def __init__(self, answers=None, status=200):
        self.answers = answers or {}
        self.status = status
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={})
        api = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
        day = request.url.params["basDd"]
        body = self.answers.get((api, day), self.answers.get(api))
        if body is None:
            body = _fixture(api) if (FIX / f"{api}.json").exists() else _fixture("closed_day")
        return httpx.Response(200, json=body)


def _client(fake: Krx, clock=lambda: WED_0900, **kw) -> krx.Client:
    return krx.Client(KEY, transport=httpx.MockTransport(fake), clock=clock, sleep=lambda s: None, **kw)


@pytest.fixture
def engine(db_url):
    migrate(db_url)
    eng = make_engine(db_url)
    yield eng
    eng.dispose()


def _count(engine, table, **where) -> int:
    q = select(func.count()).select_from(table)
    for k, v in where.items():
        q = q.where(table.c[k] == v)
    with engine.connect() as conn:
        return conn.execute(q).scalar_one()


def test_numbers_come_as_strings_with_commas_and_dashes():
    assert krx.num("4,012.35") == 4012.35
    assert krx.num("-21.40") == -21.4
    assert krx.num("512,334,120") == 512334120
    assert krx.num("-") is None and krx.num("") is None and krx.num(None) is None and krx.num("N/A") is None


def test_a_call_carries_the_key_in_the_header_only_and_reads_the_outblock():
    fake = Krx()
    rows = _client(fake).fetch("kospi_dd_trd", TUE)
    req = fake.calls[0]
    assert str(req.url) == "https://data-dbg.krx.co.kr/svc/apis/idx/kospi_dd_trd.json?basDd=20261117"
    assert req.headers["AUTH_KEY"] == KEY and KEY not in str(req.url)
    assert [r["IDX_NM"] for r in rows][:2] == ["코스피", "코스피 200"]


def test_stored_rows_keep_krx_strings_and_one_row_per_code(engine):
    desk = krx.KrxDaily(engine, _client(Krx()), clock=lambda: WED_0900)
    out = desk.collect("kospi_dd_trd", [TUE])
    assert out["stored"] == 1 and out["rows"] == 3            # the duplicated 코스피 200 row kept once
    rows = desk.rows("kospi_dd_trd", TUE)
    kospi = next(r for r in rows if r["IDX_NM"] == "코스피")
    assert kospi["CLSPRC_IDX"] == "4,012.35" and krx.num(kospi["CLSPRC_IDX"]) == 4012.35
    opt = krx.KrxDaily(engine, _client(Krx())).collect("opt_bydd_trd", [TUE])
    assert opt["rows"] == 2
    put = next(r for r in desk.rows("opt_bydd_trd", TUE) if r["RGHT_TP_NM"] == "PUT")
    assert krx.num(put["IMP_VOLT"]) == 22.81 and krx.num(put["ACC_OPNINT_QTY"]) == 41002


def test_a_rerun_asks_only_for_the_days_it_lacks(engine):
    fake = Krx()
    desk = krx.KrxDaily(engine, _client(fake), clock=lambda: WED_0900)
    first, last = date(2026, 11, 9), TUE                     # Mon .. Tue: 7 business days, a weekend between
    days = desk.missing("kospi_dd_trd", first, last, WED_0900)
    assert days == [date(2026, 11, d) for d in (9, 10, 11, 12, 13, 16, 17)]   # Sat/Sun never asked
    desk.collect("kospi_dd_trd", days)
    assert len(fake.calls) == 7
    assert desk.missing("kospi_dd_trd", first, last, WED_0900) == []
    desk.collect("kospi_dd_trd", [TUE])                      # forced again: replaced, not doubled
    assert _count(engine, krx_daily, api="kospi_dd_trd", bas_dd=TUE) == 3
    assert _count(engine, krx_days, api="kospi_dd_trd") == 7


def test_a_day_is_not_asked_before_krx_publishes_it(engine):
    desk = krx.KrxDaily(engine, _client(Krx()))
    kst = krx.KST
    assert desk.missing("kospi_dd_trd", TUE, TUE, datetime(2026, 11, 18, 8, 0, tzinfo=kst)) == []
    assert desk.missing("kospi_dd_trd", TUE, TUE, datetime(2026, 11, 18, 8, 30, tzinfo=kst)) == [TUE]
    fri = date(2026, 11, 13)                                 # Friday's rows: Monday 08:30, not Saturday
    assert krx.ready_at(fri) == datetime(2026, 11, 16, 8, 30, tzinfo=kst)


def test_an_empty_answer_is_a_closed_day_only_once_it_is_old(engine):
    empty = {"kospi_dd_trd": _fixture("closed_day")}
    fake = Krx(empty)
    clock = [WED_0900]
    desk = krx.KrxDaily(engine, _client(fake, clock=lambda: clock[0]), clock=lambda: clock[0],
                        apis=("kospi_dd_trd",))
    out = desk.run()                                         # the last two weeks: all empty
    assert out[0]["later"] == 4 and out[0]["empty"] == 7     # the last 4 business days still recent
    n = len(fake.calls)
    clock[0] = WED_0900 + timedelta(hours=1)
    assert desk.run() == [] and len(fake.calls) == n         # the recent ones wait LATER_EVERY
    fake.answers = {}
    clock[0] = WED_0900 + krx.LATER_EVERY
    assert desk.run()[0]["rows"] == 12                       # published late: 3 rows a day
    assert _count(engine, krx_days, api="kospi_dd_trd", rows=0) == 7


def test_a_refused_service_is_skipped_for_hours_and_the_key_never_shows(engine, caplog):
    refused = {"respCode": "401", "respMsg": f"Unauthorized key {KEY}"}
    fake = Krx({"fut_bydd_trd": refused})
    desk = krx.KrxDaily(engine, _client(fake), clock=lambda: WED_0900, apis=("fut_bydd_trd", "kospi_dd_trd"))
    with pytest.raises(krx.Refused) as exc:
        desk.client.fetch("fut_bydd_trd", TUE)
    assert KEY not in str(exc.value)
    results = desk.run()
    assert results[0]["stopped"] == "refused" and results[1]["stopped"] is None
    assert KEY not in caplog.text
    asked = len([c for c in fake.calls if "fut_bydd_trd" in c.url.path])
    desk.run(WED_0900 + timedelta(hours=1))
    assert len([c for c in fake.calls if "fut_bydd_trd" in c.url.path]) == asked
    desk.run(WED_0900 + krx.REFUSED_FOR)
    assert len([c for c in fake.calls if "fut_bydd_trd" in c.url.path]) == asked + 1


def test_the_daily_quota_stops_every_service_until_midnight(engine):
    fake = Krx(status=429)
    desk = krx.KrxDaily(engine, _client(fake), clock=lambda: WED_0900)
    results = desk.run()
    assert len(results) == 1 and results[0]["stopped"] == "quota" and len(fake.calls) == 1
    with pytest.raises(krx.QuotaExceeded):
        desk.client.fetch("kospi_dd_trd", TUE)
    assert len(fake.calls) == 1                              # not even asked
    budget = _client(Krx(), budget=2)
    budget.fetch("kospi_dd_trd", TUE)
    budget.fetch("kospi_dd_trd", TUE)
    with pytest.raises(krx.QuotaExceeded):
        budget.fetch("kospi_dd_trd", TUE)


def test_without_a_key_nothing_is_asked(engine, monkeypatch, db_url, capsys):
    fake = Krx()
    desk = krx.KrxDaily(engine, krx.Client("", transport=httpx.MockTransport(fake)), clock=lambda: WED_0900)
    assert desk.run() == [] and fake.calls == []
    monkeypatch.setenv("DESK_DATABASE_URL", db_url)
    monkeypatch.delenv("DESK_KRX_API_KEY", raising=False)
    assert cli.main(["krx", "backfill", "--api", "kospi_dd_trd"]) == 2
    assert "DESK_KRX_API_KEY" in capsys.readouterr().err
    assert cli.main(["krx", "backfill", "--api", "nope"]) == 2
    assert cli.main(["krx", "status"]) == 0


def test_backfill_from_the_cli_is_rerun_safe(engine, monkeypatch, db_url, capsys):
    fake = Krx()
    real = krx.Client

    def client(key, **kw):
        return real(key, transport=httpx.MockTransport(fake), sleep=lambda s: None)

    monkeypatch.setattr(krx, "Client", client)
    monkeypatch.setenv("DESK_DATABASE_URL", db_url)
    monkeypatch.setenv("DESK_KRX_API_KEY", KEY)
    args = ["krx", "backfill", "--api", "kospi_dd_trd", "--from", "2026-11-09", "--to", "2026-11-13"]
    assert cli.main(args) == 0
    assert len(fake.calls) == 5
    assert cli.main(args) == 0
    assert len(fake.calls) == 5                              # nothing left to ask
    assert cli.main(["krx", "backfill", "--api", "kospi_dd_trd", "--from", "2026-11-09", "--to",
                     "2026-11-17", "--max-calls", "1"]) == 0
    assert len(fake.calls) == 6
    assert KEY not in capsys.readouterr().out


def test_krx_error_text_and_odd_keys_stay_out_of_the_logs(engine, caplog):
    echo = {"respCode": "500", "respMsg": f"bad request {KEY.upper()} {KEY[:8]}"}
    with pytest.raises(krx.KrxError) as exc:
        _client(Krx({"kospi_dd_trd": echo})).fetch("kospi_dd_trd", TUE)
    assert str(exc.value).endswith("respCode 500") and KEY[:8] not in str(exc.value)
    fake = Krx()
    odd = krx.Client("키-not-ascii", transport=httpx.MockTransport(fake))
    assert not odd.enabled
    assert krx.KrxDaily(engine, odd).run(WED_0900) == [] and fake.calls == []
    assert "키" not in caplog.text


def test_a_pass_still_running_is_not_started_twice(engine):
    desk = krx.KrxDaily(engine, _client(Krx()), clock=lambda: WED_0900)
    with desk._running:                                      # the previous pass, past its timeout
        assert desk.run() == []
    assert desk.run()                                        # free again


def test_futures_rows_of_two_sessions_both_stay_and_true_repeats_are_counted(engine):
    row = {"BAS_DD": "20261117", "ISU_CD": "KR4101WC0006", "ISU_NM": "코스피200 F 202612", "TDD_CLSPRC": "553.10",
           "ACC_OPNINT_QTY": "251,330"}
    body = {"OutBlock_1": [{**row, "MKT_NM": "정규"}, {**row, "MKT_NM": "야간", "TDD_CLSPRC": "551.95"},
                           {**row, "MKT_NM": "정규"}, {"ISU_NM": "코드 없음"}]}
    desk = krx.KrxDaily(engine, _client(Krx({"fut_bydd_trd": body})), clock=lambda: WED_0900)
    out = desk.collect("fut_bydd_trd", [TUE])
    assert out["rows"] == 2 and out["dropped"] == 2
    assert sorted(r["MKT_NM"] for r in desk.rows("fut_bydd_trd", TUE)) == ["야간", "정규"]


def test_a_day_that_fails_is_skipped_not_a_wall(engine):
    bad = {("kospi_dd_trd", "20261112"): {"OutBlock_1": [], "OutBlock_2": []}}   # two blocks: an error
    fake = Krx(bad)
    desk = krx.KrxDaily(engine, _client(fake), clock=lambda: WED_0900, apis=("kospi_dd_trd",))
    out = desk.run()[0]
    assert out["failed"] == 1 and out["stopped"] is None and out["stored"] == 10
    n = len(fake.calls)
    assert desk.run(WED_0900 + timedelta(hours=1)) == [] and len(fake.calls) == n   # waits LATER_EVERY
    fake.answers = {}
    assert desk.run(WED_0900 + krx.LATER_EVERY)[0]["stored"] == 1
    down = Krx(status=503)
    stop = krx.KrxDaily(engine, _client(down), apis=("kosdaq_dd_trd",)).collect(
        "kosdaq_dd_trd", [date(2026, 11, d) for d in (9, 10, 11, 12, 13)], WED_0900)
    assert stop["stopped"] == "error" and stop["failed"] == krx.MAX_ERRORS and len(down.calls) == krx.MAX_ERRORS


def test_http_403_is_a_refusal_and_the_quota_comes_back_after_midnight_kst():
    with pytest.raises(krx.Refused):
        _client(Krx(status=403)).fetch("kospi_dd_trd", TUE)
    clock = [WED_0900]
    fake = Krx(status=429)
    client = _client(fake, clock=lambda: clock[0])
    with pytest.raises(krx.QuotaExceeded):
        client.fetch("kospi_dd_trd", TUE)
    clock[0] = datetime(2026, 11, 18, 14, 59, tzinfo=UTC)               # 23:59 KST: still spent
    with pytest.raises(krx.QuotaExceeded):
        client.fetch("kospi_dd_trd", TUE)
    assert len(fake.calls) == 1
    fake.status = 200
    clock[0] = datetime(2026, 11, 18, 15, 0, tzinfo=UTC)                # 00:00 KST
    assert client.fetch("kospi_dd_trd", TUE) and client.spent() == 1


def test_an_empty_open_day_turns_closed_after_seven_days_and_can_be_asked_again(engine, monkeypatch, db_url, capsys):
    desk = krx.KrxDaily(engine, _client(Krx({"kospi_dd_trd": _fixture("closed_day")})), clock=lambda: WED_0900)
    out = desk.collect("kospi_dd_trd", [date(2026, 11, 11), date(2026, 11, 12)])       # 7 and 6 days back
    assert out["empty"] == 1 and out["later"] == 1
    assert desk.missing("kospi_dd_trd", date(2026, 11, 11), date(2026, 11, 11), WED_0900) == []
    assert desk.missing("kospi_dd_trd", date(2026, 11, 11), date(2026, 11, 11), WED_0900,
                        refetch_empty=True) == [date(2026, 11, 11)]
    monkeypatch.setenv("DESK_DATABASE_URL", db_url)
    assert cli.main(["krx", "status"]) == 0
    assert "1 open day(s) with no rows" in capsys.readouterr().out


def test_the_cli_says_so_when_a_service_is_refused(engine, monkeypatch, db_url):
    fake = Krx({"kospi_dd_trd": _fixture("refused")})
    real = krx.Client
    monkeypatch.setattr(krx, "Client", lambda key, **kw: real(key, transport=httpx.MockTransport(fake),
                                                             sleep=lambda s: None))
    monkeypatch.setenv("DESK_DATABASE_URL", db_url)
    monkeypatch.setenv("DESK_KRX_API_KEY", KEY)
    assert cli.main(["krx", "backfill", "--api", "kospi_dd_trd", "--from", "2026-11-09", "--to", "2026-11-13"]) == 1
    assert len(fake.calls) == 1


def test_the_vkospi_row_of_the_derivatives_indices(engine):
    desk = krx.KrxDaily(engine, _client(Krx()), clock=lambda: WED_0900)
    assert desk.collect("drvprod_dd_trd", [TUE])["rows"] == 2
    vkospi = [r for r in desk.rows("drvprod_dd_trd", TUE) if r["IDX_NM"].replace(" ", "") == "코스피200변동성지수"]
    assert len(vkospi) == 1 and krx.num(vkospi[0]["CLSPRC_IDX"]) == 24.18
