"""#483: well-known funds' 13F holdings - parsing, amendments, 13F-NT, the quarter-on-quarter changes,
the deadlines, the safety limits and the API."""

import json
import threading
from datetime import date

import httpx
import pytest

from desk import funds13f as f13
from tests.test_api import _client

NS = "http://www.sec.gov/edgar/document/thirteenf/informationtable"


def _table(rows: list[tuple]) -> bytes:
    """rows: (issuer, class, cusip, value, shares, put_call | None[, share type])."""
    items = "".join(
        f"<ns1:infoTable><ns1:nameOfIssuer>{r[0]}</ns1:nameOfIssuer><ns1:titleOfClass>{r[1]}</ns1:titleOfClass>"
        f"<ns1:cusip>{r[2]}</ns1:cusip><ns1:value>{r[3]}</ns1:value><ns1:shrsOrPrnAmt><ns1:sshPrnamt>{r[4]}</ns1:sshPrnamt>"
        f"<ns1:sshPrnamtType>{r[6] if len(r) > 6 else 'SH'}</ns1:sshPrnamtType></ns1:shrsOrPrnAmt>"
        + (f"<ns1:putCall>{r[5]}</ns1:putCall>" if r[5] else "")
        + "<ns1:investmentDiscretion>SOLE</ns1:investmentDiscretion></ns1:infoTable>"
        for r in rows)
    return f'<?xml version="1.0"?><ns1:informationTable xmlns:ns1="{NS}">{items}</ns1:informationTable>'.encode()


Q2 = _table([("ALPHABET INC", "CAP STK CL C", "02079K107", 5_000_000_000, 20_000_000, None),
             ("HILTON WORLDWIDE HLDGS INC", "COM", "43300A203", 3_000_000_000, 12_000_000, None),
             ("CANADIAN PAC KANS CITY LTD", "COM", "13646K108", 2_000_000_000, 25_000_000, None),
             ("BROOKFIELD CORP", "CL A LTD VT SH", "11271J107", 14_000_000_000, 300_000_000, None)])
Q3 = _table([("ALPHABET INC", "CAP STK CL C", "02079K107", 6_000_000_000, 15_000_000, None),
             ("ALPHABET INC", "CAP STK CL C", "02079K107", 1_000_000_000, 2_500_000, None),   # another manager's lot
             ("HILTON WORLDWIDE HLDGS INC", "COM", "43300A203", 4_000_000_000, 14_000_000, None),
             ("UBER TECHNOLOGIES INC", "COM", "90353T100", 2_348_000_000, 34_268_279, None),
             ("UBER TECHNOLOGIES INC", "COM", "90353T100", 100_000_000, 1_000_000, "Call"),
             ("BROOKFIELD CORP", "CL A LTD VT SH", "11271J107", 15_852_000_000, 300_000_000, None)])
STOCKS_Q3 = 7_000_000_000 + 4_000_000_000 + 2_348_000_000 + 15_852_000_000


def _cover(other_cik: str | None | bool = "0002000001", amendment: str | None = None) -> bytes:
    other = ("<otherManagersInfo><otherManager>" + (f"<cik>{other_cik}</cik>" if other_cik else "")
             + "<form13FFileNumber>028-99999</form13FFileNumber><name>Pershing Square Holdco, L.P.</name>"
             "</otherManager></otherManagersInfo>") if other_cik is not False else ""
    amend = f"<amendmentInfo><amendmentType>{amendment}</amendmentType></amendmentInfo>" if amendment else ""
    return (f'<?xml version="1.0"?><edgarSubmission xmlns="http://www.sec.gov/edgar/thirteenffiler">'
            f"<formData><coverPage>{amend}{other}</coverPage></formData></edgarSubmission>").encode()


def _subs(rows: list[tuple]) -> dict:
    """rows: (form, accession, filed, period)."""
    return {"filings": {"recent": {"form": [r[0] for r in rows], "accessionNumber": [r[1] for r in rows],
                                   "filingDate": [r[2] for r in rows], "reportDate": [r[3] for r in rows],
                                   "primaryDocument": ["primary_doc.xml"] * len(rows)}}}


def _index(*names: str) -> bytes:
    return json.dumps({"directory": {"item": [{"name": "primary_doc.xml"}, *({"name": n} for n in names)]}}).encode()


class FakeEdgar:
    def __init__(self, pages: dict):
        self.pages, self.asked = pages, []

    def fetch(self, url: str) -> bytes:
        self.asked.append(url)
        got = self.pages.get(url)
        if got is None:
            raise f13.FundsUnavailable("SEC 에 그 문서가 없습니다")
        if isinstance(got, Exception):
            raise got
        return got

    def json(self, url: str) -> dict:
        return json.loads(self.fetch(url))


def _archive(cik: str, acc: str) -> str:
    return f13.ARCHIVE_URL.format(cik=int(cik), acc=acc.replace("-", ""))


SUBS = f13.SUBMISSIONS_URL.format
PSQ, HOLDCO = "0001336528", "0002000001"
Q2_ACC, Q3_NT, HOLD_Q3 = "0001172661-26-000002", "0001172661-26-000003", "0009999999-26-000003"


def _pershing_pages() -> dict:
    """Pershing: Q2 13F-HR of its own, Q3 a 13F-NT reported by a holdco (as in 2026)."""
    pages = {
        SUBS(cik=PSQ): json.dumps(_subs([("13F-NT", Q3_NT, "2026-11-16", "2026-09-30"),
                                         ("13F-HR", Q2_ACC, "2026-08-14", "2026-06-30")])).encode(),
        _archive(PSQ, Q3_NT) + "primary_doc.xml": _cover(),
        SUBS(cik=HOLDCO): json.dumps(_subs([("13F-HR", HOLD_Q3, "2026-11-16", "2026-09-30")])).encode(),
        _archive(HOLDCO, HOLD_Q3) + "index.json": _index("infotable.xml"),
        _archive(HOLDCO, HOLD_Q3) + "infotable.xml": Q3,
        _archive(PSQ, Q2_ACC) + "index.json": _index("form13fInfoTable.xml"),
        _archive(PSQ, Q2_ACC) + "form13fInfoTable.xml": Q2,
    }
    for fund in f13.FUNDS:
        if fund.id != "pershing":
            pages[SUBS(cik=fund.cik)] = json.dumps(_subs([])).encode()
    return pages


TICKERS = {"02079K107": "GOOG", "43300A203": "HLT", "90353T100": "UBER", "11271J107": "BN", "13646K108": "CP"}


def _funds(pages=None, tmp_path=None, figi=None) -> f13.Funds:
    return f13.Funds(FakeEdgar(pages or _pershing_pages()), cache_dir=tmp_path,
                     figi=figi or (lambda cusips: {c: TICKERS.get(c) for c in cusips}), today=lambda: date(2026, 11, 17))


# ------------------------------------------------------------------ parsing

def test_the_table_is_read_with_its_namespace_options_units_and_bond_rows():
    rows = f13.parse_info_table(Q3)
    assert len(rows) == 6 and rows[3] == {"name": "UBER TECHNOLOGIES INC", "title": "COM", "cusip": "90353T100",
                                          "put_call": None, "shares": 34_268_279, "share_type": "SH", "value": 2_348_000_000}
    assert rows[4]["put_call"] == "CALL"
    old = f13.parse_info_table(_table([("X", "COM", "000000000", 1234, 10, None)]), dollars=False)
    assert old[0]["value"] == 1_234_000                              # filed before 2023-01-03: thousands
    bond = f13.parse_info_table(_table([("X CONV NOTE", "NOTE 2.5%", "000000001", 5_000_000, 5_000_000, None, "PRN")]))
    assert bond[0]["share_type"] == "PRN"


def test_a_document_with_a_dtd_is_refused_and_the_root_is_read_from_the_first_tag():
    bomb = b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "xxxx">]><informationTable>&x;</informationTable>'
    with pytest.raises(f13.FundsUnavailable, match="DTD"):
        f13.parse_info_table(bomb)
    with pytest.raises(f13.FundsUnavailable):
        f13.cover(bomb)
    assert f13.root_name(Q3) == "informationTable" and f13.root_name(_cover()) == "edgarSubmission"


def test_lots_of_one_issuer_add_up_but_options_stay_apart():
    rows = f13.merge(f13.parse_info_table(Q3))
    goog = [r for r in rows if r["cusip"] == "02079K107"]
    assert len(goog) == 1 and goog[0]["shares"] == 17_500_000 and goog[0]["value"] == 7_000_000_000
    assert len([r for r in rows if r["cusip"] == "90353T100"]) == 2


def test_a_cover_page_gives_the_amendment_type_and_a_well_formed_other_manager():
    assert f13.cover(_cover(amendment="NEW HOLDINGS"))["amendment_type"] == "NEW HOLDINGS"
    assert f13.cover(_cover())["other"] == {"cik": HOLDCO, "name": "Pershing Square Holdco, L.P."}
    assert f13.cover(_cover("../../x"))["other"]["cik"] is None          # not a CIK: never put in a URL


# ------------------------------------------------------------------ comparing and words

def test_the_quarter_on_quarter_changes_weighted_over_stocks():
    cur = f13.merge(f13.parse_info_table(Q3))
    prev = f13.merge(f13.parse_info_table(Q2))
    rows = {(h["cusip"], h["put_call"]): h for h in f13.compare(cur, prev)}
    uber = rows[("90353T100", None)]
    assert uber["change"] == "new" and uber["change_shares"] == 34_268_279 and uber["prev_shares"] == 0
    assert uber["weight"] == round(100 * 2_348_000_000 / STOCKS_Q3, 2)       # the call's notional is not in the base
    assert rows[("90353T100", "CALL")]["weight"] is None
    assert rows[("02079K107", None)]["change"] == "decrease" and rows[("02079K107", None)]["change_shares"] == -2_500_000
    assert rows[("43300A203", None)]["change"] == "increase" and rows[("43300A203", None)]["change_pct"] == 16.7
    sold = rows[("13646K108", None)]
    assert sold["change"] == "sold" and sold["shares"] == 0 and sold["prev_shares"] == 25_000_000
    order = f13.compare(cur, prev)
    assert order[0]["cusip"] == "11271J107" and order[-1]["change"] == "sold"
    assert [h["put_call"] for h in order if h["change"] != "sold"][-1] == "CALL"        # options after the stocks
    unknown = f13.compare(cur, None)
    assert {h["change"] for h in unknown} == {None} and all(h["change_shares"] is None for h in unknown)


def test_amounts_read_in_korean_units():
    assert f13.won_style(2_348_000_000) == "23억 4,800만 달러"
    assert f13.won_style(950_000_000) == "9억 5,000만 달러"
    assert f13.won_style(15_852_000_000) == "158억 5,200만 달러"
    assert f13.won_style(40_000_000) == "4,000만 달러" and f13.won_style(123_456) == "12만 달러"
    assert f13.won_style(4_000) == "4,000 달러" and f13.won_style(-950_000_000) == "-9억 5,000만 달러"


def test_bonds_and_options_stay_out_of_the_share_wording():
    fund = f13.BY_ID["oaktree"]
    holdings = f13.compare(f13.merge(f13.parse_info_table(_table([
        ("X CONV NOTE", "NOTE", "000000001", 5_000_000, 5_000_000, None, "PRN"),
        ("Y CORP", "COM", "000000002", 1_000_000, 10_000, None)]))), [])
    head, bullets = f13.summary(fund, "2026Q3", holdings)
    assert "Y Corp" in head and "X Conv" not in " ".join(bullets)


# ------------------------------------------------------------------ deadlines

def test_quarters_deadlines_and_how_often_to_ask():
    assert f13.quarter_of(date(2026, 9, 30)) == "2026Q3" and f13.period_of("2026Q4") == date(2026, 12, 31)
    assert f13.previous_quarter("2026Q1") == "2025Q4"
    for bad in ("2026Q5", "26Q1", "１９９９Q1", "0000Q1"):
        with pytest.raises(ValueError):
            f13.period_of(bad)
    assert f13.due_date(date(2026, 9, 30)) == date(2026, 11, 16)            # 11-14 is a Saturday
    assert f13.due_date(date(2026, 3, 31)) == date(2026, 5, 15)
    assert f13.next_due(date(2026, 11, 17)) == date(2027, 2, 15)             # 2027-02-14 is a Sunday
    assert f13.next_due(date(2026, 10, 20)) == date(2026, 11, 16)
    assert f13.next_due(date(2026, 1, 10)) == date(2026, 2, 16)              # year boundary: Q4's deadline
    assert f13.check_every(date(2026, 11, 30)) == f13.WINDOW_EVERY_SEC        # 14 days after the deadline
    assert f13.check_every(date(2026, 12, 1)) == f13.DAILY_SEC                # 15 days after
    assert f13.check_every(date(2027, 2, 1)) == f13.WINDOW_EVERY_SEC         # 14 days before 2/15
    assert f13.check_every(date(2026, 4, 1)) == f13.DAILY_SEC


def test_filings_are_kept_per_period_in_filing_order_with_checked_accessions():
    got = f13.filings(_subs([("13F-HR/A", "0000000001-26-000003", "2026-09-01", "2026-06-30"),
                             ("13F-HR", "0000000001-26-000002", "2026-08-14", "2026-06-30"),
                             ("13F-HR", "0000000001-26-000001", "2026-05-15", "2026-03-31"),
                             ("13F-HR", "../../etc", "2026-05-15", "2025-12-31"),
                             ("SC 13G", "0000000001-26-000009", "2026-05-01", "2026-03-31")]))
    assert [f["accession"] for f in got["2026-06-30"]] == ["0000000001-26-000002", "0000000001-26-000003"]
    assert set(got) == {"2026-06-30", "2026-03-31"}


# ------------------------------------------------------------------ the desk's view

def test_a_13f_nt_is_followed_to_the_manager_that_reports_and_summarised(tmp_path):
    funds = _funds(tmp_path=tmp_path)
    assert funds.refresh() == len(f13.FUNDS)
    v = funds.view("pershing")
    assert v["quarter"] == "2026Q3" and v["form"] == "13F-NT" and v["filed_at"] == "2026-11-16"   # its own filing
    assert v["reported_by"] == {"name": "Pershing Square Holdco, L.P.", "cik": HOLDCO}
    assert v["prev_quarter"] == "2026Q2" and v["quarters"] == ["2026Q3", "2026Q2"]
    assert v["count"] == 4 and v["total_value"] == STOCKS_Q3 and v["options_value"] == 100_000_000
    uber = next(h for h in v["holdings"] if h["ticker"] == "UBER" and not h["put_call"])
    assert uber["shares"] == 34_268_279 and uber["value"] == 2_348_000_000 and uber["change"] == "new"
    assert v["summary"] == "빌 애크먼 퍼싱스퀘어 3분기 포트폴리오 — UBER 23억 4,800만 달러 신규 매수"
    assert v["bullets"][0].startswith("신규: UBER 34,268,279주(23억 4,800만 달러") and "전량 매도: CP" in v["bullets"]
    listed = next(r for r in funds.list()["funds"] if r["id"] == "pershing")
    assert listed["latest"]["quarter"] == "2026Q3" and listed["latest"]["top_new"][0]["ticker"] == "UBER"
    assert listed["latest"]["count"] == 4 and listed["next_due"] == "2027-02-15" and listed["error"] is None
    assert funds.changes(["UBER", "NVDA"])["items"] == {"UBER": [{"fund": "pershing", "name": "퍼싱스퀘어", "quarter": "2026Q3",
                                                                  "change": "new", "change_shares": 34_268_279,
                                                                  "shares": 34_268_279, "weight": uber["weight"]}]}
    # the tables are cached by accession: a new desk reads them from disk, not from the Archives
    offline = {k: (f13.FundsUnavailable("offline") if "Archives" in k and "primary_doc" not in k else v)
               for k, v in _pershing_pages().items()}
    again = _funds(pages=offline, tmp_path=tmp_path)
    again.refresh()
    assert again.view("pershing")["count"] == 4 and again.errors["pershing"] is None


def _berkshire(amendment: str) -> dict:
    cik, base, amend, prev = "0001067983", "0000950123-26-000001", "0000950123-26-000002", "0000950123-26-000000"
    pages = {SUBS(cik=f.cik): json.dumps(_subs([])).encode() for f in f13.FUNDS}
    pages[SUBS(cik=cik)] = json.dumps(_subs([("13F-HR/A", amend, "2026-12-01", "2026-09-30"),
                                             ("13F-HR", base, "2026-11-14", "2026-09-30"),
                                             ("13F-HR", prev, "2026-08-14", "2026-06-30")])).encode()
    pages[_archive(cik, amend) + "primary_doc.xml"] = _cover(other_cik=False, amendment=amendment)
    for acc, rows in ((base, [("APPLE INC", "COM", "037833100", 60_000_000_000, 300_000_000, None)]),
                      (amend, [("CHUBB LIMITED", "COM", "H1467J104", 6_700_000_000, 26_000_000, None)]),
                      (prev, [("APPLE INC", "COM", "037833100", 80_000_000_000, 400_000_000, None),
                              ("CHUBB LIMITED", "COM", "H1467J104", 5_000_000_000, 20_000_000, None)])):
        pages[_archive(cik, acc) + "index.json"] = _index("summary.xml", "infotable.xml")
        pages[_archive(cik, acc) + "summary.xml"] = _cover(other_cik=False)            # not the table: skipped
        pages[_archive(cik, acc) + "infotable.xml"] = _table(rows)
    return pages


def test_a_new_holdings_amendment_is_added_and_a_restatement_replaces(tmp_path):
    funds = _funds(pages=_berkshire("NEW HOLDINGS"), tmp_path=tmp_path / "a")
    funds.refresh()
    v = funds.view("berkshire")
    rows = {h["name"]: h for h in v["holdings"]}
    assert v["count"] == 2 and rows["CHUBB LIMITED"]["change"] == "increase"           # not "sold", not the only row
    assert v["form"] == "13F-HR/A" and v["filed_at"] == "2026-12-01"
    funds = _funds(pages=_berkshire("RESTATEMENT"), tmp_path=tmp_path / "b")
    funds.refresh()
    rows = {h["name"]: h for h in funds.view("berkshire")["holdings"]}
    assert rows["APPLE INC"]["change"] == "sold" and rows["CHUBB LIMITED"]["shares"] == 26_000_000


def test_a_failed_previous_quarter_leaves_the_changes_unknown_and_retries_soon(tmp_path):
    pages = _pershing_pages()
    pages[_archive(PSQ, Q2_ACC) + "index.json"] = f13.FundsUnavailable("SEC 응답 오류(HTTP 500)")
    funds = _funds(pages=pages, tmp_path=tmp_path)
    funds.refresh()
    v = funds.view("pershing")
    assert v["prev_quarter"] is None and {h["change"] for h in v["holdings"]} == {None}
    assert funds.errors["pershing"] and funds.next_delay() == f13.RETRY_SEC
    assert funds.changes(["UBER"])["items"] == {}


def test_a_failed_pass_keeps_the_last_answer_and_says_why(tmp_path):
    funds = _funds(tmp_path=tmp_path)
    funds.refresh()
    refused = f13.FundsUnavailable("SEC 가 연락처 없는 User-Agent 를 거절했습니다 — SEC_EDGAR_USER_AGENT 를 설정하세요")
    funds.edgar = FakeEdgar({k: refused for k in _pershing_pages()})
    assert funds.refresh() == 0
    row = next(r for r in funds.list()["funds"] if r["id"] == "pershing")
    assert row["latest"]["quarter"] == "2026Q3" and "User-Agent" in row["error"]
    assert funds.next_delay() == f13.RETRY_SEC
    assert funds.view("pershing")["count"] == 4                                    # still answered from memory


def test_an_nt_without_the_reporting_managers_cik_says_so_without_hammering(tmp_path):
    pages = _pershing_pages()
    pages[_archive(PSQ, Q3_NT) + "primary_doc.xml"] = _cover(other_cik=None)
    funds = _funds(pages=pages, tmp_path=tmp_path)
    funds.refresh()
    assert "운용사를 찾지 못했습니다(Pershing Square Holdco" in funds.errors["pershing"]
    assert funds.next_delay() != f13.RETRY_SEC                                     # a retry will not fix it
    asked = len(funds.edgar.asked)
    with pytest.raises(f13.FundsUnavailable):
        funds.view("pershing")
    assert len(funds.edgar.asked) == asked                                         # the failure is remembered


def test_the_table_file_is_found_by_its_root_and_an_empty_one_is_not_kept(tmp_path):
    pages = _pershing_pages()
    pages[_archive(HOLDCO, HOLD_Q3) + "infotable.xml"] = _table([])
    funds = _funds(pages=pages, tmp_path=tmp_path)
    funds.refresh()
    assert "비어 있습니다" in funds.errors["pershing"]
    assert not list(tmp_path.glob(f"table-{HOLD_Q3}.json"))


def test_a_quarter_not_filed_a_fund_never_read_or_another_fetch_running(tmp_path):
    funds = _funds(tmp_path=tmp_path)
    with pytest.raises(f13.FundsUnavailable):
        funds.view("pershing")                                                     # nothing read yet
    funds.refresh()
    with pytest.raises(f13.NotFiled):
        funds.view("pershing", "2025Q1")
    with pytest.raises(KeyError):
        funds.view("nobody")
    funds._views.clear()
    held = threading.Event()
    done = threading.Event()

    def hold():
        with funds._fetching(wait=True):
            held.set()
            done.wait(5)

    t = threading.Thread(target=hold)
    t.start()
    held.wait(5)
    try:
        with pytest.raises(f13.FundsBusy):
            funds.view("pershing")                                                 # never queues behind SEC
    finally:
        done.set()
        t.join()


def test_a_class_share_ticker_from_figi_matches_the_desks_spelling(tmp_path):
    funds = _funds(tmp_path=tmp_path, figi=lambda cusips: {c: ("UBER/B" if c == "90353T100" else None) for c in cusips})
    funds.refresh()
    assert list(funds.changes(["UBER-B"])["items"]) == ["UBER-B"]
    assert list(funds.changes(["UBER.B"])["items"]) == ["UBER.B"]                  # keyed as asked


def test_the_edgar_client_paces_requests_refuses_redirects_and_names_a_missing_contact():
    slept, now = [], [100.0]
    ok = httpx.Response(200, content=b"{}", request=httpx.Request("GET", "https://data.sec.gov/a"))
    edgar = f13.Edgar(get=lambda url, headers: ok, agent=lambda: "app contact", clock=lambda: now[0],
                      sleep=lambda s: slept.append(round(s, 2)))
    edgar.fetch("https://data.sec.gov/a")
    edgar.fetch("https://data.sec.gov/b")
    assert slept == [f13.MIN_GAP_SEC]
    blocked = httpx.Response(403, content=b"<title>Your Request Originates from an Undeclared Automated Tool</title>",
                             request=httpx.Request("GET", "https://www.sec.gov/x"))
    with pytest.raises(f13.FundsUnavailable, match="SEC_EDGAR_USER_AGENT"):
        f13.Edgar(get=lambda url, headers: blocked, agent=lambda: "app").fetch("https://www.sec.gov/x")
    moved = httpx.Response(301, headers={"location": "https://elsewhere.example/"},
                           request=httpx.Request("GET", "https://www.sec.gov/x"))
    with pytest.raises(f13.FundsUnavailable, match="다른 주소"):
        f13.Edgar(get=lambda url, headers: moved, agent=lambda: "app").fetch("https://www.sec.gov/x")


def test_an_error_for_the_page_never_carries_remote_text():
    assert f13._short(KeyError("reportDate")) == "공시를 받지 못했습니다(KeyError)"
    assert f13._short(f13.FundsUnavailable("SEC 에 그 문서가 없습니다")) == "SEC 에 그 문서가 없습니다"


# ------------------------------------------------------------------ the API

def test_the_funds_api(db_url, monkeypatch):
    view = {"id": "pershing", "quarter": "2026Q3", "holdings": []}
    seen = []

    def fake_view(self, fund_id, quarter=None, wait=False, map_budget=0):
        seen.append((fund_id, quarter))
        if quarter == "2025Q1":
            raise f13.NotFiled("2025Q1 공시가 없습니다")
        if quarter == "2025Q2":
            raise f13.FundsBusy("다른 요청이 SEC 에서 공시를 받는 중입니다")
        if quarter == "2025Q3":
            raise RuntimeError("remote said: secret detail")
        return view

    monkeypatch.setattr(f13.Funds, "view", fake_view)
    with _client(db_url) as client:
        listed = client.get("/api/funds").json()
        assert [r["id"] for r in listed["funds"]] == [f.id for f in f13.FUNDS] and listed["funds"][0]["latest"] is None
        assert client.get("/api/funds/pershing?quarter=2026Q3").json() == view and seen == [("pershing", "2026Q3")]
        assert client.get("/api/funds/nobody").status_code == 404
        assert client.get("/api/funds/pershing?quarter=2026Q9").status_code == 422
        assert client.get("/api/funds/pershing?quarter=2025Q1").status_code == 404
        busy = client.get("/api/funds/pershing?quarter=2025Q2")
        assert busy.status_code == 503 and "받는 중" in busy.json()["detail"]
        odd = client.get("/api/funds/pershing?quarter=2025Q3")
        assert odd.status_code == 503 and "secret" not in odd.text
        assert client.get("/api/funds/changes?tickers=UBER,005930").json() == {"items": {}}
        assert client.get("/api/funds/changes?tickers=").status_code == 422
        assert client.get("/api/funds/changes?tickers=" + ",".join(f"T{i}" for i in range(31))).status_code == 422


def test_the_funds_api_is_closed_without_a_session(db_url):
    with _client(db_url, login=False) as client:
        for path in ("/api/funds", "/api/funds/pershing", "/api/funds/changes?tickers=UBER"):
            assert client.get(path).status_code == 401
