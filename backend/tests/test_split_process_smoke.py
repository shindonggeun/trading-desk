"""A loopback-only split-backend smoke on a dedicated PostgreSQL test database."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from desk import db
from desk.auth import Auth

LOOPBACK = "127.0.0.1"
START_TIMEOUT = 50
OWNER = "split-smoke@example.test"
PASSWORD = "split-smoke-fixture-only"
BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture
def split_db_url():
    raw = os.environ.get("DESK_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("split-process smoke requires the PostgreSQL test database")
    base = _require_test_db(raw)
    # Advisory locks survive table resets. Keep the production lock key in an isolated database.
    name = f"desk_split_{uuid4().hex}_test"
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
            try:
                yield base.set(database=name).render_as_string(hide_password=False)
            finally:
                conn.exec_driver_sql(f'DROP DATABASE "{name}"')
    finally:
        admin.dispose()


def _require_test_db(raw: str):
    message = "split-process smoke requires a direct loopback PostgreSQL *_test database"
    try:
        url = make_url(raw)
    except ArgumentError:
        raise ValueError(message) from None
    if (url.get_backend_name() != "postgresql" or url.host not in (LOOPBACK, "localhost") or url.query
            or os.environ.get("PGHOSTADDR") or os.environ.get("PGSERVICE")
            or not re.fullmatch(r"[a-z_][a-z0-9_]*_test(?:_gw[0-9]+)?", url.database or "")):
        raise ValueError(message)
    return url


@pytest.mark.parametrize("url", [
    "postgresql://remote.example.test/desk_test",
    "postgresql://localhost/desk",
    "postgresql://localhost/desk_test?hostaddr=192.0.2.1",
])
def test_split_smoke_refuses_non_test_database_destinations(url):
    with pytest.raises(ValueError, match="loopback PostgreSQL"):
        _require_test_db(url)


def _loopback_only():
    original = socket.getaddrinfo
    hosts = (LOOPBACK, "localhost", "::1")

    def resolve(host, *args, **kwargs):
        if host is not None and host not in hosts:
            raise AssertionError("external network is disabled in the split smoke")
        return original(host, *args, **kwargs)

    def guard(connect):
        def local(sock, address):
            if not isinstance(address, tuple) or address[0] not in hosts:
                raise AssertionError("external network is disabled in the split smoke")
            return connect(sock, address)

        return local

    socket.getaddrinfo = resolve
    socket.socket.connect = guard(socket.socket.connect)
    socket.socket.connect_ex = guard(socket.socket.connect_ex)


def _serve(role: str, port_file: Path):
    # Executed by a separate interpreter with an allowlisted environment and a temporary cwd.
    _require_test_db(os.environ["DESK_DATABASE_URL"])
    _loopback_only()
    import uvicorn
    from alembic import command

    from desk.api import create_app
    from desk.settings import Settings

    settings = Settings(database_url=os.environ["DESK_DATABASE_URL"], role=role, fake_delay=0,
                        cookie_secure=False, macro_interval_sec=0, report_every_days=0)
    if not settings.fake_runs:
        raise RuntimeError("split smoke requires fake runs")
    if role in ("collector", "analyst"):
        get_heads = MigrationContext.get_current_heads

        def deny_upgrade(*_args, **_kwargs):
            raise AssertionError("workers must never execute Alembic upgrade")

        def read_heads(context):
            if context.connection.exec_driver_sql("SHOW transaction_read_only").scalar_one() != "on":
                raise AssertionError("worker schema inspection must run in a read-only transaction")
            return get_heads(context)

        command.upgrade = deny_upgrade
        MigrationContext.get_current_heads = read_heads
    try:
        app = create_app(settings)
    except RuntimeError as exc:
        if role in ("collector", "analyst") and str(exc).startswith("워커 DB 스키마"):
            print("worker-schema-refused", flush=True)
            raise SystemExit(10) from None
        raise

    with socket.socket() as listener:
        listener.bind((LOOPBACK, 0))
        port_file.write_text(str(listener.getsockname()[1]), encoding="ascii")
        config = uvicorn.Config(app, host=LOOPBACK, access_log=False, log_level="warning")
        uvicorn.Server(config).run(sockets=[listener])


@contextmanager
def _process(root: Path, url: str, role: str, label: str):
    folder = root / label
    folder.mkdir()
    port_file = folder / "port"
    log = folder / "server.log"
    env = {name: os.environ[name] for name in
           ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL") if name in os.environ}
    env.update(PYTHONPATH=str(BACKEND), PYTHONIOENCODING="utf-8", DESK_FAKE_RUNS="true",
               DESK_DATABASE_URL=url, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    with log.open("wb") as output:
        child = subprocess.Popen(
            [sys.executable, "-B", str(Path(__file__).resolve()), role, str(port_file)],
            cwd=folder, env=env, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
        )
        try:
            yield child, port_file, log
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=10)


def _ready(process, client: httpx.Client, role: str) -> str:
    child, port_file, _log = process
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        if child.poll() is not None:
            pytest.fail(f"{role} exited before health (exit code {child.returncode})")
        if port_file.exists() and (port := port_file.read_text(encoding="ascii")).isdigit():
            base = f"http://{LOOPBACK}:{port}"
            try:
                if client.get(base + "/api/health", timeout=1).status_code == 200:
                    return base
            except httpx.TransportError:
                pass
        time.sleep(0.1)
    pytest.fail(f"{role} never became healthy")


def _heads(engine):
    with engine.connect() as connection:
        return set(MigrationContext.configure(connection).get_current_heads())


def _events(response):
    event_id = None
    deadline = time.monotonic() + 100
    for line in response.iter_lines():
        assert time.monotonic() < deadline, "timed out while reading the run SSE stream"
        if line.startswith("id: "):
            event_id = int(line[4:])
        elif line.startswith("data: "):
            event = json.loads(line[6:])
            assert event["seq"] == event_id
            yield event


def _listener_ready(client, base):
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        response = client.get(base + "/api/status")
        response.raise_for_status()
        jobs = {job["name"]: job for job in response.json()["jobs"]}
        if jobs["run_events"]["state"] == "fresh":
            return
        time.sleep(0.1)
    pytest.fail("API PostgreSQL listener did not become ready")


def test_postgresql_split_processes_prepare_claim_and_replay(tmp_path, split_db_url):
    url = split_db_url
    script = ScriptDirectory(str(db.MIGRATIONS))
    expected = set(script.get_heads())
    assert "0028" in {r.revision for r in script.walk_revisions()}
    engine = db.make_engine(url)
    try:
        assert not inspect(engine).has_table("alembic_version")
        for role in ("collector", "analyst"):
            with _process(tmp_path, url, role, role + "-unprepared") as (child, port, log):
                assert child.wait(timeout=START_TIMEOUT) == 10
                assert not port.exists()
                assert "worker-schema-refused" in log.read_text(encoding="utf-8")
            assert not inspect(engine).has_table("alembic_version"), f"{role} changed the unprepared schema"

        with httpx.Client(trust_env=False, timeout=60) as client, _process(tmp_path, url, "api", "api") as api:
            api_base = _ready(api, client, "api")
            assert _heads(engine) == expected
            Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(OWNER, PASSWORD)
            login = client.post(api_base + "/api/auth/login", json={"username": OWNER, "password": PASSWORD})
            assert login.status_code == 204
            _listener_ready(client, api_base)

            with _process(tmp_path, url, "collector", "collector") as collector:
                collector_base = _ready(collector, client, "collector")
                assert client.get(collector_base + "/api/status").json()["role"] == "collector"
                assert _heads(engine) == expected
                submitted = client.post(api_base + "/api/runs", json={"ticker": "NVDA", "use_portfolio": False})
                assert submitted.status_code == 201
                run = submitted.json()
                assert run["status"] == "queued" and run["queue"]["analyst"] == "down"
                run_url = api_base + "/api/runs/" + run["id"]

                with client.stream("GET", run_url + "/stream") as live:
                    live.raise_for_status()
                    events = _events(live)
                    first = next(events)
                    assert first["type"] == "status" and first["content"] == "queued"
                    with _process(tmp_path, url, "analyst", "analyst") as analyst:
                        analyst_base = _ready(analyst, client, "analyst")
                        assert client.get(analyst_base + "/api/status").json()["role"] == "analyst"
                        observed = [first, *events]
                        assert observed[-1]["type"] == "done"
                        assert any(e["type"] == "status" and e["content"] == "running" for e in observed)
                        assert any(e["type"] == "plan" for e in observed)
                        done = client.get(run_url).json()
                        assert done["status"] == "done" and done["plan"]
                        assert _heads(engine) == expected

                # The worker has stopped. Reconnect to the API and replay from the persisted cursor.
                with client.stream("GET", run_url + "/stream", headers={"Last-Event-ID": str(first["seq"])}) as replay:
                    replay.raise_for_status()
                    assert list(_events(replay)) == observed[1:]
                assert client.get(run_url + "/events").json() == observed
    finally:
        engine.dispose()


if __name__ == "__main__":
    _serve(sys.argv[1], Path(sys.argv[2]))
