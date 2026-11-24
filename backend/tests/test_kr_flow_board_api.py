from contextlib import closing
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from desk import kr_flow_data, kr_flow_import
from desk.db import make_engine
from tests.test_favorites import OTHER, _app, _login

URL = "/api/market/flow-board/kr"
NOW = datetime(2026, 11, 24, 10, tzinfo=UTC)
DAY = date(2026, 11, 24)


def test_flow_board_auth_and_per_account_favorites_with_a_shared_ranking(db_url, monkeypatch):
    app = _app(db_url)
    read = kr_flow_data.read

    async def read_at(engine, market, days, favorites, now):
        return await read(engine, market, days, favorites, NOW)

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
