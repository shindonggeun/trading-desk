"""Container-side HTTP and database assertions for the split round trip."""

import hashlib
import json
import os
import sys
import time
from datetime import timedelta

import httpx
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from desk import db
from desk.auth import COOKIE, Auth

OWNER = "compose-roundtrip@example.test"
PASSWORD = "compose-roundtrip-fixture-only"
BASE = "http://127.0.0.1:8000"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def check_schema(create_user=False):
    engine = db.make_engine(os.environ["DESK_DATABASE_URL"])
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            actual = set(MigrationContext.configure(conn).get_current_heads())
        expected = set(ScriptDirectory(str(db.MIGRATIONS)).get_heads())
        require(expected and actual == expected, "PostgreSQL is not at the image's Alembic heads")
        if create_user:
            Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(OWNER, PASSWORD)
        return sorted(actual)
    finally:
        engine.dispose()


def login(client):
    response = client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
    require(response.status_code == 204, f"fixture login returned HTTP {response.status_code}")
    cookie = response.cookies.get(COOKIE)
    require(cookie and "; secure" in response.headers["set-cookie"].lower(), "secure session cookie missing")
    # This test client is inside the container and can only target its loopback.
    client.headers["Cookie"] = f"{COOKIE}={cookie}"


def role_status(client, role):
    role_switches = {
        "all": {"run_events": True},
        "api": {"run_events": True, "tokens": False, "run_queue": False},
        "collector": {"run_events": False, "run_queue": False},
        "analyst": {"run_events": False, "tokens": False, "run_queue": True},
    }
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        response = client.get("/api/status")
        response.raise_for_status()
        status = response.json()
        require(status["role"] == role, f"unexpected application role, expected {role}")
        jobs = {job["name"]: job["state"] for job in status["jobs"]}
        for name, enabled in role_switches[role].items():
            require(name in jobs, f"{role}: missing {name}")
            if not enabled:
                require(jobs[name] == "off", f"{role}: {name} must be off")
        if all(jobs[name] == "fresh" for name, enabled in role_switches[role].items() if enabled):
            return
        time.sleep(0.25)
    raise RuntimeError(f"{role}: expected internal jobs did not become fresh")


def submit(client, ticker, queued):
    response = client.post("/api/runs", json={"ticker": ticker, "use_portfolio": False})
    require(response.status_code == 201, f"fake analysis submission returned HTTP {response.status_code}")
    run = response.json()
    if queued:
        require(run["status"] == "queued" and run["queue"]["analyst"] == "down",
                "split API must queue the run before the analyst starts")
    return run["id"]


def events(response):
    response.raise_for_status()
    require(response.headers.get("content-type", "").startswith("text/event-stream"), "expected an SSE response")
    event_id = None
    deadline = time.monotonic() + 150
    for line in response.iter_lines():
        require(time.monotonic() < deadline, "timed out while reading the run SSE stream")
        if line.startswith("id: "):
            event_id = int(line[4:])
        elif line.startswith("data: "):
            event = json.loads(line[6:])
            require(event["seq"] == event_id, "SSE event ID differs from the persisted sequence")
            yield event
            event_id = None


def persisted(client, run_url):
    run = client.get(run_url)
    run.raise_for_status()
    detail = run.json()
    require(detail["status"] == "done" and detail["plan"], "fake analysis did not complete with a plan")
    response = client.get(f"{run_url}/events")
    response.raise_for_status()
    values = response.json()
    require(values and values[-1]["type"] == "done", "persisted events do not end in done")
    require(any(event["type"] == "status" and event["content"] == "running" for event in values),
            "fake analysis never entered running")
    require(any(event["type"] == "plan" for event in values), "fake analysis never emitted its plan")
    sequences = [event["seq"] for event in values]
    require(sequences == sorted(set(sequences)), "persisted sequence is out of order or duplicated")
    payload = json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return values, {"first": sequences[0], "count": len(values), "digest": hashlib.sha256(payload).hexdigest()}


def observe(client, run_id, before_analyst=False, cursor=None):
    run_url = f"/api/runs/{run_id}"
    headers = {} if cursor is None else {"Last-Event-ID": cursor}
    with client.stream("GET", f"{run_url}/stream", headers=headers) as response:
        source = events(response)
        first = next(source)
        if before_analyst:
            require(first["type"] == "status" and first["content"] == "queued", "first SSE event must be queued")
            print(json.dumps({"queued": first["seq"]}), flush=True)
        observed = [first, *source]
    values, summary = persisted(client, run_url)
    expected = values if cursor is None else [event for event in values if event["seq"] > int(cursor)]
    require(observed == expected, "SSE did not match the persisted event sequence")
    return summary


def main():
    action, *args = sys.argv[1:]
    with httpx.Client(base_url=BASE, trust_env=False, timeout=45) as client:
        if action == "baseline":
            check_schema(create_user=True)
        login(client)
        if action in ("baseline", "role"):
            role_status(client, args[0])
            output = {"role": args[0], "heads": check_schema()}
        elif action == "queue":
            output = {"run": submit(client, args[0], queued=True)}
        elif action == "direct":
            run_id = submit(client, args[0], queued=False)
            output = {"run": run_id, **observe(client, run_id)}
        elif action == "live":
            output = observe(client, args[0], before_analyst=True)
        elif action == "replay":
            output = observe(client, args[0], cursor=args[1])
        else:
            raise ValueError(f"unknown probe action: {action}")
        print(json.dumps(output), flush=True)


if __name__ == "__main__":
    main()
