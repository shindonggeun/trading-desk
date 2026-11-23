"""Exercise the operator's forward cutover and the deployed factory on one DB."""

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from threading import Event
from unittest.mock import patch

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from desk import api, privacy_admin
from desk.account_owner import AccountOwnerStore
from desk.auth import Auth
from desk.broker.toss import TossBroker
from desk.db import (
    account_owner_binding,
    alembic_config,
    make_engine,
    migrate,
    now,
    owner_toss_attestations,
    privacy_policy,
    runs,
    users,
)
from desk.privacy import ISOLATED, LEGACY, PROTECTED, PolicyChanged, PolicyEpoch, PrivacyPolicyStore
from tests.test_api import PASSWORD

_OWNER = "synthetic-cutover-owner"
_PEER = "synthetic-cutover-peer"
_CLIENT = "synthetic-cutover-client"
_SECRET = "synthetic-cutover-secret"
_ACCOUNT = "0001245067"
_PRIVATE = "synthetic-cutover-private-content"


@pytest.fixture
def desk(db_url, tmp_path, monkeypatch):
    memory = tmp_path / "trading_memory.md"
    for name, value in {
        "DESK_DATABASE_URL": db_url,
        "DESK_ROLE": "api",
        "DESK_FAKE_RUNS": "true",
        "DESK_LOG_LEVEL": "WARNING",
        "DESK_BROKER": "toss",
        "TOSS_CLIENT_ID": _CLIENT,
        "TOSS_CLIENT_SECRET": _SECRET,
        "TOSS_ACCOUNT_SEQ": _ACCOUNT,
        "TRADINGAGENTS_MEMORY_LOG_PATH": str(memory),
    }.items():
        monkeypatch.setenv(name, value)

    migrate(db_url)
    engine = make_engine(db_url)
    try:
        timestamp = now()
        with engine.begin() as conn:
            conn.execute(users.insert(), [
                {"id": user_id, "username": username, "password_hash": "unused",
                 "created_at": timestamp, "password_changed_at": timestamp}
                for user_id, username in [(1, _PEER), (2, _OWNER)]
            ])
        owners = AccountOwnerStore(engine)
        assert owners.bind_verified(verified_user_id=2, expected_vacancy=owners.binding()) is not None
        yield engine, memory
    finally:
        engine.dispose()


def _invoke(capsys, *args, code=0):
    capsys.readouterr()
    assert privacy_admin.main(list(args)) == code
    captured = capsys.readouterr()
    assert not captured.err
    assert not any(value in captured.out for value in (_CLIENT, _SECRET, _ACCOUNT, _PRIVATE, _PEER, _OWNER))
    return json.loads(captured.out)


def _isolate(capsys):
    assert _invoke(capsys, "isolate", "--epoch", "0", "--confirm") == {"mode": ISOLATED, "epoch": 1}


def _challenge(capsys, epoch="1"):
    result = _invoke(capsys, "toss-approval-check", "--epoch", epoch)
    assert result == {"mode": ISOLATED, "epoch": int(epoch), "challenge": result["challenge"]}
    assert re.fullmatch(r"[0-9a-f]{64}", result["challenge"])
    return result["challenge"]


def _approve(capsys, challenge, *, epoch="1", code=0):
    with patch("sys.stdin", StringIO(f"{challenge}\n")):
        return _invoke(capsys, "approve-toss", "--epoch", epoch, "--confirm", code=code)


def _policy(engine):
    return PrivacyPolicyStore(engine).current()


def _approvals(engine):
    with engine.connect() as conn:
        return conn.execute(select(owner_toss_attestations)).all()


def test_cli_cutover_requires_cleanup_and_serves_only_the_attested_owner(desk, monkeypatch, capsys):
    engine, memory = desk
    timestamp = now()
    with engine.begin() as conn:
        base = {"ticker": "SYNTHETIC", "trade_date": "2026-01-01", "status": "done",
                "plan": {"decision": _PRIVATE}, "created_at": timestamp}
        conn.execute(runs.insert(), [
            dict(base, id="private-legacy", options={"use_portfolio": True}, is_shareable=None),
            dict(base, id="public-legacy", options={"use_portfolio": False}, is_shareable=True),
        ])
    memory.write_text(_PRIVATE, encoding="utf-8")
    public_memory = memory.with_name("trading_memory.public.md")
    public_memory.write_text(_PRIVATE, encoding="utf-8")

    auth = Auth(engine, timedelta(hours=1), timedelta(hours=1))
    assert auth.set_password(_PEER, PASSWORD)
    assert auth.set_password(_OWNER, PASSWORD)

    provider_accounts = []

    async def get(self, path, headers=None, params=None):
        if path == "/api/v1/holdings":
            provider_accounts.append(headers["X-Tossinvest-Account"])
            return {"items": [{"symbol": "SYNTHETIC", "marketCountry": "US", "currency": "USD",
                               "quantity": "3", "averagePurchasePrice": "100", "lastPrice": "120"}]}
        if path == "/api/v1/prices":
            return [{"symbol": "SYNTHETIC", "lastPrice": "120", "currency": "USD"}]
        assert path == "/api/v1/stocks/all"
        return []

    async def fx(self):
        return 1300

    monkeypatch.setattr(TossBroker, "_get", get)
    monkeypatch.setattr(TossBroker, "usd_krw", fx)

    with TestClient(api.create_app(), base_url="https://testserver") as legacy:
        assert legacy.get("/api/health").status_code == 200
        _isolate(capsys)
        assert legacy.get("/api/health").status_code == 503
        with pytest.raises(PolicyChanged):
            api.create_app()
        challenge = _challenge(capsys)
        assert _approvals(engine) == []
        assert _invoke(capsys, "activate", "--epoch", "1", "--confirm", code=1) == {
            "error": "toss_approval_mismatch",
        }
        assert _approve(capsys, challenge) == {"mode": ISOLATED, "epoch": 1}
        assert _approve(capsys, challenge) == {"mode": ISOLATED, "epoch": 1}
        assert _invoke(capsys, "activate", "--epoch", "1", "--confirm", code=1) == {
            "error": "legacy_cleanup_pending", "legacy_account_run_candidates": 1, "legacy_memory_files": 2,
        }
        assert _invoke(capsys, "hide-legacy", "--apply", "--epoch", "1", "--confirm")[
            "hidden_legacy_account_runs"
        ] == 1
        assert _invoke(capsys, "activate", "--epoch", "1", "--confirm", code=1) == {
            "error": "legacy_cleanup_pending", "legacy_account_run_candidates": 0, "legacy_memory_files": 2,
        }
        assert _policy(engine) == PolicyEpoch(ISOLATED, 1)
        if os.name == "posix":
            assert _invoke(capsys, "hide-legacy", "--archive-memory", "--apply", "--epoch", "1", "--confirm")[
                "archived_legacy_memory_files"
            ] == 2
        else:
            for active in (memory, public_memory):
                active.rename(active.with_suffix(".fixture-copy"))
        assert _invoke(capsys, "activate", "--epoch", "1", "--confirm") == {"mode": PROTECTED, "epoch": 2}
        assert legacy.get("/api/health").status_code == 503

    with TestClient(api.create_app(), base_url="https://testserver") as protected:
        login = "/api/auth/login"
        assert protected.post(login, json={"username": _PEER, "password": PASSWORD}).status_code == 204
        assert protected.get("/api/portfolio").status_code == 403
        assert provider_accounts == []
        assert protected.post(login, json={"username": _OWNER, "password": PASSWORD}).status_code == 204
        response = protected.get("/api/portfolio")
        assert response.status_code == 200
        assert response.json()["holdings"][0]["symbol"] == "SYNTHETIC"
        assert provider_accounts == [_ACCOUNT]
        assert protected.get("/api/runs/private-legacy").status_code == 404
        assert protected.get("/api/runs/public-legacy").status_code == 404
        assert _invoke(capsys, "rollback", "--epoch", "2", "--confirm") == {"mode": ISOLATED, "epoch": 3}
        assert protected.get("/api/health").status_code == 503
    with pytest.raises(PolicyChanged):
        api.create_app()
    assert _invoke(capsys, "isolate", "--epoch", "0", "--confirm", code=1) == {"error": "policy_mismatch"}
    assert _invoke(capsys, "activate", "--epoch", "3", "--confirm", code=1) == {"error": "toss_approval_mismatch"}
    assert _approve(capsys, challenge, epoch="3", code=1) == {"error": "toss_approval_mismatch"}
    assert _approve(capsys, _challenge(capsys, "3"), epoch="3") == {"mode": ISOLATED, "epoch": 3}
    assert _invoke(capsys, "activate", "--epoch", "3", "--confirm") == {"mode": PROTECTED, "epoch": 4}
    with engine.connect() as conn:
        assert conn.execute(select(runs.c.id, runs.c.is_shareable, runs.c.plan).order_by(runs.c.id)).all() == [
            ("private-legacy", False, {"decision": _PRIVATE}),
            ("public-legacy", True, {"decision": _PRIVATE}),
        ]


def test_isolation_preflight_does_not_migrate_or_change_an_unready_database(desk, db_url, monkeypatch, capsys):
    engine, memory = desk
    assert _invoke(capsys, "isolate", "--epoch", "0", code=2) == {"error": "invalid_arguments"}
    assert _invoke(capsys, "isolate", "--epoch", "2", "--confirm", code=1) == {"error": "policy_mismatch"}
    with monkeypatch.context() as changed:
        changed.setenv("TOSS_ACCOUNT_SEQ", " ")
        assert _invoke(capsys, "isolate", "--epoch", "0", "--confirm", code=1) == {
            "error": "toss_configuration_invalid",
        }
    command.downgrade(alembic_config(db_url), "0030")
    assert _invoke(capsys, "isolate", "--epoch", "0", "--confirm", code=1) == {"error": "schema_not_ready"}
    assert _policy(engine) == PolicyEpoch(LEGACY, 0)
    assert _invoke(capsys, "isolate", "--epoch", "0", "--confirm", code=1) == {"error": "schema_not_ready"}

    # Preserve the earlier rollback command even without the new approval schema or Toss credentials.
    with engine.begin() as conn:
        conn.execute(privacy_policy.update().values(mode=PROTECTED, epoch=2))
    monkeypatch.setenv("DESK_BROKER", "mock")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "")
    memory.mkdir()
    assert _invoke(capsys, "status", code=1) == {"error": "unsafe_memory_path"}
    assert _invoke(capsys, "status", "--policy-only") == {"mode": PROTECTED, "epoch": 2}
    assert _invoke(capsys, "status", "--policy-only", "--inspect-stdin", code=2) == {"error": "invalid_arguments"}
    assert _invoke(capsys, "rollback", "--epoch", "2", "--confirm") == {"mode": ISOLATED, "epoch": 3}


def test_approval_rejects_argv_empty_and_extra_input_without_echo(capsys):
    args = ("approve-toss", "--epoch", "1", "--confirm")
    for payload in ("", f"{'a' * 64}\n{_PRIVATE}", "a" * 65, "A" * 64, _PRIVATE * 100):
        with patch("sys.stdin", StringIO(payload)):
            assert _invoke(capsys, *args, code=2) == {"error": "invalid_arguments"}
    assert _invoke(capsys, *args, "--challenge", _PRIVATE, code=2) == {"error": "invalid_arguments"}


def test_binding_replacement_invalidates_the_challenge_and_approval(desk, capsys):
    engine, _ = desk
    owners = AccountOwnerStore(engine)
    with engine.begin() as conn:
        conn.execute(account_owner_binding.update().values(user_id=None))
    assert _invoke(capsys, "isolate", "--epoch", "0", "--confirm", code=1) == {
        "error": "owner_binding_unavailable",
    }
    original = owners.bind_verified(verified_user_id=2, expected_vacancy=owners.binding())
    assert original is not None
    _isolate(capsys)
    challenge = _challenge(capsys)
    with engine.begin() as conn:
        conn.execute(account_owner_binding.update().values(user_id=1))
    assert _approve(capsys, challenge, code=1) == {"error": "toss_approval_mismatch"}
    assert _approvals(engine) == []
    with engine.begin() as conn:
        conn.execute(account_owner_binding.update().values(user_id=None))
    assert _invoke(capsys, "toss-approval-check", "--epoch", "1", code=1) == {"error": "owner_binding_unavailable"}

    rebound_same_user = owners.bind_verified(verified_user_id=2, expected_vacancy=owners.binding())
    assert rebound_same_user is not None and rebound_same_user.generation != original.generation
    assert _approve(capsys, challenge, code=1) == {"error": "toss_approval_mismatch"}
    assert _approve(capsys, _challenge(capsys)) == {"mode": ISOLATED, "epoch": 1}
    with engine.begin() as conn:
        conn.execute(account_owner_binding.update().values(user_id=1))
    assert _invoke(capsys, "activate", "--epoch", "1", "--confirm", code=1) == {"error": "toss_approval_mismatch"}
    assert _policy(engine) == PolicyEpoch(ISOLATED, 1)


@pytest.mark.parametrize(("key", "value"), [
    ("TOSS_CLIENT_ID", "different-cutover-client"),
    ("TOSS_CLIENT_SECRET", "different-cutover-secret"),
    ("TOSS_ACCOUNT_SEQ", _ACCOUNT.lstrip("0")),
])
def test_source_drift_cannot_be_approved_or_activated(desk, monkeypatch, capsys, key, value):
    engine, _ = desk
    _isolate(capsys)
    challenge = _challenge(capsys)
    assert _invoke(capsys, "toss-approval-check", "--epoch", "3", code=1) == {"error": "policy_mismatch"}
    assert _approve(capsys, challenge, epoch="3", code=1) == {"error": "policy_mismatch"}
    for invalid, error, code in [(_PRIVATE, "invalid_arguments", 2), ("0" * 64, "toss_approval_mismatch", 1)]:
        assert _approve(capsys, invalid, code=code) == {"error": error}
    assert _approvals(engine) == []
    with monkeypatch.context() as changed:
        changed.setenv(key, value)
        assert _approve(capsys, challenge, code=1) == {"error": "toss_approval_mismatch"}
    assert _approvals(engine) == []
    assert _approve(capsys, challenge) == {"mode": ISOLATED, "epoch": 1}
    original = _approvals(engine)
    monkeypatch.setenv(key, value)
    assert _invoke(capsys, "toss-approval-check", "--epoch", "1", code=1) == {"error": "toss_approval_mismatch"}
    assert _invoke(capsys, "activate", "--epoch", "1", "--confirm", code=1) == {"error": "toss_approval_mismatch"}
    assert _policy(engine) == PolicyEpoch(ISOLATED, 1)
    assert _approvals(engine) == original


def test_edited_approval_epoch_cannot_activate(desk, capsys):
    engine, _ = desk
    _isolate(capsys)
    assert _approve(capsys, _challenge(capsys)) == {"mode": ISOLATED, "epoch": 1}
    assert _invoke(capsys, "activate", "--epoch", "1", "--confirm") == {"mode": PROTECTED, "epoch": 2}
    assert _invoke(capsys, "rollback", "--epoch", "2", "--confirm") == {"mode": ISOLATED, "epoch": 3}
    with engine.begin() as conn:
        conn.execute(owner_toss_attestations.update().values(approved_isolation_epoch=3))
    assert _invoke(capsys, "activate", "--epoch", "3", "--confirm", code=1) == {"error": "toss_approval_mismatch"}
    assert _policy(engine) == PolicyEpoch(ISOLATED, 3)


def test_unsafe_active_memory_path_leaves_the_policy_isolated(desk, capsys):
    engine, memory = desk
    _isolate(capsys)
    assert _approve(capsys, _challenge(capsys)) == {"mode": ISOLATED, "epoch": 1}
    memory.mkdir()
    assert _invoke(capsys, "activate", "--epoch", "1", "--confirm", code=1) == {"error": "unsafe_memory_path"}
    assert _policy(engine) == PolicyEpoch(ISOLATED, 1)


def test_activation_fences_policy_binding_and_approval_writes_through_the_cleanup_check(desk, monkeypatch, capsys):
    engine, _ = desk
    _isolate(capsys)
    assert _approve(capsys, _challenge(capsys)) == {"mode": ISOLATED, "epoch": 1}

    entered = Event()
    resume = Event()
    memory_count = privacy_admin._memory_count

    def pause_memory_check(path):
        entered.set()
        assert resume.wait(10)
        return memory_count(path)

    monkeypatch.setattr(privacy_admin, "_memory_count", pause_memory_check)
    with ThreadPoolExecutor(max_workers=1) as pool:
        activation = pool.submit(privacy_admin.main, ["activate", "--epoch", "1", "--confirm"])
        try:
            assert entered.wait(10)
            for table in (privacy_policy, account_owner_binding, owner_toss_attestations):
                key = next(iter(table.primary_key.columns))
                with engine.begin() as conn, pytest.raises(OperationalError) as blocked:
                    if conn.dialect.name == "postgresql":
                        conn.execute(select(key).with_for_update(nowait=True))
                    else:
                        conn.exec_driver_sql("PRAGMA busy_timeout=0")
                        conn.execute(table.update().values({key: key}))
                original = blocked.value.orig
                if engine.dialect.name == "postgresql":
                    assert (getattr(original, "sqlstate", None) or original.pgcode) == "55P03"
                else:
                    assert original.sqlite_errorname == "SQLITE_BUSY"
        finally:
            resume.set()
        assert activation.result(timeout=10) == 0
    assert json.loads(capsys.readouterr().out) == {"mode": PROTECTED, "epoch": 2}
