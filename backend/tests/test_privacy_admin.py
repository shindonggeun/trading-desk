"""Check the readiness CLI on synthetic records and an isolated memory path."""

import io
import json
import os
import sys
from pathlib import PurePosixPath

import pytest
from sqlalchemy import select

from desk import privacy_admin
from desk.db import (
    account_owner_binding,
    events,
    make_engine,
    market_alerts,
    metadata,
    now,
    privacy_policy,
    run_submissions,
    runs,
    users,
)
from desk.run_access import LEGACY_WRITER, PROTECTED_WRITER, VISIBILITY_WORKER, VISIBILITY_WRITER

PRIVATE = "private-readiness-canary"


@pytest.fixture
def readiness(db_url, tmp_path, monkeypatch):
    memory = tmp_path / "memory" / "trading_memory.md"
    monkeypatch.setenv("DESK_DATABASE_URL", db_url)
    monkeypatch.setenv("TRADINGAGENTS_MEMORY_LOG_PATH", str(memory))
    engine = make_engine(db_url)
    metadata.create_all(engine)
    timestamp = now()
    with engine.begin() as conn:
        conn.execute(users.insert(), [
            {"id": user_id, "username": f"{PRIVATE}-{user_id}", "password_hash": PRIVATE,
                 "created_at": timestamp, "password_changed_at": timestamp} for user_id in (1, 2)
        ])
        conn.execute(account_owner_binding.insert().values(id=1, user_id=1, generation="proof"))
        conn.execute(privacy_policy.insert().values(id=1, mode="isolated", epoch=3))
        for name, portfolio, share, writer, worker in (
            ("historical", True, True, None, None),
            ("legacy-unknown-share", True, None, LEGACY_WRITER, None),
            ("legacy-already-hidden", True, False, LEGACY_WRITER, None),
            ("unrecognized-writer", True, True, "8", None),
            ("protected-unproven", True, True, PROTECTED_WRITER, PROTECTED_WRITER),
            ("protected-current", True, False, PROTECTED_WRITER, PROTECTED_WRITER),
            ("non-account", False, True, None, None),
        ):
            options = {"use_portfolio": portfolio}
            if writer is not None:
                options[VISIBILITY_WRITER] = writer
            if worker is not None:
                options[VISIBILITY_WORKER] = worker
            conn.execute(runs.insert().values(
                id=name, ticker=PRIVATE[:20], trade_date="2026-01-01", status="done", options=options,
                plan={"decision": PRIVATE}, report_path=PRIVATE, created_at=timestamp, is_shareable=share,
            ))
        conn.execute(run_submissions.insert(), [
            {"run_id": "historical", "submitted_by_user_id": 1, "non_account_submission": False,
                 "owner_generation": "proof"},
            {"run_id": "protected-current", "submitted_by_user_id": 1, "non_account_submission": False,
                 "owner_generation": "proof"},
        ])
        conn.execute(events.insert().values(run_id="historical", seq=1, ts=timestamp, type="message",
                                            content=PRIVATE, data={"account": PRIVATE}))
        for kind in ("holding_alert", "sector_mover"):
            conn.execute(market_alerts.insert().values(
                trade_date="2026-01-01", kind=kind, rule="fixture", market="fixture", who="all",
                severity="info", at="09:00", text=PRIVATE, pushed=True, created_at=timestamp,
            ))
    try:
        yield engine, memory
    finally:
        engine.dispose()


def invoke(capsys, *args, code=0):
    assert privacy_admin.main(list(args)) == code
    captured = capsys.readouterr()
    assert PRIVATE not in captured.out + captured.err
    assert captured.err == ""
    return json.loads(captured.out)


def _memory_files(memory):
    return memory, memory.with_name("trading_memory.public.md")


def _snapshot(engine):
    tables = (users, account_owner_binding, privacy_policy, runs, run_submissions, events, market_alerts)
    with engine.connect() as conn:
        return {table.name: [dict(row) for row in conn.execute(select(table).order_by(*table.primary_key)).mappings()]
                for table in tables}


def test_status_and_default_dry_run_are_read_only(readiness, capsys):
    engine, memory = readiness
    memory.parent.mkdir()
    for path in _memory_files(memory):
        path.write_text(PRIVATE, encoding="utf-8")
    before = _snapshot(engine)
    assert invoke(capsys, "status") == {
        "account_rows": 2, "binding_occupied": True, "legacy_owner_matches_binding": True,
        "ownerless_account_runs": 5, "legacy_holding_alert_rows": 1, "legacy_memory_files": 2,
        "mode": "isolated", "epoch": 3, "role_configuration": "unverified",
    }
    assert invoke(capsys, "hide-legacy") == {
        "mode": "isolated", "epoch": 3, "legacy_account_run_candidates": 2, "legacy_memory_files": 2,
    }
    assert _snapshot(engine) == before
    assert all(path.read_text(encoding="utf-8") == PRIVATE for path in _memory_files(memory))


def test_status_reports_vacancy_and_inferred_owner_mismatch(readiness, capsys):
    engine, _memory = readiness
    with engine.begin() as conn:
        conn.execute(account_owner_binding.update().values(user_id=2))
    mismatch = invoke(capsys, "status")
    assert mismatch["binding_occupied"] is True
    assert mismatch["legacy_owner_matches_binding"] is False
    assert mismatch["ownerless_account_runs"] == 6
    with engine.begin() as conn:
        conn.execute(account_owner_binding.update().values(user_id=None))
    vacancy = invoke(capsys, "status")
    assert vacancy["binding_occupied"] is False
    assert vacancy["legacy_owner_matches_binding"] is None


def test_apply_hides_only_legacy_account_run_flags_and_is_idempotent(readiness, capsys):
    engine, memory = readiness
    memory.parent.mkdir()
    memory.write_text(PRIVATE, encoding="utf-8")
    before = _snapshot(engine)
    for run in before["runs"]:
        if run["id"] in ("historical", "legacy-unknown-share"):
            run["is_shareable"] = False
    args = ("hide-legacy", "--apply", "--epoch", "3", "--confirm")
    assert invoke(capsys, *args)["hidden_legacy_account_runs"] == 2
    assert invoke(capsys, *args)["hidden_legacy_account_runs"] == 0
    assert _snapshot(engine) == before
    assert memory.read_text(encoding="utf-8") == PRIVATE


def test_stale_epoch_and_incomplete_confirmation_do_not_write(readiness, capsys):
    engine, _memory = readiness
    before = _snapshot(engine)
    assert invoke(capsys, "hide-legacy", "--apply", "--epoch", "2", "--confirm", code=1) == {
        "error": "policy_mismatch",
    }
    assert invoke(capsys, "hide-legacy", "--apply", "--epoch", "3", code=2) == {"error": "invalid_arguments"}
    assert invoke(capsys, "rollback", "--epoch", "3", "--confirm", code=1) == {"error": "policy_mismatch"}
    assert _snapshot(engine) == before


def test_rollback_only_moves_expected_protected_epoch_to_isolated(readiness, capsys):
    engine, _memory = readiness
    with engine.begin() as conn:
        conn.execute(privacy_policy.update().values(mode="protected", epoch=4))
    assert invoke(capsys, "rollback", "--epoch", "3", "--confirm", code=1) == {"error": "policy_mismatch"}
    assert invoke(capsys, "rollback", "--epoch", "4", "--confirm") == {"mode": "isolated", "epoch": 5}
    assert invoke(capsys, "rollback", "--epoch", "4", "--confirm", code=1) == {"error": "policy_mismatch"}
    with engine.begin() as conn:
        conn.execute(privacy_policy.update().values(mode="legacy", epoch=0))
    assert invoke(capsys, "rollback", "--epoch", "0", "--confirm", code=1) == {"error": "policy_mismatch"}
    assert invoke(capsys, "status")["mode"] == "legacy"


def test_errors_do_not_echo_bad_arguments_database_or_input(tmp_path, monkeypatch, capsys):
    assert invoke(capsys, "rollback", "--epoch", PRIVATE, "--confirm", code=2) == {"error": "invalid_arguments"}
    db = tmp_path / PRIVATE / "missing.db"
    monkeypatch.setenv("DESK_DATABASE_URL", f"sqlite:///{db.as_posix()}")
    assert invoke(capsys, "status", code=1) == {"error": "unavailable"}
    assert not db.parent.exists()
    assert invoke(capsys, "hide-legacy", "--apply", "--epoch", "3", "--confirm", code=1) == {
        "error": "unavailable",
    }
    assert not db.parent.exists()
    monkeypatch.setattr(sys, "stdin", io.StringIO(PRIVATE))
    assert invoke(capsys, "status", "--inspect-stdin", code=1) == {"error": "role_configuration_mismatch"}


@pytest.mark.parametrize("locking_override", ["immutable=1", "nolock=1", "vfs=unix-none"])
def test_sqlite_locking_overrides_are_rejected_before_connecting(tmp_path, monkeypatch, capsys, locking_override):
    db = (tmp_path / f"{PRIVATE}.db").as_uri()
    monkeypatch.setenv("DESK_DATABASE_URL", f"sqlite:///{db}?uri=true&{locking_override}")

    def unexpected_engine(*_args, **_kwargs):
        pytest.fail("requested SQLite URI disables the locking contract")

    monkeypatch.setattr(privacy_admin, "create_engine", unexpected_engine)
    assert invoke(capsys, "hide-legacy", "--apply", "--epoch", "3", "--confirm", code=1) == {
        "error": "unavailable",
    }


def _inspect_record(role, index, dsn, memory):
    if role in ("all", "api"):
        listen = ["0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
    else:
        listen = ["127.0.0.1", "--port", "8000"]
    return {
        "Id": f"{index:064x}", "Image": "sha256:" + "9" * 64,
        "State": {"Running": True},
        "Config": {
            "User": "app", "Entrypoint": None,
            "Cmd": ["uvicorn", "desk.api:create_app", "--factory", "--host", *listen,
                    "--timeout-graceful-shutdown", "20"],
            "Env": [f"DESK_ROLE={role}", f"DESK_DATABASE_URL={dsn}", f"DESK_OTP_KEY={PRIVATE}",
                    f"TRADINGAGENTS_MEMORY_LOG_PATH={memory}"],
        },
        "Mounts": [{"Type": "volume", "Name": PRIVATE, "Source": f"/volumes/{PRIVATE}/_data",
                    "Destination": "/home/app/.tradingagents", "RW": True}],
    }


@pytest.fixture
def docker_inspect(readiness, monkeypatch):
    memory = PurePosixPath("/home/app/.tradingagents/memory/trading_memory.md")
    monkeypatch.setattr(privacy_admin, "_memory_path", lambda: memory)
    monkeypatch.setattr(privacy_admin, "_memory_count", lambda _path: 0)
    # Validate a synthetic PostgreSQL DSN without connecting to it. Run the
    # subsequent status queries only against the test database.
    fixture_dsn = os.environ["DESK_DATABASE_URL"]
    engine_factory = privacy_admin._engine
    monkeypatch.setattr(privacy_admin, "_engine", lambda _dsn, *, write: engine_factory(fixture_dsn, write=write))
    monkeypatch.setenv("DESK_DATABASE_URL", f"postgresql://{PRIVATE}:fixture@db.invalid/fixture")
    return memory


@pytest.mark.parametrize("roles,topology", [("all", "all"), ("api collector analyst", "split")])
def test_inspect_requires_matching_effective_topology(docker_inspect, monkeypatch, capsys, roles, topology):
    records = [_inspect_record(role, index, os.environ["DESK_DATABASE_URL"], docker_inspect)
               for index, role in enumerate(roles.split(), 1)]
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(records)))
    result = invoke(capsys, "status", "--inspect-stdin")
    assert result["role_configuration"] == topology


@pytest.mark.parametrize("mismatch", [
    "missing", "duplicate", "image", "dsn", "settings", "mount", "memory", "stopped", "paused", "command",
    "entrypoint", "sqlite", "code-overlay",
])
def test_inspect_rejects_incomplete_or_inconsistent_roles(docker_inspect, monkeypatch, capsys, mismatch):
    if mismatch == "sqlite":
        monkeypatch.setenv("DESK_DATABASE_URL", f"sqlite:///{PRIVATE}.db")
    records = [_inspect_record(role, index, os.environ["DESK_DATABASE_URL"], docker_inspect)
               for index, role in enumerate(("api", "collector", "analyst"), 1)]
    if mismatch == "missing":
        records.pop()
    elif mismatch == "duplicate":
        records[2]["Id"] = records[1]["Id"]
    elif mismatch == "image":
        records[2]["Image"] = "sha256:" + "a" * 64
    elif mismatch == "dsn":
        records[2]["Config"]["Env"][1] += PRIVATE
    elif mismatch == "settings":
        records[2]["Config"]["Env"][2] += PRIVATE
    elif mismatch == "mount":
        records[2]["Mounts"][0]["Source"] += PRIVATE
    elif mismatch == "memory":
        records[2]["Mounts"].append({"Type": "bind", "Source": f"/{PRIVATE}",
                                     "Destination": str(docker_inspect.parent), "RW": True})
    elif mismatch == "stopped":
        records[2]["State"]["Running"] = False
    elif mismatch == "paused":
        records[2]["State"]["Paused"] = True
    elif mismatch == "command":
        records[2]["Config"]["Cmd"] = ["sh", "-c", PRIVATE]
    elif mismatch == "entrypoint":
        records[2]["Config"]["Entrypoint"] = ["sh", "-c", PRIVATE]
    elif mismatch == "code-overlay":
        records[2]["Mounts"].append({"Type": "bind", "Source": f"/{PRIVATE}",
                                     "Destination": "/opt/venv", "RW": True})
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(records)))
    assert invoke(capsys, "status", "--inspect-stdin", code=1) == {"error": "role_configuration_mismatch"}


@pytest.mark.skipif(os.name != "posix", reason="archive permissions and descriptor-relative operations are POSIX")
def test_archive_is_idempotent_with_private_permissions_and_preserves_bytes(readiness, capsys):
    engine, memory = readiness
    memory.parent.mkdir()
    for source in _memory_files(memory):
        source.write_bytes(PRIVATE.encode())
        source.chmod(0o664)
    before = _snapshot(engine)
    args = ("hide-legacy", "--archive-memory", "--apply", "--epoch", "3", "--confirm")
    assert invoke(capsys, *args)["archived_legacy_memory_files"] == 2
    assert invoke(capsys, *args)["archived_legacy_memory_files"] == 0
    for source in _memory_files(memory):
        assert not source.exists()
        archive = source.with_name(f"{source.name}.legacy-archive")
        assert archive.read_bytes() == PRIVATE.encode()
        assert archive.stat().st_mode & 0o777 == 0o600
    assert _snapshot(engine) == before


@pytest.mark.skipif(os.name == "posix", reason="non-POSIX archive refusal")
def test_archive_on_non_posix_refuses_without_changes(readiness, capsys):
    engine, memory = readiness
    memory.parent.mkdir()
    memory.write_text(PRIVATE, encoding="utf-8")
    before = _snapshot(engine)
    assert invoke(capsys, "hide-legacy", "--archive-memory", "--apply", "--epoch", "3", "--confirm", code=1) == {
        "error": "archive_requires_posix",
    }
    assert memory.read_text(encoding="utf-8") == PRIVATE
    assert _snapshot(engine) == before


@pytest.mark.skipif(os.name != "posix", reason="POSIX filesystem guards")
@pytest.mark.parametrize("unsafe", [
    "parent-link", "source-link", "destination-link", "collision", "fifo", "writable-parent", "writable-ancestor",
])
def test_archive_refuses_unsafe_paths_before_any_move(readiness, capsys, unsafe, tmp_path):
    engine, memory = readiness
    outside = tmp_path / "outside"
    outside.mkdir()
    other = outside / "untouched"
    other.write_text(PRIVATE, encoding="utf-8")
    if unsafe == "parent-link":
        memory.parent.symlink_to(outside, target_is_directory=True)
    else:
        memory.parent.mkdir()
    memory.write_text(PRIVATE, encoding="utf-8")
    public = _memory_files(memory)[1]
    archive = public.with_name(f"{public.name}.legacy-archive")
    if unsafe == "source-link":
        public.symlink_to(other)
    elif unsafe == "destination-link":
        archive.symlink_to(other)
    elif unsafe == "collision":
        public.write_bytes(b"public")
        archive.write_bytes(b"preexisting")
        archive.chmod(0o600)
    elif unsafe == "fifo":
        os.mkfifo(public)
    elif unsafe == "writable-parent":
        memory.parent.chmod(0o775)
    elif unsafe == "writable-ancestor":
        memory.parent.parent.chmod(0o777)
    before = _snapshot(engine)
    args = ("hide-legacy", "--archive-memory", "--apply", "--epoch", "3", "--confirm")
    assert invoke(capsys, *args, code=1) == {"error": "unsafe_memory_path"}
    assert _snapshot(engine) == before
    assert memory.read_text(encoding="utf-8") == other.read_text(encoding="utf-8") == PRIVATE
    if unsafe == "collision":
        assert archive.read_bytes() == b"preexisting"
        assert public.read_bytes() == b"public"


@pytest.mark.skipif(os.name != "posix", reason="POSIX archive recovery")
def test_archive_retries_a_partial_move_without_replacing_the_archive(readiness, monkeypatch, capsys):
    engine, memory = readiness
    memory.parent.mkdir()
    memory.write_text(PRIVATE, encoding="utf-8")
    before = _snapshot(engine)
    args = ("hide-legacy", "--archive-memory", "--apply", "--epoch", "3", "--confirm")

    def interrupted(*_args, **_kwargs):
        raise OSError(PRIVATE)

    with monkeypatch.context() as patch:
        patch.setattr(os, "unlink", interrupted)
        assert invoke(capsys, *args, code=1) == {"error": "unavailable"}
    archive = memory.with_name(f"{memory.name}.legacy-archive")
    assert memory.stat().st_ino == archive.stat().st_ino
    assert invoke(capsys, *args)["archived_legacy_memory_files"] == 1
    assert not memory.exists()
    assert archive.read_text(encoding="utf-8") == PRIVATE
    assert _snapshot(engine) == before
