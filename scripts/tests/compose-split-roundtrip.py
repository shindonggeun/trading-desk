"""Run the production backend Compose definition in an isolated round-trip fixture."""

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "backend/docker-compose-backend.yml"
PROBE = Path(__file__).with_name("compose-split-roundtrip-probe.py")
SOCKET = Path("/var/run/docker.sock")
BACKEND = "backend-prod"
WORKERS = {"collector-prod": "collector", "analyst-prod": "analyst"}
APP_SERVICES = {BACKEND, *WORKERS}
POSTGRES = "fixture-db"
POSTGRES_IMAGE = "postgres:17"
PG_NAME = "compose_roundtrip_test"
PG_USER = "roundtrip"
PG_PASSWORD = "roundtrip-fixture-only"
DB_URL = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{POSTGRES}:5432/{PG_NAME}"
ROLE_COMMAND = [
    "uvicorn", "desk.api:create_app", "--factory", "--host", "127.0.0.1", "--port", "8000",
    "--timeout-graceful-shutdown", "20",
]
HEALTH_COMMAND = [
    "CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)",
]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


class Fixture:
    def __init__(self, directory, project, host):
        self.directory = directory
        self.project = project
        self.image = f"{project}:backend"
        self.desk = f"{project}-desk"
        self.default = f"{project}-workers"
        self.home_volume = f"{project}-tradingagents"
        self.db_volume = f"{project}-postgres"
        self.probe = PROBE.read_text(encoding="utf-8")
        config = directory / "docker-cli"
        config.mkdir()
        self.env = {"PATH": os.environ["PATH"], "DOCKER_CONFIG": str(config), "LC_ALL": "C.UTF-8"}
        self.host = host
        env_file = directory / "empty.env"
        env_file.touch()
        overlay = directory / "isolated.yml"
        overlay.write_text(json.dumps(self.overlay()), encoding="utf-8")
        self.compose = [
            "compose", "--project-name", project, "--env-file", str(env_file),
            "-f", str(MANIFEST), "-f", str(overlay),
        ]

    def name(self, service):
        return f"{self.project}-{service}"

    @property
    def docker(self):
        return ["docker", "--host", self.host]

    def overlay(self):
        services = {
            service: {
                "container_name": self.name(service), "image": self.image, "pull_policy": "never",
            }
            for service in APP_SERVICES
        }
        services[POSTGRES] = {
            "container_name": self.name(POSTGRES), "image": POSTGRES_IMAGE, "pull_policy": "never",
            "environment": {"POSTGRES_DB": PG_NAME, "POSTGRES_USER": PG_USER, "POSTGRES_PASSWORD": PG_PASSWORD},
            "networks": ["desk", "default"],
            "volumes": ["fixture_postgres:/var/lib/postgresql/data"],
            "healthcheck": {
                "test": ["CMD", "pg_isready", "-h", "127.0.0.1", "-U", PG_USER, "-d", PG_NAME],
                "interval": "1s", "timeout": "3s", "retries": 60,
            },
        }
        return {
            "services": services,
            "volumes": {
                "tradingagents_home": {"name": self.home_volume}, "fixture_postgres": {"name": self.db_volume},
            },
            "networks": {
                "desk": {"name": self.desk, "external": False, "internal": True},
                "default": {"name": self.default, "internal": True},
            },
        }

    def compose_env(self, split):
        return {
            **self.env, "DESK_DATABASE_URL": DB_URL, "DESK_DOCKER_NETWORK": self.desk,
            "DESK_BACKEND_SPLIT": "1" if split else "0", "DESK_API_ROLE": "api" if split else "all",
            "DESK_FAKE_RUNS": "true", "DESK_FAKE_DELAY": "0.05", "DESK_BROKER": "mock",
            "DESK_MARKET_STREAM": "off", "DESK_MACRO_INTERVAL_SEC": "0", "DESK_SCORE_INTERVAL_SEC": "0",
            "DESK_REPORT_EVERY_DAYS": "0", "DESK_RETENTION_DAYS": "0",
        }

    def compose_command(self, split, *args):
        profile = ["--profile", "split"] if split else []
        return [*self.docker, *self.compose, *profile, *args]

    def run(self, command, env=None, timeout=180):
        return subprocess.run(
            command, env=self.env if env is None else env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, check=False,
        )

    def command(self, *args, timeout=180):
        result = self.run([*self.docker, *args], timeout=timeout)
        require(result.returncode == 0, f"Docker {args[0]} failed ({result.returncode})\n{result.stderr}")
        return result.stdout

    def call(self, split, *args):
        result = self.run(self.compose_command(split, *args), self.compose_env(split))
        require(result.returncode == 0,
                f"Compose {args[0]} failed ({result.returncode})\n{result.stdout}{result.stderr}")
        return result.stdout

    def validate(self):
        for split in (False, True):
            model = json.loads(self.call(split, "config", "--format", "json"))
            expected_services = {BACKEND, POSTGRES, *WORKERS} if split else {BACKEND, POSTGRES}
            require(model["name"] == self.project and set(model["services"]) == expected_services,
                    "unexpected Compose project or service profile")
            networks = model["networks"]
            require(set(networks) == {"desk", "default"}, "unexpected Compose networks")
            for key, name in (("desk", self.desk), ("default", self.default)):
                network = networks[key]
                require(network["name"] == name and network.get("internal") is True and not network.get("external"),
                        f"{key}: network is not privately owned and internal")
            volumes = model["volumes"]
            require(set(volumes) == {"tradingagents_home", "fixture_postgres"}, "unexpected Compose volume")
            self.validate_volume(volumes["tradingagents_home"], self.home_volume)
            self.validate_volume(volumes["fixture_postgres"], self.db_volume)
            for service, definition in model["services"].items():
                self.validate_service(service, definition, split)

    @staticmethod
    def validate_volume(volume, name):
        require(volume["name"] == name and not any(volume.get(k) for k in ("external", "driver", "driver_opts")),
                "volume must be locally owned by the rehearsal")

    def validate_service(self, service, definition, split):
        forbidden = (
            "ports", "network_mode", "privileged", "devices", "device_cgroup_rules", "volumes_from",
            "pid", "ipc", "cap_add", "secrets", "configs", "env_file", "extra_hosts", "dns", "use_api_socket",
        )
        require(definition["container_name"] == self.name(service) and not any(definition.get(k) for k in forbidden),
                f"{service}: fixture allows only its named container and internal resources")
        fixture_db = service == POSTGRES
        require(definition["image"] == (POSTGRES_IMAGE if fixture_db else self.image), "unexpected image")
        require(definition["pull_policy"] == "never", "runtime image pulling is not allowed")
        volume, target = ("fixture_postgres", "/var/lib/postgresql/data") if fixture_db else (
            "tradingagents_home", "/home/app/.tradingagents",
        )
        mounts = definition["volumes"]
        require(len(mounts) == 1 and mounts[0]["type"] == "volume"
                and mounts[0]["source"] == volume and mounts[0]["target"] == target,
                f"{service}: unexpected mount")
        expected_nets = {"desk", "default"} if fixture_db else {"desk"} if service == BACKEND else {"default"}
        require(set(definition["networks"]) == expected_nets, f"{service}: unexpected network attachment")
        env = definition["environment"]
        if fixture_db:
            require(env == self.overlay()["services"][POSTGRES]["environment"], "unexpected database environment")
            return
        role = ("api" if split else "all") if service == BACKEND else WORKERS[service]
        require(env["DESK_ROLE"] == role and env["DESK_DATABASE_URL"] == DB_URL
                and env["DESK_BROKER"] == "mock" and env["DESK_FAKE_RUNS"] == "true"
                and env["DESK_COOKIE_SECURE"] == "true", f"{service}: unsafe fixture environment")
        credential = re.compile(r"(API_KEY|TOKEN|SECRET|PASSWORD|ACCOUNT_SEQ|CLIENT_ID|APP_KEY|DESK_REDIS_)", re.IGNORECASE)
        require(not any(value for key, value in env.items() if credential.search(key)),
                f"{service}: credentials or shared cache unexpectedly configured")
        require(definition["healthcheck"]["test"] == HEALTH_COMMAND, f"{service}: inherited health probe changed")
        if service == BACKEND:
            require(not definition.get("command") and not definition.get("entrypoint"),
                    "backend must use the production Dockerfile's entrypoint and command")
            require(definition["labels"]["desk.backend-split"] == ("1" if split else "0"), "unexpected split label")
        else:
            require(definition["profiles"] == ["split"] and definition["command"] == ROLE_COMMAND
                    and not definition.get("entrypoint"), f"{service}: inherited worker startup changed")
            dependencies = definition["depends_on"]
            require(set(dependencies) == {BACKEND} and dependencies[BACKEND]["condition"] == "service_healthy",
                    f"{service}: inherited API-healthy gate changed")

    def build(self):
        self.command("pull", POSTGRES_IMAGE, timeout=300)
        self.command("build", "-f", str(ROOT / "backend/backend.Dockerfile"), "-t", self.image,
                     str(ROOT / "backend"), timeout=900)

    def up(self, split, *services, options=()):
        self.call(split, "up", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "100", *options, *services)

    def services(self, split, expected):
        actual = set(self.call(split, "ps", "--all", "--services").splitlines())
        require(actual == {POSTGRES, *expected}, f"unexpected fixture services: {sorted(actual)}")

    def snapshot(self, service):
        raw = self.command("inspect", self.name(service))
        container, = json.loads(raw)
        state = container["State"]
        require(container["Config"]["Labels"].get("com.docker.compose.project") == self.project,
                f"{service}: container must belong to the fixture")
        require(state["Status"] == "running" and state["Health"]["Status"] == "healthy",
                f"{service}: container is not healthy")
        return {
            "id": container["Id"], "startedAt": state["StartedAt"],
            "restartCount": container["RestartCount"], "image": container["Image"],
        }

    def probe_args(self, service, *args):
        return ["exec", "-T", service, "python", "-u", "-c", self.probe, *args]

    def probe_call(self, split, service, *args):
        return json.loads(self.call(split, *self.probe_args(service, *args)))

    def check_empty_schema(self):
        result = self.call(False, "exec", "-T", POSTGRES, "psql", "-X", "-qAt", "-U", PG_USER, "-d", PG_NAME,
                           "-c", "SELECT to_regclass('public.alembic_version') IS NULL")
        require(result.strip() == "t", "the fresh fixture DB must have no Alembic schema")

    def check_worker_refusal(self):
        for service in WORKERS:
            result = self.run(self.compose_command(True, "run", "--rm", "--no-deps", "-T", service),
                              self.compose_env(True))
            output = result.stdout + result.stderr
            require(result.returncode != 0 and "워커 DB 스키마가 이미지와 일치하지 않습니다" in output,
                    f"{service}: unprepared schema must be refused\n{output}")
            self.check_empty_schema()

    def live_with_analyst(self, run_id):
        command = self.compose_command(True, *self.probe_args(BACKEND, "live", run_id))
        out_path = self.directory / "sse.stdout"
        err_path = self.directory / "sse.stderr"
        with (
            out_path.open("w", encoding="utf-8") as out,
            err_path.open("w", encoding="utf-8") as err,
            subprocess.Popen(command, env=self.compose_env(True), stdout=out, stderr=err) as stream,
        ):
            try:
                deadline = time.monotonic() + 35
                while time.monotonic() < deadline and stream.poll() is None:
                    first = out_path.read_text(encoding="utf-8").partition("\n")
                    if first[1]:
                        break
                    time.sleep(0.2)
                else:
                    raise RuntimeError("SSE failed to open before analyst startup\n" + err_path.read_text(encoding="utf-8"))
                queued = json.loads(first[0])["queued"]
                self.up(True, "analyst-prod", options=("--no-recreate",))
                require(stream.wait(timeout=75) == 0, "live SSE failed\n" + err_path.read_text(encoding="utf-8"))
            finally:
                if stream.poll() is None:
                    stream.kill()
        handshake, summary = map(json.loads, out_path.read_text(encoding="utf-8").splitlines())
        require(handshake["queued"] == queued == summary["first"], "SSE cursor does not identify the queued event")
        return summary

    def replay(self, split, run_id, expected):
        actual = self.probe_call(split, BACKEND, "replay", run_id, str(expected["first"]))
        require(actual == expected, "persisted SSE changed across a container or role transition")

    def roundtrip(self):
        self.up(False, POSTGRES)
        database = self.snapshot(POSTGRES)
        self.check_empty_schema()
        self.check_worker_refusal()
        self.up(False, BACKEND, options=("--no-deps",))
        self.services(False, {BACKEND})
        baseline = self.probe_call(False, BACKEND, "baseline", "all")
        initial_container = self.snapshot(BACKEND)
        first_run = self.probe_call(False, BACKEND, "direct", "AAPL")
        first_id = first_run.pop("run")
        print(f"Initial all role migrated PostgreSQL to {baseline['heads']} and processed its own run", flush=True)

        self.call(False, "stop", BACKEND)
        self.up(True, BACKEND, options=("--no-deps", "--force-recreate"))
        split_api = self.snapshot(BACKEND)
        require(split_api["id"] != initial_container["id"], "split API did not replace the all container")
        self.services(True, {BACKEND})
        require(self.probe_call(True, BACKEND, "role", "api")["heads"] == baseline["heads"],
                "schema must be ready before either split worker starts")
        self.replay(True, first_id, first_run)
        self.up(True, "collector-prod", options=("--no-recreate",))
        self.probe_call(True, "collector-prod", "role", "collector")
        split_id = self.probe_call(True, BACKEND, "queue", "NVDA")["run"]
        summary = self.live_with_analyst(split_id)
        self.probe_call(True, "analyst-prod", "role", "analyst")
        self.services(True, APP_SERVICES)
        require(self.snapshot(BACKEND) == split_api, "worker startup must keep the already healthy split API")
        print(f"Split analyst claimed the queued run and the API delivered {summary['count']} persisted events over SSE",
              flush=True)

        workers = {service: self.snapshot(service) for service in WORKERS}
        self.up(True, BACKEND, options=("--no-deps", "--force-recreate"))
        replacement = self.snapshot(BACKEND)
        require(replacement["id"] != split_api["id"], "API-only command did not recreate the API")
        require(replacement["image"] == split_api["image"]
                and all(entry["image"] == replacement["image"] for entry in workers.values()),
                "all roles must share the single fixture image")
        require(workers == {service: self.snapshot(service) for service in WORKERS},
                "API-only replacement changed a worker's container, start time, image or restart count")
        self.probe_call(True, BACKEND, "role", "api")
        self.replay(True, split_id, summary)
        print("API-only replacement kept both worker identities and resumed SSE from Last-Event-ID", flush=True)

        self.call(True, "stop", *WORKERS)
        self.call(True, "rm", "--force", *WORKERS)
        self.services(True, {BACKEND})
        self.up(False, BACKEND, options=("--no-deps", "--force-recreate"))
        self.services(True, {BACKEND})
        require(self.snapshot(BACKEND)["id"] != replacement["id"], "return to all did not replace the split API")
        require(self.snapshot(POSTGRES) == database, "the PostgreSQL container changed during the round trip")
        require(self.probe_call(False, BACKEND, "role", "all") == baseline,
                "return to all must preserve the database schema")
        self.replay(False, split_id, summary)
        self.probe_call(False, BACKEND, "direct", "MSFT")
        print("Return to all removed both split workers, preserved the split run and processed a new run", flush=True)

    def cleanup(self):
        self.call(True, "down", "--volumes")
        self.command("image", "rm", self.image)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="use the local Unix Docker socket for the rehearsal")
    args = parser.parse_args()
    project = f"td-split-roundtrip-{uuid4().hex[:12]}"
    with TemporaryDirectory(prefix=project) as temp:
        directory = Path(temp)
        fixture = Fixture(directory, project, f"unix://{directory / 'absent.sock'}")
        fixture.validate()
        print("Fixture Compose rendered with only private networks, containers and named volumes", flush=True)
        if not args.run:
            return
        require(SOCKET.is_socket(), "local Docker socket unavailable; use the hosted CI rehearsal")
        fixture.host = f"unix://{SOCKET}"
        fixture.build()
        try:
            fixture.roundtrip()
        except Exception:
            logs = fixture.run(fixture.compose_command(True, "logs", "--tail", "15"), fixture.compose_env(True))
            print(logs.stdout, logs.stderr, flush=True)
            raise
        finally:
            fixture.cleanup()


if __name__ == "__main__":
    main()
