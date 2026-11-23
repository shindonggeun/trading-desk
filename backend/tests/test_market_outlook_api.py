import json
from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient

from desk.api import create_app
from desk.auth import Auth
from desk.db import make_engine
from desk.funds13f import FUNDS, FundsUnavailable, filings
from desk.market_outlook import MarketOutlook
from desk.market_outlook_sources import DUQUESNE, VIEWS, read_duquesne, read_views
from desk.settings import Settings
from tests.test_api import OWNER, PASSWORD
from tests.test_funds13f import SUBS, _archive, _cover, _funds, _index, _subs, _table

CIK = "0001536411"
Q2 = "0001536411-26-000009"
Q3 = "0001536411-26-000011"
AMENDMENT = "0001536411-26-000012"
SUBMISSIONS = SUBS(cik=CIK)
TODAY = datetime(2026, 11, 23, 22, tzinfo=UTC)


def test_public_views_keep_disagreement_and_dates_with_dedup_and_aging():
    rows = read_views(TODAY.date(), (*VIEWS, VIEWS[0], {**VIEWS[1], "published_on": "2026-12-01"}))
    assert [r["stance"] for r in rows] == ["bullish", "cautious"]
    assert rows[0]["spoken_on"] is None and rows[0]["published_on"] == "2026-11-19"
    assert rows[1]["spoken_on"] == "2026-11-17" and rows[1]["published_on"] is None
    assert rows[1]["positions"] == [{"asset": "구리", "side": "short", "status": "unverified", "as_of": None}]
    assert all(r["status"] == "available" and r["change"] is None for r in rows)
    assert all(r["status"] == "stale" for r in read_views(date(2027, 3, 1)))
    assert read_views(date(2026, 11, 16)) == []


def _put_filing(pages: dict, accession: str, shares: int, amendment: str | None = None) -> None:
    directory = _archive(CIK, accession)
    pages[directory + "index.json"] = _index("infotable.xml")
    pages[directory + "primary_doc.xml"] = _cover(other_cik=False, amendment=amendment)
    pages[directory + "infotable.xml"] = _table([("EXAMPLE", "COM", "000000001", shares * 50, shares, None)])


def test_duquesne_new_filing_amendment_and_outage_never_relabel_old_holdings(tmp_path):
    previous = ("13F-HR", Q2, "2026-08-14", "2026-06-30")
    recent = ("13F-HR", Q3, "2026-11-16", "2026-09-30")
    pages = {SUBS(cik=f.cik): json.dumps(_subs([])).encode() for f in FUNDS}
    pages[SUBMISSIONS] = json.dumps(_subs([previous])).encode()
    _put_filing(pages, Q2, 10)
    _put_filing(pages, Q3, 15)
    _put_filing(pages, AMENDMENT, 30, "RESTATEMENT")
    funds = _funds(pages=pages, tmp_path=tmp_path)
    assert read_duquesne(funds, TODAY)["status"] == "missing"

    funds.refresh()
    snapshot_time = datetime.fromisoformat(funds.checked_at)
    old = read_duquesne(funds, snapshot_time)["items"][0]
    assert old["accession"] == Q2 and old["previous_quarter"] is None

    pages[SUBMISSIONS] = json.dumps(_subs([recent, previous])).encode()
    funds.refresh()
    item = read_duquesne(funds, datetime.fromisoformat(funds.checked_at))["items"][0]
    assert (item["cik"], item["quarter"], item["period_of_report"], item["filed_at"]) == (
        CIK, "2026Q3", "2026-09-30", "2026-11-16",
    )
    assert item["source_url"] == _archive(CIK, Q3) + f"{Q3}-index.html"
    assert item["publication_lag_days"] == 47 and item["previous_quarter"] == "2026Q2"
    assert item["holdings"][0]["change"] == "increase"

    amendment = ("13F-HR/A", AMENDMENT, "2026-11-20", "2026-09-30")
    pages[SUBMISSIONS] = json.dumps(_subs([amendment, recent, previous])).encode()
    funds.periods[DUQUESNE] = filings(_subs([amendment, recent, previous]))
    pending = read_duquesne(funds, datetime.fromisoformat(funds.checked_at))["items"][0]
    assert pending["accession"] == AMENDMENT and pending["holdings_status"] == "missing"
    assert pending["holdings"] == []
    funds.refresh()
    checked_at = datetime.fromisoformat(funds.checked_at)
    assert read_duquesne(funds, checked_at + timedelta(days=3))["reason"] == "check_overdue"
    count = len(funds.edgar.asked)
    item = read_duquesne(funds, checked_at)["items"][0]
    assert item["form"] == "13F-HR/A" and item["accession"] == AMENDMENT
    assert item["holdings"][0]["shares"] == 30 and len(funds.edgar.asked) == count
    # Eviction from the historical LRU does not remove a current background snapshot.
    funds._views.clear()
    assert read_duquesne(funds, checked_at)["items"][0]["holdings"] == item["holdings"]

    # An incomplete comparison cannot discard a newly versioned current table.
    missing_previous = ("13F-HR/A", "0001536411-26-000013", "2026-11-22", "2026-06-30")
    pages[SUBMISSIONS] = json.dumps(_subs([missing_previous, amendment, recent, previous])).encode()
    funds.refresh()
    funds._views.clear()
    partial = read_duquesne(funds, datetime.fromisoformat(funds.checked_at))["items"][0]
    assert partial["previous_quarter"] is None and partial["holdings"][0]["shares"] == 30
    assert partial["holdings"][0]["change"] is None

    pages[SUBMISSIONS] = FundsUnavailable("fake outage")
    funds.refresh()
    failed = read_duquesne(funds, datetime.fromisoformat(funds.checked_at))
    assert failed["status"] == "stale" and failed["items"][0]["accession"] == AMENDMENT
    assert "fake outage" not in json.dumps(failed)


def test_authenticated_outlook_keeps_source_failures_and_opinions_independent(db_url):
    calls = []

    def filing():
        calls.append("filing")
        raise RuntimeError("private upstream error")

    outlook = MarketOutlook(filing=filing, views=read_views, clock=lambda: TODAY)
    app = create_app(Settings(database_url=db_url, fake_runs=True), market_outlook_service=outlook)
    engine = make_engine(db_url)
    try:
        Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(OWNER, PASSWORD)
    finally:
        engine.dispose()
    path = "/api/macro/market-outlook"
    with TestClient(app, base_url="https://testserver") as client:
        assert client.get(path).status_code == 401 and calls == []
        assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
        assert client.get(path, params={"market": "kr"}).status_code == 422 and calls == []
        response = client.get(path, params={"market": "us"})
        assert response.status_code == 200 and calls == ["filing"]
        body = response.json()
        assert body["market"] == "us" and body["as_of"] == TODAY.isoformat()
        assert {view["stance"] for view in body["views"]} == {"bullish", "cautious"}
        assert body["filings"] == [] and body["sources"]["filings"]["status"] == "error"
        assert body["regime"]["state"] == "undetermined"
        assert "private upstream error" not in response.text
