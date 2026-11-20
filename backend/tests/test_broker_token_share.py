"""#688 the brokers on the shared token store: one issue across processes and restarts, a reader that never
issues, a 403 (an IP Toss does not know) that asks for nothing. SQLite here, PostgreSQL in CI."""

import asyncio
import logging

import httpx
import pytest
from sqlalchemy import select

from desk import token_store
from desk.broker import TossBroker
from desk.broker.toss import TossError
from desk.db import broker_tokens, make_engine, migrate
from desk.futures_flow import KisRest
from desk.token_store import TokenStore
from tests.test_token_store import Clock
from tests.test_toss_token import FakeToss


@pytest.fixture
def engine(db_url, monkeypatch):
    monkeypatch.setattr(token_store, "FAIL_FAST_SEC", 0.0)
    migrate(db_url)
    eng = make_engine(db_url)
    yield eng
    eng.dispose()


def _toss(fake, engine, role, wait_sec=3.0, clock=None) -> TossBroker:
    store = TokenStore(engine, role=role, wait_sec=wait_sec, poll_sec=0.02, **({"clock": clock} if clock else {}))
    return TossBroker("id", "secret", tokens=store,
                      http=httpx.AsyncClient(base_url="https://toss.test", transport=httpx.MockTransport(fake)))


def _refresh_for(engine, name="toss"):
    with engine.connect() as conn:
        return conn.execute(select(broker_tokens.c.refresh_for).where(broker_tokens.c.name == name)).scalar_one()


def test_redeploying_twice_issues_no_new_toss_token(engine):
    fake = FakeToss()
    for _deploy in range(3):                     # the first start, then two redeploys of DESK_ROLE=all
        assert asyncio.run(_toss(fake, engine, "all").quote("NVDA")) == (181.5, "USD")
    assert fake.issued == 1


def test_a_reader_takes_the_collectors_new_token_after_a_401(engine, caplog):
    fake, clock = FakeToss(), Clock()
    collector, api = _toss(fake, engine, "collector", clock=clock), _toss(fake, engine, "api", clock=clock)

    async def scenario():
        assert await collector.keep_token(600)                      # t1
        assert await api.quote("NVDA") == (181.5, "USD")            # read from the store
        clock.tick(300)                                             # minutes later (Toss's 60 s gap is past)
        fake.revoke()                                               # someone else's token: t1 refused
        quote = asyncio.create_task(api.quote("NVDA"))              # 401 -> request -> wait
        while not await asyncio.to_thread(_refresh_for, engine):
            await asyncio.sleep(0.02)
        assert await collector.keep_token(600)                      # the collector answers: t2
        return await quote

    with caplog.at_level(logging.INFO):
        assert asyncio.run(scenario()) == (181.5, "USD")
    assert fake.issued == 2 and fake.calls[-1] == "t2"
    assert "t1" not in caplog.text and "t2" not in caplog.text


def test_a_reader_never_issues_even_with_no_token_at_all(engine):
    fake = FakeToss()
    with pytest.raises(TossError, match="unavailable"):
        asyncio.run(_toss(fake, engine, "analyst", wait_sec=0.1).quote("NVDA"))
    assert fake.issued == 0 and _refresh_for(engine) == token_store.MISSING


def test_a_403_asks_for_no_new_token(engine):
    fake = FakeToss()
    collector, api = _toss(fake, engine, "collector"), _toss(fake, engine, "api")
    asyncio.run(collector.keep_token(600))

    def forbidden(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return fake(request)
        return httpx.Response(403, json={"error": {"code": "forbidden-ip"}})

    api._http = httpx.AsyncClient(base_url="https://toss.test", transport=httpx.MockTransport(forbidden))
    with pytest.raises(TossError, match="403"):
        asyncio.run(api.quote("NVDA"))
    assert _refresh_for(engine) is None and fake.issued == 1


class FakeKis:
    def __init__(self):
        self.issued = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            self.issued += 1
            return httpx.Response(200, json={"access_token": f"k{self.issued}", "expires_in": 86400})
        return httpx.Response(200, json={"rt_cd": "0", "output": {}})


def _kis(fake, engine, role) -> KisRest:
    return KisRest("key", "secret", "https://kis.test", tokens=TokenStore(engine, role=role, wait_sec=3.0, poll_sec=0.02),
                   http=httpx.AsyncClient(transport=httpx.MockTransport(fake)))


def test_kis_rest_is_issued_once_and_read_by_another_process(engine):
    fake = FakeKis()
    assert asyncio.run(_kis(fake, engine, "collector").token()) == "k1"
    assert asyncio.run(_kis(fake, engine, "api").token()) == "k1"
    assert asyncio.run(_kis(fake, engine, "all").token()) == "k1"   # a restart reuses it
    assert fake.issued == 1
