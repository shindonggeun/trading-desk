"""The authenticated program sidecar uses the collector's snapshot across caches."""

from datetime import UTC, datetime

import fakeredis
import pytest

from desk import api, program_latest, shared_cache
from desk.db import make_engine
from desk.futures_history import FuturesHistory
from desk.program_latest import ProgramLatest
from tests.test_api import OWNER, PASSWORD, _client
from tests.test_futures_history import _view

AT = datetime(2026, 11, 10, 1, 0, 7, 900000, tzinfo=UTC)
PROGRAM = {"kospi": {"at": "10:00:00", "arbitrage": 2, "non_arbitrage": -2, "total": 0}, "kosdaq": None}


@pytest.fixture
def program_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return AT.astimezone(tz) if tz else AT.replace(tzinfo=None)

    monkeypatch.setattr(api, "datetime", Clock)
    monkeypatch.setattr(program_latest, "datetime", Clock)


def test_split_api_serves_collector_program_value_without_program_db_lookup(db_url, program_clock, monkeypatch):
    reads = []
    original = FuturesHistory.program

    def program(history, day):
        reads.append(day)
        return original(history, day)

    monkeypatch.setattr(FuturesHistory, "program", program)
    with _client(db_url, login=False, role="api") as client:
        srv = fakeredis.FakeServer()
        sync = fakeredis.FakeRedis(server=srv, decode_responses=True)
        async_ = fakeredis.FakeAsyncRedis(server=srv, decode_responses=True)
        shared_cache.use(sync, async_)
        writer_engine = make_engine(db_url)
        try:
            collector = ProgramLatest(FuturesHistory(writer_engine), snapshot_min=1, clock=lambda: AT)
            assert client.portal is not None
            assert client.portal.call(collector.record, _view(), None, PROGRAM)
            assert client.get("/api/market/flow?market=kospi").status_code == 401
            assert reads == []
            assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
            response = client.get("/api/market/flow?market=kospi")
            assert response.status_code == 200
            body = response.json()
            assert body["market"] == "KOSPI" and body["source"] == "mock" and body["records"]
            assert body["program"]["total"] == 0
            assert body["program"]["at"] == AT.isoformat(timespec="seconds")
            assert body["program"]["source"] == "kis" and body["program"]["fetched_at"] == AT.isoformat()
            assert datetime.fromisoformat(body["program"]["source_updated_at"]).astimezone(UTC).isoformat() == (
                "2026-11-10T01:00:00+00:00"
            )
            assert reads == []
            assert client.get("/api/market/flow?market=kosdaq").json()["program"] is None
            assert len(reads) == 1
            assert "program" not in client.get("/api/market/flow?market=kospi&interval=1w").json()
        finally:
            writer_engine.dispose()
            sync.close()


def test_program_uses_existing_daily_db_value_when_redis_is_disabled(db_url, program_clock):
    with _client(db_url, role="api") as client:
        engine = make_engine(db_url)
        try:
            assert FuturesHistory(engine).record(_view(), AT, None, PROGRAM)
            response = client.get("/api/market/flow?market=kospi")
            assert response.status_code == 200
            program = response.json()["program"]
            assert program["total"] == 0 and program["at"] == AT.isoformat(timespec="seconds")
            assert program["fetched_at"] == AT.isoformat()
            assert program["source_updated_at"] is None
        finally:
            engine.dispose()
