"""#995 who sees the process diagnostics in GET /api/status."""

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from desk import outbound
from desk.account_owner import AccountOwnerStore
from desk.api import create_app
from desk.auth import Auth
from desk.db import make_engine, migrate, users
from desk.privacy import ISOLATED, LEGACY, PROTECTED, OwnerResources, PolicyEpoch, PrivacyPolicyStore
from desk.runner import make_fake_runner
from desk.settings import Settings
from tests.test_api import OWNER, PASSWORD

OTHER = "status-other@example.com"
PAGE = {"now", "jobs", "cooldowns"}                       # what the page reads (frontend/src/api.ts DeskStatus)
DIAGNOSTICS = {"role", "shared_cache", "outbound", "demand", "market_stream", "loop_lag", "prewarm"}


def _app(db_url, protected: bool):
    migrate(db_url)
    engine = make_engine(db_url)
    auth = Auth(engine, timedelta(hours=1), timedelta(hours=1))
    auth.create_user(OTHER, PASSWORD)                      # the first account is not the bound owner
    auth.create_user(OWNER, PASSWORD)
    resources = None
    if protected:
        with engine.connect() as conn:
            owner_id = conn.scalar(select(users.c.id).where(users.c.username == OWNER))
        store = AccountOwnerStore(engine)
        binding = store.bind_verified(verified_user_id=owner_id, expected_vacancy=store.binding())
        assert binding is not None
        resources = OwnerResources(binding)
        policy = PrivacyPolicyStore(engine)
        policy.transition(policy.transition(PolicyEpoch(LEGACY, 0), ISOLATED), PROTECTED)
    engine.dispose()
    return create_app(Settings(database_url=db_url, fake_runs=True, fake_delay=0), runner=make_fake_runner(0),
                      protect_run_reads=protected, owner_resources=resources)


def _login(client, who: str) -> None:
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"username": who, "password": PASSWORD}).status_code == 204


def _status(client, who: str) -> dict:
    _login(client, who)
    response = client.get("/api/status")
    assert response.status_code == 200 and response.headers["cache-control"] == "private, no-store"
    return response.json()


@pytest.mark.parametrize("protected", [False, True])
def test_the_diagnostics_are_the_owners_in_protected_mode(db_url, protected):
    with TestClient(_app(db_url, protected), base_url="https://testserver") as client:
        assert client.get("/api/status").status_code == 401
        owner, other = _status(client, OWNER), _status(client, OTHER)
    assert set(owner) == PAGE | DIAGNOSTICS                    # the owner: everything, as before
    if protected:
        assert set(other) == PAGE                              # another account: only what the page reads
    else:
        assert set(other) == PAGE | DIAGNOSTICS                # open mode: unchanged
    assert isinstance(other["jobs"], list) and isinstance(other["cooldowns"], list) and other["now"]


@pytest.mark.parametrize("protected", [False, True])
def test_the_broker_calls_by_kind_are_inside_the_owners_diagnostics(db_url, protected):
    # #689 which Toss endpoints were called (holdings among them) is the owner's, with the totals
    outbound.reset()
    outbound.count("toss", "holdings")
    outbound.count("kis", "FHKIF03020100")
    with TestClient(_app(db_url, protected), base_url="https://testserver") as client:
        owner, other = _status(client, OWNER), _status(client, OTHER)
    assert owner["outbound"]["by_kind"]["toss"]["holdings"] >= 1 and owner["outbound"]["toss"] >= 1
    assert owner["outbound"]["by_kind"]["kis"]["FHKIF03020100"] >= 1
    if protected:
        assert "outbound" not in other and "by_kind" not in json.dumps(other)
    else:
        assert other["outbound"]["by_kind"]["toss"]["holdings"] >= 1


@pytest.mark.parametrize("protected", [False, True])
def test_the_per_route_timings_are_the_owners_in_protected_mode(db_url, protected):
    # #995 per-route counts would show another account the owner's account pages being read
    with TestClient(_app(db_url, protected), base_url="https://testserver") as client:
        _login(client, OWNER)
        assert client.get("/api/status").status_code == 200
        owner = client.get("/api/perf/timings")
        assert owner.status_code == 200 and "/api/status" in {r["route"] for r in owner.json()["routes"]}
        _login(client, OTHER)
        other = client.get("/api/perf/timings", params={"reset": 1})
        assert other.status_code == (403 if protected else 200)
        assert other.headers["cache-control"] == "private, no-store"
        _login(client, OWNER)
        kept = {r["route"] for r in client.get("/api/perf/timings").json()["routes"]}
        assert ("/api/status" in kept) is protected                    # another account's reset wiped nothing
