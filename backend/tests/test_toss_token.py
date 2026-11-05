"""Toss keeps one valid token per client: a token issued elsewhere revokes ours (#50)."""

import asyncio

import httpx
import pytest

from desk.broker import TossBroker
from desk.broker.toss import TossError

REVOKED = {"error": {"code": "token-revoked", "message": "새로 발급된 토큰으로 대체되어 더 이상 유효하지 않은 토큰입니다."}}


class FakeToss:
    """Issues t1, t2, ...; ``revoke()`` makes Toss refuse every token issued so far."""

    def __init__(self, always_401=False):
        self.issued = 0
        self.valid: str | None = None
        self.always_401 = always_401
        self.calls: list[str] = []

    def revoke(self):
        self.valid = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            self.issued += 1
            self.valid = f"t{self.issued}"
            return httpx.Response(200, json={"access_token": self.valid, "expires_in": 3600})
        token = request.headers["authorization"].removeprefix("Bearer ")
        self.calls.append(token)
        if self.always_401 or token != self.valid:
            return httpx.Response(401, json=REVOKED)
        return httpx.Response(200, json={"result": [{"symbol": "NVDA", "lastPrice": "181.5", "currency": "USD"}]})


def _broker(fake: FakeToss) -> TossBroker:
    return TossBroker("id", "secret", http=httpx.AsyncClient(base_url="https://toss.test",
                                                                transport=httpx.MockTransport(fake)))


def test_revoked_token_is_renewed_once_and_the_call_succeeds():
    fake = FakeToss()
    broker = _broker(fake)

    async def scenario():
        assert await broker.quote("NVDA") == (181.5, "USD")
        fake.revoke()                                   # someone else issued a token
        assert await broker.quote("NVDA") == (181.5, "USD")
        assert await broker.quote("NVDA") == (181.5, "USD")
    asyncio.run(scenario())
    assert fake.issued == 2
    assert fake.calls == ["t1", "t1", "t2", "t2"]


def test_still_401_after_renewing_raises():
    fake = FakeToss(always_401=True)
    with pytest.raises(TossError, match="401"):
        asyncio.run(_broker(fake).quote("NVDA"))
    assert fake.issued == 2                             # one renewal, not a loop
    assert len(fake.calls) == 2


def test_concurrent_calls_on_a_revoked_token_renew_only_once():
    fake = FakeToss()
    broker = _broker(fake)

    async def scenario():
        await broker.quote("NVDA")
        fake.revoke()
        results = await asyncio.gather(*(broker.quote("NVDA") for _ in range(5)))
        assert all(r == (181.5, "USD") for r in results)
    asyncio.run(scenario())
    assert fake.issued == 2


def test_a_second_revocation_within_the_cooldown_fails_fast():
    fake = FakeToss()
    broker = _broker(fake)

    async def scenario():
        await broker.quote("NVDA")
        fake.revoke()
        await broker.quote("NVDA")                      # renewed once
        fake.revoke()                                   # the other client took it back at once
        with pytest.raises(TossError, match="another client"):
            await broker.quote("NVDA")
    asyncio.run(scenario())
    assert fake.issued == 2                             # no third token: the ping-pong stops here
