from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update

from desk.account_owner import AccountOwnerStore
from desk.api import create_app
from desk.auth import Auth
from desk.db import account_owner_binding, events, make_engine, now, run_submissions, runs, users
from desk.run_read import RunReader
from desk.runner import make_fake_runner
from desk.settings import Settings
from tests.test_api import PASSWORD


@pytest.fixture
def fixture_desk(db_url):
    settings = Settings(database_url=db_url, fake_runs=True, fake_delay=0, role="api")
    app = create_app(settings, runner=make_fake_runner(0), protect_run_reads=True)
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
        assert owners.bind_verified(verified_user_id=ids["bound-owner"], expected_vacancy=vacancy) is not None
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
    assert client.post("/api/auth/logout").status_code == 204
    _login(client, "bound-owner")
    assert client.get("/api/runs").json()["total"] == 8
    assert client.get("/api/runs/a-private").json()["plan"]["summary"] == "synthetic body"
    assert client.get("/api/runs/c-legacy").status_code == 200

    reader = RunReader(engine)
    first = reader.event_batch(ids["bound-owner"], "c-legacy", after=0, limit=1)
    assert first is not None and [row["seq"] for row in first.items] == [1]
    with engine.begin() as conn:
        conn.execute(update(account_owner_binding).values(user_id=None))
    assert reader.event_batch(ids["bound-owner"], "c-legacy", after=1, limit=1) is None
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


def test_sse_keeps_listener_when_done_status_precedes_done_event(fixture_desk, monkeypatch):
    client, engine, _ = fixture_desk
    _login(client, "author")
    with engine.begin() as conn:
        conn.execute(delete(events).where(events.c.run_id == "b-shared", events.c.seq == 3))
    original = RunReader.event_batch
    observed_terminal_gap = []

    def append_after_read(reader, viewer_id, run_id, *, after, limit=None):
        batch = original(reader, viewer_id, run_id, after=after, limit=limit)
        if not observed_terminal_gap and batch is not None and not batch.items and batch.terminal_seq is None:
            assert batch.status == "done"
            observed_terminal_gap.append(True)
            with engine.begin() as conn:
                conn.execute(events.insert().values(
                    run_id=run_id, seq=3, ts=now(), type="done", content="done",
                ))
            client.portal.call(client.app.state.manager.hub.notify, run_id)
        return batch

    monkeypatch.setattr(RunReader, "event_batch", append_after_read)
    response = client.get("/api/runs/b-shared/stream", headers={"Last-Event-ID": "2"})
    assert response.status_code == 200
    assert observed_terminal_gap == [True]
    assert "id: 3\n" in response.text
