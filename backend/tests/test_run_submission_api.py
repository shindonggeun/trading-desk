from secrets import randbits

import pytest
from sqlalchemy import event, func, select

from desk import run_queue
from desk.db import events, run_submissions, runs, users
from tests.test_api import OWNER, _client, _wait_done
from tests.test_owner_only import OTHER, _second_account
from tests.test_run_queue import _process

_SUBMISSION_FIELDS = {"authenticated_submitter_id", "submitted_by_user_id", "non_account_submission"}


@pytest.mark.parametrize("role", ["all", "api"])
@pytest.mark.parametrize("is_owner", [True, False])
def test_run_submission_uses_authenticated_account_and_final_portfolio_policy(db_url, monkeypatch, role, is_owner):
    monkeypatch.setattr(run_queue, "LOCK_KEY", randbits(63))
    with _client(db_url, role=role) as client:
        if not is_owner:
            _second_account(client, db_url)
        response = client.post("/api/runs", json={
            "ticker": "TEST",
            "trade_date": "2026-11-20",
            "use_portfolio": True,
            "authenticated_submitter_id": 999,
            "submitted_by_user_id": 999,
            "non_account_submission": is_owner,
        })
        assert response.status_code == 201
        run = response.json()
        run_id = run["id"]
        assert run["options"]["use_portfolio"] is is_owner
        assert _SUBMISSION_FIELDS.isdisjoint(run)
        assert _SUBMISSION_FIELDS.isdisjoint(run["options"])
        assert run["status"] == "queued"
        assert set(run["queue"]) == {"ahead", "running", "analyst"}

        with client.app.state.manager.store.engine.connect() as conn:
            user_id = conn.scalar(select(users.c.id).where(users.c.username == (OWNER if is_owner else OTHER)))
            proof = conn.execute(select(
                run_submissions.c.submitted_by_user_id,
                run_submissions.c.non_account_submission,
            ).where(run_submissions.c.run_id == run_id)).one()
            assert user_id is not None and tuple(proof) == (user_id, not is_owner)

        queued = client.get(f"/api/runs/{run_id}/events").json()[0]
        assert (queued["seq"], queued["type"], queued["agent"], queued["content"]) == (1, "status", "system", "queued")
        assert queued["data"] == ({"waiting_for": "analyst"} if role == "api" else None)
        if role == "api":
            with _process(db_url, "analyst"):
                completed = _wait_done(client, run_id)
        else:
            completed = _wait_done(client, run_id)
        assert completed["status"] == "done"
        assert _SUBMISSION_FIELDS.isdisjoint(completed)
        assert _SUBMISSION_FIELDS.isdisjoint(completed["options"])


@pytest.mark.parametrize("role", ["all", "api"])
def test_run_submission_rolls_back_run_proof_and_event_on_write_failure(db_url, role):
    with _client(db_url, role=role) as client:
        engine = client.app.state.manager.store.engine

        def abort_after_event(_conn, _cursor, statement, *_args):
            if statement.startswith("INSERT INTO events "):
                raise RuntimeError("synthetic failure after queued event write")

        event.listen(engine, "after_cursor_execute", abort_after_event)
        try:
            with pytest.raises(RuntimeError, match="synthetic failure after queued event write"):
                client.post("/api/runs", json={"ticker": "TEST", "trade_date": "2026-11-20"})
        finally:
            event.remove(engine, "after_cursor_execute", abort_after_event)

        with engine.connect() as conn:
            for table in (runs, run_submissions, events):
                assert conn.scalar(select(func.count()).select_from(table)) == 0
