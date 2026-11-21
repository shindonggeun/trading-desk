"""Run the #776 API replacement and rollback on an isolated local Docker project.

python3 scripts/tests/compose-api-isolation.py           # render only, no daemon
python3 scripts/tests/compose-api-isolation.py --run     # needs local python:3.12-slim

The execution path uses the local Unix socket and a disposable project. HTTP
and migration inputs are synthetic, so this measures Compose behavior without
running the backend application or contacting a database or market source.
"""

import argparse
import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
BASE_IMAGE = "python:3.12-slim"
PRODUCTION_PROJECT = "trading-desk-backend-prod"
ROLES = {"backend-prod": "api", "collector-prod": "collector", "analyst-prod": "analyst"}
BOOT, NEXT, BAD = (letter * 40 for letter in "abc")
HEALTH_PROBE = ["CMD", "python", "-c",
                "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"]
FIXTURE_FILES = {
    "Dockerfile": """
FROM python:3.12-slim
WORKDIR /fixture
COPY . .
ENV PYTHONPATH=/fixture
ARG FIXTURE_OK
ENV FIXTURE_OK=${FIXTURE_OK}
USER 65534:65534
CMD ["python", "/fixture/health.py"]
""",
    "health.py": """
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != '/api/health':
            self.send_error(404)
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({'ok': os.environ['FIXTURE_OK'] == '1'}).encode())

HTTPServer(('127.0.0.1', 8000), Health).serve_forever()
""",
    "desk/db.py": """
from contextlib import nullcontext
from types import SimpleNamespace

def make_engine(url):
    assert url == 'sqlite:///fixture-only.db'
    return SimpleNamespace(connect=nullcontext)

def alembic_config(url):
    return None
""",
    "desk/migrations/versions/__init__.py": "",
    "desk/migrations/versions/0001_fixture.py": "# Shared by all fixture images.\n",
    "alembic/runtime/migration.py": """
from types import SimpleNamespace

class MigrationContext:
    @staticmethod
    def configure(connection):
        return SimpleNamespace(get_current_heads=lambda: ('fixture-head',))
""",
    "alembic/script.py": """
from types import SimpleNamespace

class ScriptDirectory:
    @staticmethod
    def from_config(config):
        return SimpleNamespace(get_heads=lambda: ('fixture-head',))
""",
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(command, env, *, code=0):
    result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=240, check=False)
    require(result.returncode == code,
            f"{command[0]} returned {result.returncode}, expected {code}\n{result.stdout}{result.stderr}")
    return result.stdout


def compose(project, env_file, manifest):
    return ["docker", "compose", "-p", project, "--env-file", str(env_file),
            "--profile", "split", "-f", str(manifest)]


def prepare(directory, project, offline):
    env_file = directory / "fixture.env"
    env_file.write_text(
        f"DESK_BACKEND_SPLIT=1\nDESK_DOCKER_NETWORK={project}-desk\n"
        "DESK_DATABASE_URL=sqlite:///fixture-only.db\n",
        encoding="utf-8",
    )
    source = compose(project, env_file, ROOT / "backend/docker-compose-backend.yml")
    source_services = json.loads(
        run(source + ["config", "--format", "json"], {**offline, "DESK_API_ROLE": "api"})
    )["services"]
    require(set(source_services) == set(ROLES), "repository split service set changed")

    # Allowlist topology and the HTTP health command from the real source. Never inherit
    # hard-coded container names, bind mounts, volumes, environment or external networks.
    services = {}
    for service, role in ROLES.items():
        original = source_services[service]
        require(original["environment"]["DESK_ROLE"] == role, f"{service}: role changed")
        require(set(original["networks"]) == ({"desk"} if role == "api" else {"default"}),
                f"{service}: network changed")
        require(original["healthcheck"]["test"] == HEALTH_PROBE, f"{service}: loopback health probe changed")
        model = {
            "image": f"{project}:prod",
            "pull_policy": "never",
            "healthcheck": {"test": original["healthcheck"]["test"], "interval": "200ms",
                            "timeout": "2s", "retries": 10, "start_period": "1s"},
            "environment": {"DESK_ROLE": role, "DESK_DATABASE_URL": "sqlite:///fixture-only.db"},
            "networks": list(original["networks"]),
        }
        if role == "api":
            require(original["labels"]["desk.backend-split"] == "1", "API split label changed")
            model["labels"] = {"desk.backend-split": "1"}
        else:
            dependencies = original["depends_on"]
            require(original["profiles"] == ["split"] and set(dependencies) == {"backend-prod"}
                    and dependencies["backend-prod"]["condition"] == "service_healthy",
                    f"{service}: worker dependency changed")
            model.update(profiles=original["profiles"], depends_on=dependencies)
        services[service] = model

    manifest = directory / "backend/docker-compose-backend.yml"
    manifest.parent.mkdir()
    manifest.write_text(json.dumps({
        "services": services,
        "networks": {"desk": {"name": f"{project}-desk", "internal": True},
                     "default": {"internal": True}},
    }), encoding="utf-8")

    # A disposable copy changes only the fixed Compose project name, so the production
    # script's build/health/rollback decisions and Docker commands still run as written.
    runner = directory / "deploy-compose.sh"
    script = (ROOT / "scripts/deploy-compose.sh").read_text(encoding="utf-8")
    original_project = f"PROJECT={PRODUCTION_PROJECT};"
    require(script.count(original_project) == 1, "cannot safely isolate the deployment project")
    runner.write_text(script.replace(original_project, f"PROJECT={project};"), encoding="utf-8")
    command = compose(project, env_file, manifest)
    run(command + ["config", "-q"], offline)
    return command, runner, env_file


def snapshot(command, env):
    result = {}
    for service in ROLES:
        ids = run(command + ["ps", "-q", service], env).splitlines()
        require(len(ids) == 1 and ids[0], f"{service}: expected one running container")
        fields = run(["docker", "inspect", "--format",
                      "{{.Id}} {{.State.StartedAt}} {{.Image}} {{.RestartCount}} {{.State.Health.Status}}",
                      ids[0]], env).split()
        require(len(fields) == 5 and fields[-1] == "healthy", f"{service}: not healthy")
        result[service] = dict(zip(("id", "startedAt", "image", "restartCount", "health"), fields, strict=True))
    return result


def drill(directory, project, command, runner, env_file, env):
    # The caller/CI supplies the base image. The drill never pulls it.
    run(["docker", "image", "inspect", BASE_IMAGE, "--format", "{{.Id}}"], env)
    build = directory / "fixture"
    for name, content in FIXTURE_FILES.items():
        path = build / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.lstrip(), encoding="utf-8")

    tags = [f"{project}:{tag}" for tag in ("prod", NEXT, BAD)]
    try:
        for tag, sha, healthy in zip(tags, (BOOT, NEXT, BAD), ("1", "1", "0"), strict=True):
            run(["docker", "build", "--pull=false", "--network", "none", "-t", tag,
                 "--label", f"org.opencontainers.image.revision={sha}",
                 "--build-arg", f"FIXTURE_OK={healthy}", str(build)], env)
        run(command + ["up", "-d", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "25"], env)
        before = snapshot(command, env)

        def deploy(sha, code):
            image = run(["docker", "image", "inspect", f"{project}:{sha}", "--format", "{{.Id}}"], env).strip()
            settings = {**env, "ENV_FILE": str(env_file), "REVISION": sha, "STABLE_SEC": "0", "WAIT_TIMEOUT": "25"}
            output = run(["bash", str(runner), "backend", str(directory),
                          "--service", "api", "--image-id", image], settings, code=code)
            return image, output

        next_image, _ = deploy(NEXT, 0)
        updated = snapshot(command, env)
        require(updated["backend-prod"]["image"] == next_image, "API did not use the prepared image")
        require(updated["backend-prod"]["id"] != before["backend-prod"]["id"], "API was not recreated")
        _, output = deploy(BAD, 2)
        require("확인 실패: backend-prod 상태 확인(health) 응답이 없다" in output
                and "backend-prod 만 직전 이미지" in output, "expected HTTP failure and scoped rollback record")
        restored = snapshot(command, env)
        require(restored["backend-prod"]["image"] == next_image, "API did not recover its previous image")
        require(restored["backend-prod"]["id"] != updated["backend-prod"]["id"], "API rollback was not recreated")
        for service in ("collector-prod", "analyst-prod"):
            require(before[service] == updated[service] == restored[service],
                    f"{service} changed: {before[service]}, {updated[service]}, {restored[service]}")
        print(json.dumps({"before": before, "updated": updated, "restored": restored}, indent=2))
        print("API-only update and HTTP-failure rollback preserved both worker containers")
    finally:
        run(command + ["down", "--timeout", "3"], env)
        for tag in tags:
            result = subprocess.run(["docker", "image", "inspect", tag], env=env,
                                    capture_output=True, timeout=15, check=False)
            if result.returncode == 0:
                run(["docker", "image", "rm", tag], env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="use the local Unix Docker socket for the isolated drill")
    args = parser.parse_args()
    project = f"td-api-drill-{uuid4().hex[:12]}"
    with TemporaryDirectory(prefix=project) as raw_directory:
        directory = Path(raw_directory)
        # Ignore the caller's Docker contexts, credentials and implicit project env.
        docker_config = directory / "docker-cli"
        docker_config.mkdir()
        common = {"PATH": os.environ["PATH"], "DOCKER_CONFIG": str(docker_config)}
        offline = {**common, "DOCKER_HOST": f"unix://{directory / 'absent.sock'}"}
        command, runner, env_file = prepare(directory, project, offline)
        if not args.run:
            print("Isolated Compose fixture rendered (no Docker daemon contacted)")
            return
        socket = Path("/var/run/docker.sock")
        require(socket.is_socket(), "local Docker socket unavailable; use the hosted CI drill")
        drill(directory, project, command, runner, env_file, {**common, "DOCKER_HOST": f"unix://{socket}"})


if __name__ == "__main__":
    main()
