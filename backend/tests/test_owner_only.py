"""#775 the broker account is the owner's: holdings, fills and the runs that read them - not another account's."""

import contextlib
import json
from dataclasses import asdict
from datetime import date, timedelta
from secrets import randbits

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from desk import api as api_module
from desk import auth as auth_module
from desk import run_queue
from desk.api import create_app
from desk.auth import Auth
from desk.db import make_engine, users
from desk.runner import RunRequest, make_fake_runner
from desk.settings import Settings
from desk.store import Store
from tests.test_api import OWNER, PASSWORD, _client, _wait_done

OTHER = "other@example.com"


def _second_account(client, db_url) -> None:
    engine = make_engine(db_url)
    Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(OTHER, PASSWORD)
    engine.dispose()
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"username": OTHER, "password": PASSWORD}).status_code == 204


def _trader(client, run_id) -> str:
    _wait_done(client, run_id)
    return next(e for e in client.get(f"/api/runs/{run_id}/events").json() if e["agent"] == "trader")["content"]


def test_the_owner_reads_the_account(db_url):
    with _client(db_url) as client:
        assert client.get("/api/portfolio").status_code == 200
        assert client.get("/api/review").status_code == 200
        run = client.post("/api/runs", json={"ticker": "NVDA", "use_portfolio": True})
        assert run.status_code == 201
        assert run.json()["options"]["use_portfolio"] is True
        assert "Current position in NVDA: 12 units" in _trader(client, run.json()["id"])


def test_another_account_never_gets_the_account(db_url):
    with _client(db_url) as client:
        assert client.get("/api/portfolio").status_code == 200
        assert client.get("/api/review?days=180").status_code == 200
        _second_account(client, db_url)
        for path in ("/api/portfolio", "/api/portfolio/stream", "/api/review", "/api/review?days=180"):
            got = client.get(path)
            assert got.status_code == 403, path
            assert got.json() == {"detail": "only the desk's owner sees the account"}, path   # one answer, nothing held
        # a run asked with the portfolio is run without it - no position reaches the agents or the events
        run = client.post("/api/runs", json={"ticker": "NVDA", "use_portfolio": True})
        assert run.status_code == 201
        assert run.json()["options"]["use_portfolio"] is False
        run_id = run.json()["id"]
        assert _wait_done(client, run_id)["status"] == "done"
        events = client.get(f"/api/runs/{run_id}/events").json()
        assert any(event["agent"] == "trader" for event in events)
        rendered = json.dumps(events)
        assert "Current position" not in rendered and "12 units" not in rendered
        # the rest of the desk is still theirs to use
        assert client.get("/api/runs").status_code == 200
        assert client.get("/api/holding-watch").json()["holdings"] == []


def test_the_owner_is_one_rule_for_the_whole_desk(db_url):
    """api (portfolio, review, runs, holding watch, alert channels) and market_alerts (pushes, the timeline)
    ask desk.auth.owner_id - the first account."""
    with _client(db_url) as client:
        engine = make_engine(db_url)
        Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(OTHER, PASSWORD)
        first = auth_module.owner_id(engine)
        assert first == Auth(engine, timedelta(hours=1), timedelta(hours=1)).owner_id()
        assert client.app.state.market_alerts.owner() == first
        engine.dispose()


QUEUED = {"type": "status", "agent": "system", "content": "queued"}


def _ids(client) -> set[str]:
    return {item["id"] for item in client.get("/api/runs?size=100").json()["items"]}


def test_another_account_cannot_read_list_stream_or_delete_the_owners_account_run(db_url):
    """#775 legacy: a run that read the account answers another account as a run that does not exist."""
    with _client(db_url) as client:
        account = client.post("/api/runs", json={"ticker": "NVDA", "use_portfolio": True}).json()["id"]
        shared = client.post("/api/runs", json={"ticker": "AMD"}).json()["id"]
        _wait_done(client, account)
        _wait_done(client, shared)
        _second_account(client, db_url)
        assert _ids(client) == {shared}
        assert client.get("/api/runs").json()["total"] == 1                 # the total counts what is listed
        missing = "000000000000"
        for path in ("/api/runs/{}", "/api/runs/{}/events", "/api/runs/{}/stream", "/api/runs/{}/explain",
                     "/api/levels?ticker=NVDA&run={}", "/api/playbooks?ticker=NVDA&run={}"):
            hidden, absent = client.get(path.format(account)), client.get(path.format(missing))
            assert hidden.status_code == 404, path
            assert (hidden.status_code, hidden.json()) == (absent.status_code, absent.json()), path
        stream = client.get(f"/api/runs/{account}/stream", headers={"Last-Event-ID": "1"})
        assert stream.status_code == 404 and "Current position" not in stream.text
        gone = client.delete(f"/api/runs/{account}")
        assert (gone.status_code, gone.json()) == (404, {"detail": "run not found"})
        assert client.get(f"/api/runs/{shared}").status_code == 200

        client.post("/api/auth/logout")
        assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
        assert _ids(client) == {account, shared}
        assert "Current position in NVDA: 12 units" in _trader(client, account)
        assert client.delete(f"/api/runs/{account}").status_code == 204


def test_other_accounts_keep_reading_runs_that_never_read_the_account(db_url):
    with _client(db_url) as client:
        owners = client.post("/api/runs", json={"ticker": "AMD"}).json()["id"]
        _wait_done(client, owners)
        _second_account(client, db_url)
        mine = client.post("/api/runs", json={"ticker": "NVDA", "use_portfolio": True}).json()["id"]
        _wait_done(client, mine)
        assert _ids(client) == {owners, mine}
        assert client.get("/api/runs").json()["total"] == 2
        for run_id in (owners, mine):
            assert client.get(f"/api/runs/{run_id}").json()["status"] == "done"
            assert any(e["agent"] == "trader" for e in client.get(f"/api/runs/{run_id}/events").json())
            assert '"content": "done"' in client.get(f"/api/runs/{run_id}/stream").text
        assert client.delete(f"/api/runs/{mine}").status_code == 204
        engine = make_engine(db_url)
        assert Store(engine).events_after(mine) == []    # the scoped delete takes the events with it
        engine.dispose()


def test_a_portfolio_run_another_account_made_before_the_rule_is_the_owners_now(db_url):
    """What the stored row says decides, not who asked: an account run from before #775 is the owner's."""
    with _client(db_url) as client:
        _second_account(client, db_url)
        engine = make_engine(db_url)
        with engine.connect() as conn:
            other = conn.scalar(select(users.c.id).where(users.c.username == OTHER))
        store = Store(engine)
        old = store.create_run("NVDA", "2026-11-20", {"ticker": "NVDA", "use_portfolio": True}, first_event=QUEUED,
                               guarded_writer=True, authenticated_submitter_id=other)
        store.mark_done(old, {"rating": "Hold"}, None)
        assert store.submitter_of(old) == other
        engine.dispose()
        assert old not in _ids(client)
        assert client.get(f"/api/runs/{old}").status_code == 404
        assert client.delete(f"/api/runs/{old}").status_code == 404
        client.post("/api/auth/logout")
        assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
        assert old in _ids(client)
        assert client.get(f"/api/runs/{old}").json()["plan"]["rating"] == "Hold"


@pytest.mark.parametrize("split", [False, True], ids=["all", "api+analyst"])
def test_only_the_owners_account_runs_keep_the_memory_that_holds_their_positions(db_url, monkeypatch, split):
    """#775 legacy runs keep TradingAgents' memory (a ticker's past decisions reach its next run's prompt). The
    default file has the owner's account runs' decisions: only those keep it, the owner's other runs keep a
    public one, and another account's run - or one with no recorded submitter - none, wherever it is claimed."""
    calls = []
    fake = make_fake_runner(0)

    def library(request, emit, portfolio, *, isolate_artifacts=False, public_memory=False):   # run_tradingagents
        calls.append((request.ticker, "none" if isolate_artifacts else "public" if public_memory else "account"))
        return fake(request, emit, portfolio)

    monkeypatch.setattr(api_module, "run_tradingagents", library)
    monkeypatch.setattr(run_queue, "LOCK_KEY", randbits(63))

    def app(role):
        return create_app(Settings(database_url=db_url, fake_runs=True, fake_delay=0, role=role), runner=library)

    api = app("api" if split else "all")              # migrates before the analyst starts
    with TestClient(api, base_url="https://testserver") as client, (
            TestClient(app("analyst"), base_url="https://testserver") if split else contextlib.nullcontext()):
        engine = make_engine(db_url)
        Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(OWNER, PASSWORD)
        assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
        _wait_done(client, client.post("/api/runs", json={"ticker": "NVDA", "use_portfolio": True}).json()["id"])
        _wait_done(client, client.post("/api/runs", json={"ticker": "META", "use_portfolio": False}).json()["id"])
        _second_account(client, db_url)
        _wait_done(client, client.post("/api/runs", json={"ticker": "AMD", "use_portfolio": True}).json()["id"])
        expected = [("NVDA", "account"), ("META", "public"), ("AMD", "none")]
        if split:     # only an analyst claims a run nobody submitted through the API
            unrecorded = Store(engine).create_run(
                "TSLA", date.today().isoformat(), asdict(RunRequest("TSLA", date.today().isoformat())),
                first_event=QUEUED, guarded_writer=True)
            assert Store(engine).submitter_of(unrecorded) is None
            _wait_done(client, unrecorded)
            expected.append(("TSLA", "none"))
        engine.dispose()
    assert calls == expected
