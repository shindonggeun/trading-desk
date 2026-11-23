#!/usr/bin/env python3
"""Fixed web-only Docker/Compose simulation for the sealed controller."""

import hashlib
import json
import re
import sys
from pathlib import Path

FIXTURE = Path("/opt/td-web-fixture")
STATE = Path("/var/jenkins_home/td-web-fixture-state.json")
REPO = "trading-desk-web"
IMAGE = f"{REPO}:prod"
PROJECT = "trading-desk-web-prod"
SERVICE = "web-prod"
LABEL = "org.opencontainers.image.revision"


def require(condition):
    if not condition:
        raise ValueError("unsupported web fixture operation")


def compose(args, state):
    cwd = Path.cwd()
    env_file = cwd / ".deploy-secrets" / "env"
    yaml = cwd / ".deploy-src" / "frontend" / "docker-compose-frontend.yml"
    require(args[:6] == ["-p", PROJECT, "--env-file", str(env_file), "-f", str(yaml)])
    require(yaml.is_file() and env_file.stat().st_mode & 0o777 == 0o600)
    require(env_file.read_text(encoding="ascii") == "DESK_DOCKER_NETWORK='td-web-fixture'\n")
    options = args[6:]
    if options == ["config", "-q"]:
        return
    if options == ["config", "--images"]:
        print(IMAGE)
        return
    if options == ["config", "--services"]:
        print(SERVICE)
        return
    if options == ["ps", "-a", "-q", SERVICE]:
        if state["container"]:
            print(state["container"]["id"])
        return
    require(len(options) == 9 and options[:6] == [
        "up", "-d", "--no-build", "--force-recreate", "--wait", "--wait-timeout",
    ])
    require(options[6].isdigit() and options[7:] == ["--remove-orphans", SERVICE])
    image = state["tags"][IMAGE]
    state["container"] = {"id": f"td-web-{len(state['events'])}", "image": image}
    state["events"].append(["up", image])


def running_image(state, container):
    running = state["container"]
    require(running and container in (running["id"], PROJECT))
    return running["image"]


def handle(args, state):
    if args[:1] == ["compose"]:
        return compose(args[1:], state)
    if args[:2] == ["inspect", "-f"] and len(args) == 4:
        image = running_image(state, args[3])
        formats = {
            "{{.Image}}": image,
            "{{.RestartCount}}": "0",
            f'{{{{index .Config.Labels "{LABEL}"}}}}': state["revisions"][image],
            "{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} {{.RestartCount}}":
                "running healthy 0",
        }
        print(formats[args[2]])
        return
    if args[:1] == ["build"] and len(args) == 8:
        _, dockerfile_flag, dockerfile, tag_flag, tag, label_flag, label, context = args
        directory = Path.cwd() / ".deploy-src" / "frontend"
        require((dockerfile_flag, tag_flag, label_flag) == ("-f", "-t", "--label"))
        require(dockerfile == str(directory / "frontend.Dockerfile") and context == str(directory))
        require(Path(dockerfile).is_file())
        revision = label.removeprefix(f"{LABEL}=")
        require(label.startswith(f"{LABEL}=") and re.fullmatch(r"[0-9a-f]{40}", revision))
        require(tag == f"{REPO}:{revision[:12]}")
        image = f"sha256:{hashlib.sha256(revision.encode('ascii')).hexdigest()}"
        state["tags"][tag] = image
        state["revisions"][image] = revision
        state["events"].append(["build", image])
        return
    if args[:1] == ["tag"] and len(args) == 3:
        _, source, destination = args
        require(destination in (IMAGE, f"{REPO}:rollback"))
        image = state["tags"].get(source, source)
        require(image in state["revisions"])
        state["tags"][destination] = image
        return
    if args[:1] == ["exec"] and len(args) == 7:
        require(args[2:] == ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1/"])
        image = running_image(state, args[1])
        rejected = (FIXTURE / "rejected-sha").read_text(encoding="ascii").strip()
        good = state["revisions"][image] != rejected
        state["events"].append(["probe", image, good])
        return 0 if good else 1
    if args[:3] == ["logs", "--tail", "60"] and len(args) == 4:
        running_image(state, args[3])
        return
    if args == ["images", "--format", "{{.Repository}}:{{.Tag}}", REPO]:
        print("\n".join(state["tags"]))
        return
    if args in (
        ["network", "inspect", "td-web-fixture"],
        ["image", "prune", "-f"],
        ["ps", "--filter", f"label=com.docker.compose.project={PROJECT}", "--format",
         r"table {{.Names}}\t{{.Status}}"],
    ):
        return
    raise ValueError("unsupported web fixture operation")


def main():
    state = {"tags": {}, "revisions": {}, "container": None, "events": []}
    if STATE.exists():
        state = json.loads(STATE.read_text(encoding="utf-8"))
    try:
        code = handle(sys.argv[1:], state)
    except (KeyError, OSError, ValueError):
        print("Web fixture rejected a Docker operation", file=sys.stderr)
        return 1
    STATE.write_text(json.dumps(state), encoding="utf-8")
    return code or 0


if __name__ == "__main__":
    sys.exit(main())
