"""KOFIA data reaches a split API whose credit cache starts empty."""

import json
from datetime import UTC, date, datetime, timedelta

import fakeredis
import httpx

from desk import credit, shared_cache
from tests.test_api import OWNER, PASSWORD, _client
from tests.test_credit import _tables


def test_split_api_serves_collector_credit_without_a_source_call(db_url, monkeypatch):
    collector = credit.CreditCache()
    api_cache = credit.CreditCache()
    monkeypatch.setattr(credit, "CACHE", api_cache)
    funds, loans = _tables(25)
    day = datetime.now(UTC).date() - timedelta(days=4)
    offset = day - date(2026, 11, 5)
    for row in [*funds, *loans]:
        original = datetime.strptime(row["TMPV1"], "%Y%m%d").date()
        row["TMPV1"] = (original + offset).strftime("%Y%m%d")
    contacts = []

    def source(request):
        table = json.loads(request.content)["dmSearch"]["OBJ_NM"]
        contacts.append(table)
        return httpx.Response(200, json={"ds1": {credit.FUNDS: funds, credit.CREDIT: loans}[table]})

    async def collect():
        async with httpx.AsyncClient(transport=httpx.MockTransport(source)) as client:
            return await collector.refresh_and_publish(client, today=day + timedelta(days=1))

    with _client(db_url, login=False, role="api") as client:
        server = fakeredis.FakeServer()
        sync = fakeredis.FakeRedis(server=server, decode_responses=True)
        shared_cache.use(sync, fakeredis.FakeAsyncRedis(server=server, decode_responses=True))
        try:
            assert client.portal is not None
            assert client.portal.call(collect) is True
            assert contacts == [credit.FUNDS, credit.CREDIT]
            assert not api_cache.series
            assert client.get("/api/market/credit").status_code == 401
            assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204

            async def no_api_source(*args, **kwargs):
                contacts.append("api")
                raise AssertionError("the API tried to call KOFIA")

            monkeypatch.setattr(credit, "fetch_table", no_api_source)
            full = client.get("/api/market/credit?days=999")
            short = client.get("/api/market/credit?days=1")
            assert full.status_code == short.status_code == 200
            assert full.json() == collector.view(400)
            assert short.json() == collector.view(7)
            assert full.json()["source_date"] == day.isoformat()
            assert full.json()["refreshed_at"] == collector.refreshed_at
            assert contacts == [credit.FUNDS, credit.CREDIT]
        finally:
            sync.close()
