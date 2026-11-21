from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update

from desk import market_bus, paper
from desk.account_owner import AccountOwnerStore
from desk.api import create_app
from desk.auth import Auth
from desk.db import account_owner_binding, events, make_engine, now, run_submissions, runs, sessions, users
from desk.run_read import RunReader
from desk.runner import make_fake_runner
from desk.settings import Settings
from desk.store import AccountBindingChanged, Store
from tests.test_api import PASSWORD


@pytest.fixture
def fixture_desk(db_url, request):
    settings = Settings(database_url=db_url, fake_runs=True, fake_delay=0, role="api")
    app = create_app(settings, runner=make_fake_runner(0), protect_run_reads=getattr(request, "param", True))
    engine = make_engine(db_url)
    try:
        auth = Auth(engine, timedelta(hours=1), timedelta(hours=1))
        for name in ("first", "author", "bound-owner"):
            auth.create_user(name, PASSWORD)
        with engine.connect() as conn:
            ids = dict(conn.execute(select(users.c.username, users.c.id)).all())
        owners = AccountOwnerStore(engine)
        vacancy = owners.binding()
        assert vacancy is not None
        bound = owners.bind_verified(verified_user_id=ids["bound-owner"], expected_vacancy=vacancy)
        assert bound is not None
        records = [
            ("a-private", "done", False, "bound-owner", False),
            ("b-shared", "done", True, "first", True),
            ("c-legacy", "done", None, None, None),
            ("d-mine", "queued", None, "author", True),
            ("e-failed", "failed", None, "author", True),
            ("f-other", "running", None, "first", True),
            ("g-account", "failed", False, "author", False),
            ("h-completed", "done", None, "author", True),
        ]
        at = now()
        with engine.begin() as conn:
            for run_id, status, visible, submitter, proof in records:
                conn.execute(runs.insert().values(
                    id=run_id, ticker="TEST", trade_date="2026-11-20", status=status, created_at=at,
                    is_shareable=visible, options={"use_portfolio": not proof, "synthetic": True},
                    plan={"rating": "Hold", "summary": "synthetic body"}, error="synthetic error",
                ))
                conn.execute(events.insert(), [
                    {"run_id": run_id, "seq": 1, "ts": at, "type": "status", "content": "queued", "data": None},
                    {"run_id": run_id, "seq": 2, "ts": at, "type": "outlook", "content": None, "data": {"fixture": 42}},
                    {"run_id": run_id, "seq": 3, "ts": at, "type": "done", "content": "done", "data": None},
                ])
                if submitter is not None:
                    conn.execute(run_submissions.insert().values(
                        run_id=run_id, submitted_by_user_id=ids[submitter], non_account_submission=proof,
                        owner_generation=bound.generation if run_id == "a-private" else None,
                    ))
        with TestClient(app, base_url="https://testserver") as client:
            yield client, engine, ids
    finally:
        engine.dispose()


def _login(client, username):
    assert client.post("/api/auth/login", json={"username": username, "password": PASSWORD}).status_code == 204


def test_list_and_detail_keep_proof_and_private_fields_out_of_submitter_status(fixture_desk):
    client, _, _ = fixture_desk
    _login(client, "author")
    page = client.get("/api/runs?size=2")
    assert page.status_code == 200 and page.headers["cache-control"] == "private, no-store"
    data = page.json()
    assert data["total"] == 4
    assert [item["id"] for item in data["items"]] == ["b-shared", "d-mine"]
    assert data["items"][0]["plan"]["summary"] == "synthetic body"
    assert data["items"][0]["can_delete"] is False
    assert data["items"][1] == {"id": "d-mine", "status": "progress"}
    assert client.get("/api/runs?page=3&size=2").json() == {"items": [], "total": 4, "page": 3, "size": 2}
    for run_id, status in [("d-mine", "progress"), ("e-failed", "failed"), ("h-completed", "completed")]:
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200 and response.json() == {"id": run_id, "status": status}
    body = client.get("/api/runs/b-shared").json()
    assert body["outlook"] == {"fixture": 42}
    assert body["options"]["synthetic"] is True
    assert all(key not in body for key in ("is_shareable", "submitted_by_user_id", "non_account_submission"))


def test_body_routes_and_sse_replay_require_full_read(fixture_desk):
    client, _, _ = fixture_desk
    _login(client, "author")
    for run_id in ("a-private", "c-legacy", "f-other", "g-account", "absent"):
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 404 and response.json() == {"detail": "run not found"}
    for run_id in ("a-private", "d-mine", "e-failed", "absent"):
        for suffix in ("events", "stream"):
            response = client.get(f"/api/runs/{run_id}/{suffix}")
            assert response.status_code == 404 and response.json() == {"detail": "run not found"}
    replay = client.get("/api/runs/b-shared/events?after=1")
    assert replay.status_code == 200 and [row["seq"] for row in replay.json()] == [2, 3]
    stream = client.get("/api/runs/b-shared/stream", headers={"Last-Event-ID": "1"})
    assert stream.status_code == 200 and stream.headers["cache-control"] == "private, no-store"
    assert "id: 1\n" not in stream.text and "id: 2\n" in stream.text and "id: 3\n" in stream.text
    empty = client.get("/api/runs/b-shared/stream", headers={"Last-Event-ID": "3"})
    assert empty.status_code == 200 and not empty.content


def test_full_read_follows_explicit_binding_and_is_rechecked_for_a_new_batch(fixture_desk):
    client, engine, ids = fixture_desk
    _login(client, "first")
    assert client.get("/api/runs/a-private").status_code == 404
    listed = {row["id"]: row for row in client.get("/api/runs").json()["items"]}
    assert listed["b-shared"]["can_delete"] is True
    assert client.post("/api/auth/logout").status_code == 204
    _login(client, "bound-owner")
    page = client.get("/api/runs").json()
    assert page["total"] == 2
    assert {row["id"]: row["can_delete"] for row in page["items"]} == {"a-private": True, "b-shared": False}
    assert client.get("/api/runs/a-private").json()["plan"]["summary"] == "synthetic body"
    assert client.get("/api/runs/c-legacy").status_code == 404

    reader = RunReader(engine)
    first = reader.event_batch(ids["bound-owner"], "a-private", after=0, limit=1)
    assert first is not None and [row["seq"] for row in first.items] == [1]
    with engine.begin() as conn:
        conn.execute(update(account_owner_binding).values(user_id=None))
    assert reader.event_batch(ids["bound-owner"], "a-private", after=1, limit=1) is None
    assert client.get("/api/runs/c-legacy").status_code == 404
    assert client.get("/api/runs/b-shared").status_code == 200


def test_sse_drains_batches_that_include_events_after_done(fixture_desk):
    client, engine, _ = fixture_desk
    _login(client, "author")
    at = now()
    with engine.begin() as conn:
        conn.execute(events.insert(), [
            {"run_id": "b-shared", "seq": seq, "ts": at, "type": "explain", "content": None, "data": None}
            for seq in range(4, 106)
        ])
    response = client.get("/api/runs/b-shared/stream")
    assert response.status_code == 200
    assert [int(line[4:]) for line in response.text.splitlines() if line.startswith("id: ")] == list(range(1, 106))


@pytest.mark.parametrize(("fixture_desk", "protected"), [(True, True), (False, False)], indirect=["fixture_desk"])
@pytest.mark.parametrize("notify_waiter", [True, False], ids=["wake", "missed-notification"])
def test_sse_keeps_listener_when_done_status_precedes_done_event(fixture_desk, monkeypatch, protected, notify_waiter):
    client, engine, _ = fixture_desk
    _login(client, "author")
    if not notify_waiter:
        monkeypatch.setattr("desk.api.HEARTBEAT_SEC", 0.01)
    with engine.begin() as conn:
        conn.execute(delete(events).where(events.c.run_id == "b-shared", events.c.seq == 3))
    observed_terminal_gap = []

    def append_done(run_id):
        observed_terminal_gap.append(True)
        with engine.begin() as conn:
            conn.execute(events.insert().values(run_id=run_id, seq=3, ts=now(), type="done", content="done"))

    if protected:
        original_batch = RunReader.event_batch

        def append_after_batch(reader, viewer_id, run_id, *, after, limit=None):
            batch = original_batch(reader, viewer_id, run_id, after=after, limit=limit)
            if not observed_terminal_gap and batch is not None and not batch.items and batch.terminal_seq is None:
                assert batch.status == "done"
                append_done(run_id)
                if notify_waiter:
                    client.portal.call(client.app.state.manager.hub.notify, run_id)
            return batch

        monkeypatch.setattr(RunReader, "event_batch", append_after_batch)
    else:
        original_events = Store.events_after

        def append_after_events(store, run_id, after=0):
            items = original_events(store, run_id, after)
            if not observed_terminal_gap and not items:
                assert store.run_status(run_id) == "done"
                append_done(run_id)
                if notify_waiter:
                    client.app.state.manager.hub.notify(run_id)
            return items

        monkeypatch.setattr(Store, "events_after", append_after_events)
    response = client.get("/api/runs/b-shared/stream", headers={"Last-Event-ID": "2"})
    assert response.status_code == 200
    assert observed_terminal_gap == [True]
    assert "id: 3\n" in response.text



def test_protected_queue_records_owner_proof_and_accepts_ownerless_non_account_status(fixture_desk):
    client, engine, ids = fixture_desk
    _login(client, "bound-owner")
    created = client.post("/api/runs", json={"ticker": "OPEN", "name": "Synthetic", "use_portfolio": True})
    assert created.status_code == 201 and created.headers["cache-control"] == "private, no-store"
    body = created.json()
    assert body["status"] == "queued" and body["options"]["use_portfolio"] is True
    assert "queue" not in body and "owner_generation" not in body
    binding = AccountOwnerStore(engine).binding()
    assert binding is not None
    with engine.begin() as conn:
        proof = conn.execute(select(run_submissions).where(run_submissions.c.run_id == body["id"])).one()
        conn.execute(update(account_owner_binding).values(user_id=None))
    assert proof.submitted_by_user_id == ids["bound-owner"]
    assert proof.non_account_submission is False and proof.owner_generation == binding.generation
    assert client.get(f"/api/runs/{body['id']}").status_code == 404

    created = client.post("/api/runs", json={"ticker": "OPEN", "name": "Synthetic", "use_portfolio": True})
    assert created.status_code == 201
    run_id = created.json()["id"]
    assert created.json() == {"id": run_id, "status": "progress"}
    assert client.get(f"/api/runs/{run_id}").json() == created.json()
    with engine.connect() as conn:
        proof = conn.execute(select(run_submissions).where(run_submissions.c.run_id == run_id)).one()
        event = conn.execute(select(events).where(events.c.run_id == run_id)).one()
    assert proof.submitted_by_user_id == ids["bound-owner"]
    assert proof.non_account_submission is True and proof.owner_generation is None
    assert event.seq == 1 and event.data == {"waiting_for": "analyst"}


def test_stale_binding_rolls_back_submission_and_rebinding_cannot_inherit_body(fixture_desk):
    client, engine, ids = fixture_desk
    owners = AccountOwnerStore(engine)
    previous = owners.binding()
    assert previous is not None
    with engine.begin() as conn:
        conn.execute(update(account_owner_binding).values(user_id=None))
    vacancy = owners.binding()
    assert vacancy is not None
    replacement = owners.bind_verified(verified_user_id=ids["bound-owner"], expected_vacancy=vacancy)
    assert replacement is not None and replacement.generation != previous.generation
    store = Store(engine)
    with pytest.raises(AccountBindingChanged):
        store.create_run(
            "ROLLBACK", "2026-11-20", {"use_portfolio": True},
            guarded_writer=True, authenticated_submitter_id=ids["bound-owner"],
            first_event={"type": "status", "agent": "system", "content": "queued"}, owner_generation=previous.generation,
        )
    with engine.connect() as conn:
        assert conn.scalar(select(runs.c.id).where(runs.c.ticker == "ROLLBACK")) is None
        assert len(conn.execute(select(run_submissions)).all()) == 7
        assert len(conn.execute(select(events)).all()) == 24
    _login(client, "bound-owner")
    assert client.get("/api/runs/a-private").status_code == 404
    assert client.delete("/api/runs/a-private").status_code == 404
    assert client.get("/api/runs/b-shared").status_code == 200


def test_protected_run_derivatives_and_mutations_use_full_scope(fixture_desk, monkeypatch):
    client, engine, _ = fixture_desk
    store = Store(engine)
    monkeypatch.setattr("desk.api.explain.build", lambda run, *_: {"summary": run["id"]})
    _login(client, "author")
    for run_id in ("a-private", "c-legacy", "d-mine", "absent"):
        for path in (f"/api/runs/{run_id}/explain", f"/api/levels?ticker=TEST&run={run_id}",
                     f"/api/playbooks?run={run_id}"):
            denied = client.get(path)
            assert denied.status_code == 404 and denied.headers["cache-control"] == "private, no-store"
        assert client.delete(f"/api/runs/{run_id}").status_code == 404
    assert client.get("/api/runs/b-shared/explain").json() == {"summary": "b-shared"}
    assert client.get("/api/runs/b-shared/explain?refresh=true").status_code == 403
    assert store.last_event_data("b-shared", "explain") is None
    assert client.delete("/api/runs/b-shared").status_code == 404
    assert client.post("/api/auth/logout").status_code == 204
    _login(client, "bound-owner")
    assert client.get("/api/runs/a-private/explain?refresh=true").json() == {"summary": "a-private"}
    assert store.last_event_data("a-private", "explain") == {"summary": "a-private"}
    assert client.get("/api/runs/c-legacy/explain").status_code == 404
    assert client.delete("/api/runs/a-private").status_code == 204
    assert store.events_after("a-private") == []
    assert store.get_run("b-shared") is not None


def test_account_fetch_rechecks_revocation_before_and_after_await(fixture_desk, monkeypatch):
    client, engine, _ = fixture_desk
    manager = client.app.state.manager
    fetched = []

    async def snapshot_and_revoke():
        fetched.append(True)
        with engine.begin() as conn:
            conn.execute(update(account_owner_binding).values(user_id=None))
        return object()

    monkeypatch.setattr(manager.broker, "portfolio", snapshot_and_revoke)
    with pytest.raises(RuntimeError, match="account authorization changed"):
        client.portal.call(manager._run_portfolio, "c-legacy")
    assert not fetched
    with pytest.raises(RuntimeError, match="account authorization changed"):
        client.portal.call(manager._run_portfolio, "a-private")
    with pytest.raises(RuntimeError, match="account authorization changed"):
        client.portal.call(manager._run_portfolio, "a-private")
    assert fetched == [True]


def test_portfolio_cache_is_per_generation_and_history_is_closed(fixture_desk, monkeypatch):
    client, engine, ids = fixture_desk
    owners = AccountOwnerStore(engine)
    books = []

    async def snapshot(self):
        if self not in books:
            books.append(self)
        return {"synthetic": books.index(self)}

    monkeypatch.setattr("desk.api.Portfolio.snapshot", snapshot)
    _login(client, "bound-owner")
    first = client.get("/api/portfolio")
    assert first.json() == {"synthetic": 0} and first.headers["cache-control"] == "private, no-store"
    assert client.get("/api/portfolio").json() == first.json()
    assert client.get("/api/review").status_code == 403
    assert client.put("/api/holding-watch", json={}).status_code == 403
    assert client.get("/api/holding-watch").json()["holdings"] == []
    assert client.get("/api/chat/threads").status_code == 403
    assert client.post("/api/chat/threads", json={}).status_code == 403

    with engine.begin() as conn:
        conn.execute(delete(users).where(users.c.id == ids["bound-owner"]))
    assert client.get("/api/portfolio").status_code == 401
    _login(client, "first")
    assert client.get("/api/portfolio").status_code == 403
    vacancy = owners.binding()
    assert vacancy is not None and vacancy.user_id is None
    assert owners.bind_verified(verified_user_id=ids["first"], expected_vacancy=vacancy) is not None
    assert client.get("/api/portfolio").json() == {"synthetic": 1}
    assert client.get("/api/runs/a-private").status_code == 404


def test_public_derivative_inputs_and_paper_cache_exclude_unverified_runs(fixture_desk):
    client, engine, _ = fixture_desk
    with engine.begin() as conn:
        conn.execute(update(runs).where(runs.c.status == "done").values(scored_at=now(), outcome={}))
    store = Store(engine)
    assert [row["id"] for row in store.list_runs(public_only=True)] == ["b-shared"]
    assert [row["id"] for row in store.scored_since(now() - timedelta(days=1), public_only=True)] == ["b-shared"]
    separate = paper.Paper(engine, dict)
    protected = client.app.state.paper
    assert protected._stamp()[0] == 1
    assert {row["id"] for row in protected._runs()} == {"b-shared"}
    assert {row["id"] for row in separate._runs()} == {"a-private", "b-shared", "c-legacy", "h-completed"}



def test_protected_market_stream_skips_legacy_holding_alerts(fixture_desk, monkeypatch):
    client, _, _ = fixture_desk
    _login(client, "first")
    bus = market_bus.BUS
    original_snapshot, original_degraded = bus.snapshot, bus.degraded
    checks = 0

    def snapshot_and_publish(subscription):
        bus.publish("alert", "holding_alert", {"text": "restricted-synthetic"}, "desk")
        bus.publish("alert", "flow_shift", {"text": "public-synthetic"}, "desk")
        return original_snapshot(subscription)

    def degraded_and_stop(subscription):
        nonlocal checks
        checks += 1
        if checks == 2:  # The current event was already sent.
            subscription.overflowed = True
            subscription.queue.put_nowait(None)
        return original_degraded(subscription)

    monkeypatch.setattr(bus, "snapshot", snapshot_and_publish)
    monkeypatch.setattr(bus, "degraded", degraded_and_stop)
    response = client.get("/api/stream/market?channels=alert")
    assert response.status_code == 200
    assert "public-synthetic" in response.text and "restricted-synthetic" not in response.text



def test_protected_portfolio_rechecks_session_after_fetch(fixture_desk, monkeypatch):
    client, engine, ids = fixture_desk
    _login(client, "bound-owner")

    async def snapshot(_self):
        with engine.begin() as conn:
            conn.execute(delete(sessions).where(sessions.c.user_id == ids["bound-owner"]))
        return {"synthetic": "must-not-be-sent"}

    monkeypatch.setattr("desk.api.Portfolio.snapshot", snapshot)
    response = client.get("/api/portfolio")
    assert response.status_code == 401 and response.headers["cache-control"] == "private, no-store"
    assert response.json() == {"detail": "login required"}
