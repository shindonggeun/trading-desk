from contextlib import closing
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from desk import kr_flow_data, kr_flow_import
from desk.db import make_engine
from desk.kr_flow_board import ForeignFlow, Turnover
from desk.krx import KrxDaily
from tests.test_favorites import OTHER, _app, _login

URL = "/api/market/flow-board/kr"
NOW = datetime(2026, 11, 24, 10, tzinfo=UTC)
DAY = date(2026, 11, 24)


def test_flow_board_auth_and_per_account_favorites_with_a_shared_ranking(db_url, monkeypatch):
    app = _app(db_url)
    read = kr_flow_data.read

    async def read_at(engine, market, days, favorites, now, **options):
        return await read(engine, market, days, favorites, NOW, **options)

    monkeypatch.setattr(kr_flow_data, "read", read_at)
    engine = make_engine(db_url)
    try:
        source = {"OutBlock_1": [
            {"BAS_DD": "20261124", "ISU_CD": code, "ISU_NM": name, "MKT_NM": "KOSPI", "ACC_TRDVAL": amount}
            for code, name, amount in (("005930", "삼성전자", "1000"), ("000660", "SK하이닉스", "2000"))
        ]}
        assert kr_flow_import.save(engine, "kospi", DAY, source, NOW) == 2
    finally:
        engine.dispose()

    with closing(TestClient(app, base_url="https://testserver")) as anonymous:
        assert anonymous.get(URL).status_code == 401
    with _login(app) as owner, closing(_login(app, OTHER)) as other:
        assert owner.post("/api/favorites", json={"ticker": "005930.KS"}).status_code in (200, 201, 204)
        assert other.post("/api/favorites", json={"ticker": "000660.KS"}).status_code in (200, 201, 204)
        assert other.post("/api/favorites", json={"ticker": "247540.KQ"}).status_code in (200, 201, 204)
        first = owner.get(URL)
        second = other.get(URL)
        assert first.status_code == second.status_code == 200
        assert first.headers["cache-control"] == second.headers["cache-control"] == "private, no-store"
        one, two = first.json(), second.json()
        assert len(one["sessions"]) == 22
        assert one["ranking"] == two["ranking"]
        assert one["ranking"]["as_of"] == DAY.isoformat()
        assert [r["ticker"] for r in one["ranking"]["items"]] == ["000660.KS", "005930.KS"]
        assert [r["ticker"] for r in one["favorites"]["items"]] == ["005930.KS"]
        assert [r["ticker"] for r in two["favorites"]["items"]] == ["000660.KS"]
        assert {r["participation_pct"] for r in one["favorites"]["items"][0]["history"]} == {None}
        assert one["sources"]["foreign_flow"]["status"] == "source_unavailable"
        assert one["sources"]["ownership"]["status"] == "source_unavailable"
        kosdaq = other.get(URL, params={"market": "kosdaq", "days": 1}).json()
        assert len(kosdaq["sessions"]) == 1
        assert [r["ticker"] for r in kosdaq["favorites"]["items"]] == ["247540.KQ"]
        assert other.get(URL, params={"market": "nxt"}).status_code == 422
        assert other.get(URL, params={"days": 23}).status_code == 422


def test_flow_board_index_change_follows_completed_kst_sessions(db_url, monkeypatch):
    app = _app(db_url)
    read = kr_flow_data.read
    now = datetime(2026, 10, 8, 6, 29, tzinfo=UTC)  # 15:29 KST
    wednesday = date(2026, 10, 7)
    thursday = date(2026, 10, 8)
    holiday = date(2026, 10, 9)

    async def read_at(engine, market, days, favorites, _now, **options):
        return await read(engine, market, days, favorites, now, **options)

    monkeypatch.setattr(kr_flow_data, "read", read_at)
    engine = make_engine(db_url)
    try:
        store = KrxDaily(engine, client=None)

        def row(day, name, close, change):
            return {"BAS_DD": day.strftime("%Y%m%d"), "IDX_NM": name, "CLSPRC_IDX": close,
                    "FLUC_RT": change, "OPNPRC_IDX": "600"}

        store.store("kospi_dd_trd", wednesday, [row(wednesday, "코스피", "3000", "0.75")])
        store.store("kospi_dd_trd", thursday, [row(thursday, "코스피", "3000", "0.00")])
        store.store("kospi_dd_trd", holiday, [row(holiday, "코스피", "9999", "233.30")])
        store.store("kosdaq_dd_trd", thursday, [row(thursday, "코스닥", "850", "-1.25")])
    finally:
        engine.dispose()

    def check(client, market, latest, close, change, *, as_of=None):
        response = client.get(URL, params={"market": market})
        assert response.status_code == 200
        body = response.json()
        assert body["latest_session"] == latest.isoformat()
        assert body["market_summary"]["index"] == {
            "as_of": (as_of or latest).isoformat(), "source": "krx_open_api", "published_at": None,
            "unit": "index_points", "value": close, "change_pct": change,
        }

    with _login(app) as client:
        check(client, "kospi", wednesday, 3000, 0.75)
        now = datetime(2026, 10, 8, 6, 30, tzinfo=UTC)  # 15:30 KST
        check(client, "kospi", thursday, 3000, 0)
        check(client, "kosdaq", thursday, 850, -1.25)
        now = datetime(2026, 10, 8, 15, 1, tzinfo=UTC)  # 00:01 KST on Hangul Day
        check(client, "kospi", thursday, 3000, 0)
        now = datetime(2026, 10, 11, 3, tzinfo=UTC)  # Sunday in Korea
        check(client, "kospi", thursday, 3000, 0)
        now = datetime(2026, 10, 12, 7, tzinfo=UTC)  # Monday's close is not yet published
        check(client, "kospi", date(2026, 10, 12), 3000, 0, as_of=thursday)


class FakeStockFlow:
    """Stands in for desk.kis_stock_flow: KIS rows for Samsung only, recording what each request asked."""

    def __init__(self):
        self.asked = []
        self.closed = False

    async def collect(self, codes, market, latest_session, *, budget_sec=2.0):
        self.asked.append((list(codes), market, latest_session))
        if "005930" not in codes:
            return [], [], []
        turnover = Turnover("005930.KS", market, latest_session, "kis", 1_000_000, trade_scope="all_sessions")
        flow = ForeignFlow("005930.KS", market, latest_session, "kis", 120_000, 80_000, venue="krx", unit="KRW",
                           trade_scope="all_sessions")
        return [turnover], [flow], []

    async def aclose(self):
        self.closed = True


def test_flow_board_shows_kis_participation_for_the_accounts_own_favorite(db_url, monkeypatch):
    stock_flow = FakeStockFlow()
    app = _app(db_url, stock_flow=stock_flow)
    read = kr_flow_data.read

    async def read_at(engine, market, days, favorites, now, **options):
        return await read(engine, market, days, favorites, NOW, **options)

    monkeypatch.setattr(kr_flow_data, "read", read_at)
    engine = make_engine(db_url)
    try:
        source = {"OutBlock_1": [
            {"BAS_DD": "20261124", "ISU_CD": code, "ISU_NM": name, "MKT_NM": "KOSPI", "ACC_TRDVAL": amount}
            for code, name, amount in (("005930", "삼성전자", "1000"), ("000660", "SK하이닉스", "2000"))
        ]}
        assert kr_flow_import.save(engine, "kospi", DAY, source, NOW) == 2
    finally:
        engine.dispose()

    with _login(app) as owner, closing(_login(app, OTHER)) as other:
        assert owner.post("/api/favorites", json={"ticker": "005930.KS"}).status_code in (200, 201, 204)
        assert other.post("/api/favorites", json={"ticker": "000660.KS"}).status_code in (200, 201, 204)
        one, two = owner.get(URL).json(), other.get(URL).json()
        # Favorites first, then the ranking (turnover desc); each code once
        assert [asked[0] for asked in stock_flow.asked] == [["005930", "000660"], ["000660", "005930"]]
        assert {asked[1:] for asked in stock_flow.asked} == {("kospi", DAY)}
        latest = one["favorites"]["items"][0]["history"][-1]
        assert latest["date"] == DAY.isoformat()
        assert latest["foreign_flow"]["status"] == "available"
        assert (latest["turnover"]["source"], latest["turnover"]["value_krw"]) == ("kis", 1_000_000)
        assert latest["participation_pct"] == pytest.approx(10.0)
        assert latest["buy_sell_imbalance"] == pytest.approx(0.2)
        assert one["sources"]["foreign_flow"]["status"] == "available"
        assert one["sources"]["ownership"]["status"] == "no_data"
        assert [item["ticker"] for item in two["favorites"]["items"]] == ["000660.KS"]
        assert {p["participation_pct"] for p in two["favorites"]["items"][0]["history"]} == {None}
    assert stock_flow.closed
