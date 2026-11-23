"""#775 every exit of the broker account in one table: the owner's holdings, fills, the runs that read them and
what is derived from those reach the owner and no other account - legacy (the deployed mode) and protected."""

import json
from datetime import date, datetime, timedelta
from secrets import randbits

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select

from desk import api as api_module
from desk import run_queue
from desk.account_owner import AccountOwnerStore
from desk.api import create_app
from desk.auth import Auth
from desk.broker.base import Fill, Holding
from desk.broker.mock import MockBroker
from desk.db import make_engine, market_alerts, migrate, now, users
from desk.futures_flow import KST
from desk.holding_watch import KIND as HOLDING_ALERT
from desk.privacy import ISOLATED, PROTECTED, OwnerResources, PrivacyPolicyStore
from desk.runner import make_fake_runner
from desk.settings import Settings
from tests.test_api import PASSWORD, _wait_done

OWNER, OTHER = "egress-owner", "egress-other"
HELD, QUANTITY, AVERAGE, FILL = "ZQXJ", 7919.25, 31337.42, "canary-fill"
SHARED = "AMD"                          # a run without the account
# what the account puts in a response: the raw numbers (JSON) and as the agents' prompt renders them
MARKS = (HELD, "31337.42", "31,337.42", "7919.25", "7,919", FILL)
TODAY = datetime.now(KST).date().isoformat()

# (method, path): every way out of the desk a second account can ask for; {run} = the owner's account run
EXITS = [
    ("GET", "/api/portfolio"),
    ("GET", "/api/portfolio/stream"),
    ("GET", "/api/review"),
    ("GET", "/api/review?days=180"),
    ("GET", "/api/runs"),
    ("GET", "/api/runs?size=100"),
    ("GET", "/api/runs/{run}"),
    ("GET", "/api/runs/{run}/events"),
    ("GET", "/api/runs/{run}/stream"),
    ("GET", "/api/runs/{run}/explain"),
    ("GET", f"/api/levels?ticker={HELD}&run={{run}}"),
    ("GET", f"/api/playbooks?ticker={HELD}&run={{run}}"),
    ("GET", f"/api/alerts/today?date={TODAY}"),
    ("GET", "/api/holding-watch"),
    ("GET", "/api/notify/channels"),
    ("GET", "/api/notify/line-watch"),
    ("GET", "/api/paper/summary"),
    ("GET", "/api/paper/trades"),
    ("GET", "/api/paper/equity"),
    ("GET", "/api/stats/plans"),
    ("CHAT", "recent_runs"),
    ("DELETE", "/api/runs/{run}"),      # last: the run must still be there for every row above
]
# the run-scoped exits answer exactly as for a run that does not exist
RUN_SCOPED = {"/api/runs/{run}", "/api/runs/{run}/events", "/api/runs/{run}/stream", "/api/runs/{run}/explain",
              f"/api/levels?ticker={HELD}&run={{run}}", f"/api/playbooks?ticker={HELD}&run={{run}}"}


class CanaryBroker(MockBroker):
    """The account: one holding and one fill no other part of the desk could make up."""

    async def holdings(self):
        return [Holding(HELD, HELD, "Canary holding", "US", "USD", QUANTITY, AVERAGE, AVERAGE * 1.01)]

    async def fills(self, start, end):
        day = date.today() - timedelta(days=3)
        fill = Fill(FILL, HELD, "BUY", QUANTITY, AVERAGE, 1.0, "USD", f"{day.isoformat()}T10:00:00+09:00")
        return [fill] if start <= day <= end else []

    async def quote(self, symbol):
        return AVERAGE * 1.01, "USD"

    async def usd_krw(self):
        return 1300.0


async def _chatter(_messages, tools, emit, *, model=None):
    """A scripted model that looks up the desk's recent runs and says what it found."""
    found = json.dumps(await tools["recent_runs"].run({}), ensure_ascii=False)
    emit({"type": "token", "text": found})
    return found, []


def _login(client, username):
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"username": username, "password": PASSWORD}).status_code == 204


@pytest.fixture(params=["legacy", "protected"])
def desk(request, db_url, monkeypatch):
    """The app in either mode, the owner (the first account; bound in protected mode) and a second account."""
    mode = request.param
    monkeypatch.setattr(run_queue, "LOCK_KEY", randbits(63))
    migrate(db_url)
    engine = make_engine(db_url)
    auth = Auth(engine, timedelta(hours=1), timedelta(hours=1))
    auth.create_user(OWNER, PASSWORD)
    auth.create_user(OTHER, PASSWORD)
    resources = None
    if mode == "legacy":
        monkeypatch.setattr(api_module, "make_broker", lambda _settings, _tokens: CanaryBroker())
    else:
        with engine.connect() as conn:
            owner_id = conn.scalar(select(users.c.id).where(users.c.username == OWNER))
        owners = AccountOwnerStore(engine)
        bound = owners.bind_verified(verified_user_id=owner_id, expected_vacancy=owners.binding())
        policy = PrivacyPolicyStore(engine)
        isolated = policy.transition(policy.current(), ISOLATED)
        assert bound is not None and isolated is not None and policy.transition(isolated, PROTECTED) is not None
        resources = OwnerResources(bound, broker=CanaryBroker())
    app = create_app(Settings(database_url=db_url, fake_runs=True, fake_delay=0), runner=make_fake_runner(0),
                     chatter=_chatter, protect_run_reads=mode == "protected", owner_resources=resources)
    try:
        with TestClient(app, base_url="https://testserver") as client:
            yield mode, client, engine
    finally:
        engine.dispose()


def _seed(mode, client, engine) -> str:
    """The owner reads the account: a portfolio run on the held ticker, the book, the fills, and a holding
    alert with its last lines (what the holding watch's pass leaves). And a run without the account, which every
    account reads - so the exits below answer with something, only not the account."""
    _login(client, OWNER)
    posted = client.post("/api/runs", json={"ticker": HELD, "use_portfolio": True})
    assert posted.status_code == 201, posted.text
    run_id = posted.json()["id"]
    shared = client.post("/api/runs", json={"ticker": SHARED}).json()["id"]
    assert _wait_done(client, run_id)["status"] == "done"
    assert _wait_done(client, shared)["status"] == "done"
    assert client.get("/api/portfolio").status_code == 200
    client.get("/api/review")
    if mode == "legacy":
        assert client.put("/api/holding-watch", json={"tickers": {HELD: {"max_loss_pct": 7}}}).status_code == 200
    client.app.state.holding_watch.last = {HELD: {"ticker": HELD, "average_price": AVERAGE, "stop": 29000.0}}
    with engine.begin() as conn:
        conn.execute(insert(market_alerts).values(
            trade_date=TODAY, kind=HOLDING_ALERT, rule="stop", market="us", who="all", severity="strong",
            at=f"{TODAY}T10:00:00+09:00", value=AVERAGE, prev=None, pushed=True, created_at=now(),
            text=f"{HELD} {QUANTITY:,.2f}주 평단 {AVERAGE:,.2f} 손절선 도달"))
    return run_id


def _walk(client, run_id, skip=()) -> dict[str, tuple[int, list[str], str]]:
    """Each exit's status, the account marks found in its body (SSE frames included) and the body."""
    seen = {}
    for method, path in EXITS:
        if f"{method} {path}" in skip:
            continue
        if method == "CHAT":
            thread = client.post("/api/chat/threads", json={})
            got = (thread if thread.status_code != 201 else
                   client.post(f"/api/chat/threads/{thread.json()['id']}/messages", json={"text": "최근 분석"}))
        else:
            got = client.request(method, path.format(run=run_id))
        seen[f"{method} {path}"] = (got.status_code, [mark for mark in MARKS if mark in got.text], got.text)
    return seen


def test_a_second_account_gets_no_account_data_on_any_path(desk):
    mode, client, engine = desk
    run_id = _seed(mode, client, engine)
    _login(client, OTHER)
    missing = {path: client.get(path.format(run="000000000000")) for path in RUN_SCOPED}

    seen = _walk(client, run_id)
    leaked = {exit_: marks for exit_, (_, marks, _) in seen.items() if marks}
    assert leaked == {}, f"{mode}: {leaked}"
    for path, absent in missing.items():
        status = seen[f"GET {path}"][0]
        assert (status, client.get(path.format(run=run_id)).json()) == (absent.status_code, absent.json()), path
    assert seen["DELETE /api/runs/{run}"][0] == 404
    listed = client.get("/api/runs").json()
    assert [item["ticker"] for item in listed["items"]] == [SHARED] and listed["total"] == 1
    # the lists answered - with the run that never read the account
    for exit_ in ("GET /api/runs", "GET /api/paper/trades") + (("CHAT recent_runs",) if mode == "legacy" else ()):
        assert seen[exit_][0] == 200 and SHARED in seen[exit_][2], exit_

    _login(client, OWNER)       # the second account's delete took nothing away
    assert client.get(f"/api/runs/{run_id}").status_code == 200


# where the account must show for its owner, so the table above cannot pass by finding nothing anywhere
OWNER_SEES = {
    "legacy": {"GET /api/portfolio", "GET /api/review", "GET /api/review?days=180", "GET /api/runs",
               "GET /api/runs?size=100", "GET /api/runs/{run}", "GET /api/runs/{run}/events",
               "GET /api/runs/{run}/stream", f"GET /api/alerts/today?date={TODAY}", "GET /api/holding-watch",
               "CHAT recent_runs", "GET /api/paper/trades"},
    "protected": {"GET /api/portfolio", "GET /api/runs", "GET /api/runs?size=100", "GET /api/runs/{run}",
                  "GET /api/runs/{run}/events", "GET /api/runs/{run}/stream"},
}


def test_the_owner_still_gets_the_account_on_the_same_paths(desk):
    mode, client, engine = desk
    run_id = _seed(mode, client, engine)
    events = json.dumps(client.get(f"/api/runs/{run_id}/events").json(), ensure_ascii=False)
    assert f"Current position in {HELD}: 7,919 units, average price 31,337.42" in events   # the agents read it
    # the owner's portfolio stream never ends while the session lives: GET /api/portfolio is its snapshot
    seen = _walk(client, run_id, skip={"GET /api/portfolio/stream"})
    marked = {exit_ for exit_, (_, marks, _) in seen.items() if marks}
    assert marked >= OWNER_SEES[mode], f"{mode}: {sorted(OWNER_SEES[mode] - marked)}"
    for path in RUN_SCOPED:
        assert seen[f"GET {path}"][0] != 404, path      # the guard lets the owner through
    assert seen["DELETE /api/runs/{run}"][0] == 204
