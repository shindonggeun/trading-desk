"""Validate rendered rehearsal Compose JSON without contacting Docker."""

import argparse
import json
import re
import sys

PROJECT = "td-jenkins-rehearsal"
NETWORKS = {
    "controller": ("control",),
    "agent": ("control", "scratch"),
    "scratch-docker": ("scratch",),
    "scm": ("control",),
}
MOUNTS = {
    "controller": {("controller-home", "/var/jenkins_home", False)},
    "agent": {
        ("agent-work", "/home/jenkins/agent", False),
        ("agent-auth", "/run/rehearsal-agent", True),
        ("scratch-client", "/run/scratch-docker-tls", True),
    },
    "scratch-docker": {
        ("scratch-data", "/var/lib/docker", False),
        ("scratch-certs", "/certs", False),
        ("scratch-client", "/certs/client", False),
    },
    "scm": {("scm-data", "/srv/git", True)},
}
ENVIRONMENT = {
    "agent": {
        "DOCKER_HOST": "tcp://scratch-docker:2376",
        "DOCKER_TLS_VERIFY": "1",
        "DOCKER_CERT_PATH": "/run/scratch-docker-tls",
    },
    "scratch-docker": {"DOCKER_TLS_CERTDIR": "/certs"},
}
COMMANDS = {
    "agent": [
        "-url", "http://controller:8080/", "-webSocket", "-name", "trading-desk-rehearsal",
        "-secret", "@/run/rehearsal-agent/secret", "-workDir", "/home/jenkins/agent",
    ],
    "scm": ["python", "-m", "http.server", "8081", "--bind", "0.0.0.0", "--directory", "/srv/git"],
}
USERS = {"controller": "1000:1000", "agent": "1000:1000", "scm": "65534:65534"}
SERVICE_FIELDS = {
    "image", "pull_policy", "profiles", "networks", "ports", "volumes", "privileged",
    "environment", "command", "entrypoint", "read_only", "security_opt", "user",
}
LOOPBACK_PORT = [
    {"mode": "ingress", "host_ip": "127.0.0.1", "target": 8080, "published": "18080", "protocol": "tcp"}
]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_mounts(name, mounts):
    actual = set()
    for mount in mounts:
        require(mount.get("type") == "volume", f"{name}: named volumes only")
        require(set(mount) <= {"type", "source", "target", "read_only", "volume"},
                f"{name}: unexpected mount options")
        require(not mount.get("volume"), f"{name}: unexpected volume options")
        actual.add((mount.get("source"), mount.get("target"), mount.get("read_only", False)))
    require(len(mounts) == len(actual) and actual == MOUNTS[name], f"{name}: mount boundary differs")


def validate(document, allow_privileged=False):
    require(set(document) <= {"name", "services", "networks", "volumes", "x-rehearsal"},
            "unexpected top-level Compose properties")
    require(document.get("name") == PROJECT, "unexpected project name")
    services = document.get("services", {})
    require(set(services) == set(NETWORKS), "expected only the four rehearsal services")
    internal_networks = document.get("networks", {})
    require(set(internal_networks) == {"control", "scratch"}, "unexpected network set")
    for name, network in internal_networks.items():
        require(network.get("name") == f"{PROJECT}_{name}" and network.get("internal") is True
                and set(network) <= {"name", "internal", "ipam"} and not network.get("ipam"),
                f"{name}: expected project-local internal network")

    declared_volumes = {source for mounts in MOUNTS.values() for source, _, _ in mounts}
    require(document.get("volumes") == {name: {"name": f"{PROJECT}_{name}"} for name in declared_volumes},
            "expected only project-owned named volumes without driver overrides")

    images = set()
    for name, service in services.items():
        require(set(service) <= SERVICE_FIELDS and service.get("entrypoint") is None,
                f"{name}: unexpected service properties")
        require(service.get("profiles") == ["rehearsal"], f"{name}: must require the rehearsal profile")
        image = service.get("image", "")
        require(re.fullmatch(r"sha256:[0-9a-f]{64}", image) and service.get("pull_policy") == "never",
                f"{name}: expected local image ID with pulling disabled")
        images.add(image)
        require(service.get("networks") == dict.fromkeys(NETWORKS[name]), f"{name}: network boundary differs")
        require(service.get("ports", []) == (LOOPBACK_PORT if name == "controller" else []),
                f"{name}: only controller loopback UI port may be published")
        scratch = name == "scratch-docker"
        require(service.get("privileged", False) is (scratch and allow_privileged),
                f"{name}: privileged requires explicit approval and is limited to scratch-docker")
        require(service.get("security_opt", []) == ([] if scratch else ["no-new-privileges:true"])
                and service.get("user") == USERS.get(name)
                and service.get("read_only", False) is (name == "scm"), f"{name}: execution boundary differs")
        require(service.get("environment", {}) == ENVIRONMENT.get(name, {})
                and service.get("command") == COMMANDS.get(name), f"{name}: command or environment differs")
        validate_mounts(name, service.get("volumes", []))
    require(len(images) == len(services), "use a separate approved image for each service")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-privileged", action="store_true", help="validate an explicitly approved DinD config")
    args = parser.parse_args()
    try:
        validate(json.load(sys.stdin), args.allow_privileged)
    except (ValueError, TypeError, AttributeError) as exc:
        parser.exit(1, f"Rehearsal config rejected: {exc}\n")
    print("Rehearsal config passed")


if __name__ == "__main__":
    main()
