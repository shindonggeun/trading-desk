"""#688 broker tokens shared by the desk's processes (desk.token_store) - SQLite here, PostgreSQL in CI."""

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from desk import token_store
from desk.db import broker_tokens, make_engine, migrate
from desk.token_store import MISSING, Issued, TokenStore, TokenUnavailable, token_hash

SECRET = "provider-secret"


@pytest.fixture
def engine(db_url, monkeypatch):
    monkeypatch.setattr(token_store, "FAIL_FAST_SEC", 0.0)
    migrate(db_url)
    eng = make_engine(db_url)
    yield eng
    eng.dispose()


class Clock:
    def __init__(self):
        self.at = datetime(2026, 11, 20, 1, 0, tzinfo=UTC)

    def __call__(self):
        return self.at

    def tick(self, sec):
        self.at += timedelta(seconds=sec)


class Issuer:
    """A provider: each issue is a new token; ``delay`` keeps an issue in flight."""

    def __init__(self, life=3600.0, delay=0.0, fail=False):
        self.count, self.life, self.delay, self.fail = 0, life, delay, fail

    async def __call__(self):
        self.count += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("refused")
        return Issued(f"tok-{self.count}", self.life)


def _store(engine, role, clock, **kw):
    return TokenStore(engine, role=role, clock=clock, wait_sec=kw.pop("wait_sec", 0.4), poll_sec=0.02, **kw)


def _row(engine, name="toss"):
    with engine.connect() as conn:
        return dict(conn.execute(select(broker_tokens).where(broker_tokens.c.name == name)).mappings().one())


def test_a_restart_reuses_the_stored_token_instead_of_issuing(engine):
    clock, issue = Clock(), Issuer()
    first = _store(engine, "all", clock).slot("toss", SECRET)
    assert asyncio.run(first.get(issue))[0] == "tok-1"
    again = _store(engine, "all", clock).slot("toss", SECRET)     # a deploy: a new process, the same row
    assert asyncio.run(again.get(issue, ahead=300))[0] == "tok-1"
    assert issue.count == 1
    row = _row(engine)
    assert row["issued_by"] == "all" and "tok-1" not in row["sealed"]       # sealed, not the value


def test_a_reader_never_issues_it_asks_and_takes_the_minters_token(engine):
    clock, issue = Clock(), Issuer()
    reader = _store(engine, "api", clock, wait_sec=3.0).slot("toss", SECRET)
    minter = _store(engine, "collector", clock).slot("toss", SECRET)
    with pytest.raises(TokenUnavailable):
        asyncio.run(_store(engine, "api", clock).slot("toss", SECRET).get(issue))
    assert issue.count == 0 and _row(engine)["refresh_for"] == MISSING

    async def both():
        waiting = asyncio.create_task(reader.get(issue))
        await asyncio.sleep(0.05)
        assert await minter.keep(issue, ahead=600)
        return await waiting

    assert asyncio.run(both())[0] == "tok-1"
    assert issue.count == 1 and _row(engine)["refresh_for"] is None


def test_a_refused_token_is_one_request_and_one_issue_after_the_gap(engine):
    clock, issue = Clock(), Issuer()
    minter = _store(engine, "collector", clock).slot("toss", SECRET)
    readers = [_store(engine, "api", clock).slot("toss", SECRET), _store(engine, "analyst", clock).slot("toss", SECRET)]
    asyncio.run(minter.keep(issue, ahead=600))                      # tok-1
    for r in readers:                                               # both see tok-1 refused (401)
        with pytest.raises(TokenUnavailable):
            asyncio.run(r.get(issue, refused="tok-1"))
    assert _row(engine)["refresh_for"] == token_hash("tok-1")
    assert not asyncio.run(minter.keep(issue, ahead=600))           # within the 60 s gap: nothing yet
    clock.tick(61)
    assert asyncio.run(minter.keep(issue, ahead=600))               # once
    assert not asyncio.run(minter.keep(issue, ahead=600))
    assert issue.count == 2
    assert asyncio.run(readers[0].get(issue, refused="tok-1"))[0] == "tok-2"   # no new request
    assert _row(engine)["refresh_for"] is None


def test_two_minters_at_once_issue_one_token(engine):
    clock, issue = Clock(), Issuer(delay=0.15)
    a = _store(engine, "all", clock).slot("toss", SECRET)
    b = _store(engine, "all", clock).slot("toss", SECRET)

    async def both():
        return await asyncio.gather(a.get(issue), b.get(issue))

    got = asyncio.run(both())
    assert issue.count == 1 and got[0][0] == got[1][0] == "tok-1"
    assert _row(engine)["lease_until"] is None


def test_a_refusal_inside_the_gap_fails_without_issuing(engine):
    clock, issue = Clock(), Issuer()
    minter = _store(engine, "all", clock).slot("toss", SECRET)
    asyncio.run(minter.get(issue))
    with pytest.raises(TokenUnavailable, match="waiting"):          # revoked again within a minute
        asyncio.run(minter.get(issue, refused="tok-1"))
    clock.tick(61)
    assert asyncio.run(minter.get(issue, refused="tok-1"))[0] == "tok-2"


def test_a_failed_issue_backs_off_and_frees_the_lease(engine):
    clock, issue = Clock(), Issuer(fail=True)
    minter = _store(engine, "collector", clock).slot("kis_rest", SECRET)

    def gap(refusals):
        return (60.0, 600.0)[min(refusals, 1)]

    with pytest.raises(RuntimeError):
        asyncio.run(minter.get(issue, gap=gap))
    row = _row(engine, "kis_rest")
    assert row["refusals"] == 1 and row["lease_until"] is None
    clock.tick(120)                                                 # past 60 s, inside the 600 s backoff
    with pytest.raises(TokenUnavailable):
        asyncio.run(minter.get(issue, gap=gap))
    assert issue.count == 1


def test_a_rotated_secret_reads_as_no_token_and_issues_again(engine, caplog):
    clock, issue = Clock(), Issuer()
    asyncio.run(_store(engine, "all", clock).slot("toss", SECRET).get(issue))
    clock.tick(120)                                                 # the restart that brings the new secret
    rotated = _store(engine, "all", clock).slot("toss", "a-new-secret")
    with caplog.at_level(logging.INFO, logger=token_store.__name__):
        assert asyncio.run(rotated.get(issue))[0] == "tok-2"
    assert "does not open" in caplog.text
    assert "tok-" not in caplog.text                                # never a value
    assert token_hash("tok-1") not in caplog.text


def test_on_demand_issues_only_what_a_reader_asked_for(engine):
    clock, issue = Clock(), Issuer()
    minter = _store(engine, "collector", clock).slot("kis_approval", SECRET)
    reader = _store(engine, "api", clock, wait_sec=0.05).slot("kis_approval", SECRET)
    assert not asyncio.run(minter.keep(issue, ahead=600, on_demand=True))   # nobody streams: no key
    assert issue.count == 0
    with pytest.raises(TokenUnavailable):
        asyncio.run(reader.get(issue))
    assert asyncio.run(minter.keep(issue, ahead=600, on_demand=True))
    assert asyncio.run(reader.get(issue))[0] == "tok-1"
    clock.tick(3600 - 300)                                          # 5 minutes left, nobody asked again
    assert asyncio.run(minter.keep(issue, ahead=600, on_demand=True)) is None
    assert issue.count == 1                                         # never renewed ahead of time


def test_a_token_near_expiry_is_renewed_ahead_and_unknown_names_are_refused(engine):
    clock, issue = Clock(), Issuer(life=1000)
    minter = _store(engine, "collector", clock).slot("toss", SECRET)
    asyncio.run(minter.keep(issue, ahead=600))
    assert not asyncio.run(minter.keep(issue, ahead=600))
    clock.tick(500)                                                 # 500 s left < 600 ahead
    assert asyncio.run(minter.keep(issue, ahead=600)) and issue.count == 2
    with pytest.raises(ValueError):
        _store(engine, "all", clock).slot("other", SECRET)


def test_a_reader_uses_a_token_near_its_end_at_once_and_asks_for_the_next(engine):
    clock, issue = Clock(), Issuer(life=1000)
    asyncio.run(_store(engine, "collector", clock).slot("toss", SECRET).keep(issue, ahead=600))
    clock.tick(800)                                                 # 200 s left: under the reader's 300
    reader = _store(engine, "api", clock, wait_sec=5.0).slot("toss", SECRET)
    started = time.monotonic()
    assert asyncio.run(reader.get(issue, ahead=300))[0] == "tok-1"
    assert time.monotonic() - started < 2.0                         # no wait
    assert _row(engine)["refresh_for"] == token_hash("tok-1")


def test_after_a_wait_in_vain_a_reader_still_reads_the_row(engine, monkeypatch):
    monkeypatch.setattr(token_store, "FAIL_FAST_SEC", 60.0)
    clock, issue = Clock(), Issuer()
    reader = _store(engine, "api", clock, wait_sec=0.05).slot("toss", SECRET)
    with pytest.raises(TokenUnavailable):
        asyncio.run(reader.get(issue))
    asyncio.run(_store(engine, "collector", clock).slot("toss", SECRET).keep(issue, ahead=600))
    assert asyncio.run(reader.get(issue))[0] == "tok-1"            # inside the fail-fast window


def test_a_fresh_token_refused_again_backs_off_longer(engine):
    clock, issue = Clock(), Issuer(life=20 * 3600)
    minter = _store(engine, "all", clock).slot("kis_rest", SECRET)

    def gap(refusals):
        return (60.0, 600.0, 1800.0)[min(refusals, 2)]

    asyncio.run(minter.get(issue, gap=gap))                         # tok-1
    clock.tick(61)
    with pytest.raises(TokenUnavailable):                           # refused right after its issue: 600 s
        asyncio.run(minter.get(issue, refused="tok-1", gap=gap))
    clock.tick(540)
    assert asyncio.run(minter.get(issue, refused="tok-1", gap=gap))[0] == "tok-2"
    clock.tick(61)
    with pytest.raises(TokenUnavailable):                           # tok-2 refused too: 1800 s now
        asyncio.run(minter.get(issue, refused="tok-2", gap=gap))
    clock.tick(1200)
    with pytest.raises(TokenUnavailable):
        asyncio.run(minter.get(issue, refused="tok-2", gap=gap))
    clock.tick(600)
    assert asyncio.run(minter.get(issue, refused="tok-2", gap=gap))[0] == "tok-3"
    assert _row(engine, "kis_rest")["refusals"] == 2
    clock.tick(20 * 3600)                                           # tok-3 served its day: an expiry
    assert asyncio.run(minter.get(issue, gap=gap))[0] == "tok-4"
    assert _row(engine, "kis_rest")["refusals"] == 0 and issue.count == 4


def test_a_minter_that_outran_its_lease_saves_nothing(engine):
    clock, fast = Clock(), Issuer()
    a = _store(engine, "all", clock).slot("toss", SECRET)
    b = _store(engine, "all", clock).slot("toss", SECRET)

    async def scenario():
        inside, go = asyncio.Event(), asyncio.Event()

        async def slow():
            inside.set()                                            # A asked the provider
            await go.wait()
            return Issued("slow-token", 3600)

        task = asyncio.create_task(a.get(slow))
        await inside.wait()
        clock.tick(token_store.LEASE_SEC + 61)                      # A's lease (and the gap) ran out
        got_b = await b.get(fast)
        go.set()
        with pytest.raises(TokenUnavailable, match="lease lost"):
            await task
        return got_b

    assert asyncio.run(scenario())[0] == "tok-1"
    row = _row(engine)
    assert row["lease_owner"] is None and row["lease_until"] is None
    assert asyncio.run(_store(engine, "all", clock).slot("toss", SECRET).get(fast))[0] == "tok-1"   # B's stands


def test_a_token_that_works_after_a_refusal_ends_the_backoff_run(engine):
    clock, issue = Clock(), Issuer(life=20 * 3600)
    minter = _store(engine, "all", clock).slot("kis_rest", SECRET)

    def gap(refusals):
        return (60.0, 600.0, 1800.0)[min(refusals, 2)]

    asyncio.run(minter.get(issue, gap=gap))                         # tok-1
    clock.tick(61)
    with pytest.raises(TokenUnavailable):
        asyncio.run(minter.get(issue, refused="tok-1", gap=gap))    # counted: refusals 1
    clock.tick(600)
    assert asyncio.run(minter.get(issue, refused="tok-1", gap=gap))[0] == "tok-2"
    clock.tick(3600)                                                # tok-2 works for an hour
    failing = Issuer(fail=True)
    with pytest.raises(RuntimeError):                               # one passing 5xx on a renewal
        asyncio.run(minter.get(failing, refused="tok-2", gap=gap))
    assert _row(engine, "kis_rest")["refusals"] == 1                # a fresh run of one, not the old run + 1
    clock.tick(601)                                                 # 600 s (as before #688), not 1800 s
    assert asyncio.run(minter.get(issue, refused="tok-2", gap=gap))[0] == "tok-3"


@pytest.mark.parametrize("role", ["all", "collector"])
def test_lost_issuer_lock_blocks_provider_calls_and_keeps_existing_token(engine, role):
    clock, issue = Clock(), Issuer(life=60)
    held = True
    slot = _store(engine, role, clock, can_issue=lambda: held).slot("toss", SECRET)
    assert asyncio.run(slot.get(issue))[0] == "tok-1"

    held = False
    assert asyncio.run(slot.get(issue))[0] == "tok-1"
    clock.tick(61)
    with pytest.raises(TokenUnavailable, match="issuer lock not held"):
        asyncio.run(slot.get(issue))
    assert asyncio.run(slot.keep(issue, ahead=300)) is None
    assert issue.count == 1
    assert _row(engine)["lease_owner"] is None
