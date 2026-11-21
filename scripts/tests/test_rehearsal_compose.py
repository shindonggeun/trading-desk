"""Offline tests for the rehearsal Compose config guard."""

import json
import os
import subprocess
from copy import deepcopy
from pathlib import Path
from runpy import run_path
from shutil import which
from tempfile import TemporaryDirectory
from unittest import TestCase, main, skipUnless

guard = run_path(str(Path(__file__).resolve().parents[1] / "jenkins" / "check_rehearsal_config.py"))
compose_dir = Path(__file__).resolve().parents[2] / "deploy" / "jenkins" / "rehearsal"


def fixture():
    project = guard["PROJECT"]
    config = {"name": project, "services": {}, "networks": {}, "volumes": {}}
    for network in ("control", "scratch"):
        config["networks"][network] = {"name": f"{project}_{network}", "internal": True}
    for index, (name, networks) in enumerate(guard["NETWORKS"].items(), 1):
        service = {
            "image": f"sha256:{str(index) * 64}",
            "profiles": ["rehearsal"],
            "pull_policy": "never",
            "networks": dict.fromkeys(networks),
            "environment": deepcopy(guard["ENVIRONMENT"].get(name, {})),
            "command": deepcopy(guard["COMMANDS"].get(name)),
            "user": guard["USERS"].get(name),
            "security_opt": [] if name == "scratch-docker" else ["no-new-privileges:true"],
            "read_only": name == "scm",
            "volumes": [],
        }
        if name == "controller":
            service["ports"] = deepcopy(guard["LOOPBACK_PORT"])
        for source, target, read_only in guard["MOUNTS"][name]:
            service["volumes"].append({
                "type": "volume", "source": source, "target": target, "read_only": read_only,
            })
            config["volumes"][source] = {"name": f"{project}_{source}"}
        config["services"][name] = service
    return config


class RehearsalConfigTest(TestCase):
    def setUp(self):
        self.config = fixture()
        self.services = self.config["services"]
        self.validate = guard["validate"]

    def test_preflight_and_explicit_dind(self):
        self.validate(self.config)
        self.services["scratch-docker"]["privileged"] = True
        with self.assertRaisesRegex(ValueError, "privileged requires explicit approval"):
            self.validate(self.config)
        self.validate(self.config, allow_privileged=True)
        self.services["agent"]["privileged"] = True
        with self.assertRaisesRegex(ValueError, "limited to scratch-docker"):
            self.validate(self.config, allow_privileged=True)

    def test_rejects_default_service_and_mutable_image(self):
        self.services["agent"]["profiles"] = []
        with self.assertRaisesRegex(ValueError, "require the rehearsal profile"):
            self.validate(self.config)
        self.services["agent"]["profiles"] = ["rehearsal"]
        self.services["controller"]["image"] = "jenkins/jenkins:lts"
        with self.assertRaisesRegex(ValueError, "local image ID"):
            self.validate(self.config)

    def test_rejects_port_and_network_overrides(self):
        self.services["controller"]["ports"][0]["host_ip"] = "0.0.0.0"
        with self.assertRaisesRegex(ValueError, "loopback"):
            self.validate(self.config)
        self.services["controller"]["ports"] = deepcopy(guard["LOOPBACK_PORT"])
        self.config["networks"]["scratch"]["external"] = True
        with self.assertRaisesRegex(ValueError, "project-local internal network"):
            self.validate(self.config)

    def test_rejects_host_docker_and_read_write_credential_mounts(self):
        self.services["agent"]["volumes"].append({
            "type": "bind", "source": "/var/run/docker.sock", "target": "/var/run/docker.sock",
        })
        with self.assertRaisesRegex(ValueError, "named volumes only"):
            self.validate(self.config)
        self.services["agent"]["volumes"].pop()
        auth = next(mount for mount in self.services["agent"]["volumes"] if mount["source"] == "agent-auth")
        auth["read_only"] = False
        with self.assertRaisesRegex(ValueError, "mount boundary differs"):
            self.validate(self.config)

    def test_rejects_external_volumes_and_shared_project_name(self):
        self.config["volumes"]["agent-work"]["external"] = True
        with self.assertRaisesRegex(ValueError, "project-owned named volumes"):
            self.validate(self.config)
        self.config["volumes"]["agent-work"].pop("external")
        self.config["name"] = "trading-desk-backend-prod"
        with self.assertRaisesRegex(ValueError, "unexpected project name"):
            self.validate(self.config)

    def test_rejects_externally_supplied_secrets_and_docker_endpoints(self):
        self.services["agent"]["env_file"] = [".env.runtime"]
        with self.assertRaisesRegex(ValueError, "unexpected service properties"):
            self.validate(self.config)
        self.services["agent"].pop("env_file")
        self.services["agent"]["environment"]["DOCKER_HOST"] = "tcp://other-host:2376"
        with self.assertRaisesRegex(ValueError, "environment differs"):
            self.validate(self.config)


@skipUnless(which("docker"), "Docker CLI needed for daemon-free Compose config checks")
class RenderedRehearsalConfigTest(TestCase):
    def render(self, *, profile=True, privileged=False, override=None):
        with TemporaryDirectory(prefix="td-compose-config-") as temp:
            directory = Path(temp)
            command = [
                "docker", "--host", f"unix://{(directory / 'absent.sock').as_posix()}",
                "compose", "-p", guard["PROJECT"],
                "--env-file", str(compose_dir / "rehearsal.env.example"),
                "-f", str(compose_dir / "compose.rehearsal.yml"),
            ]
            if override:
                file = directory / "override.yml"
                file.write_text(json.dumps(override), encoding="utf-8")
                command.extend(["-f", str(file)])
            if profile:
                command.extend(["--profile", "rehearsal"])
            config = {key: value for key, value in os.environ.items()
                      if not key.startswith(("TD_REHEARSAL_", "COMPOSE_", "DOCKER_"))}
            if privileged:
                config["TD_REHEARSAL_PRIVILEGED"] = "true"
            command.extend(["config", "--format", "json"] if profile else ["config", "--services"])
            return subprocess.run(command, env=config, capture_output=True, text=True, timeout=15,
                                  check=True).stdout

    def test_real_config_positive_and_privileged_opt_in(self):
        self.assertEqual(self.render(profile=False).strip(), "")
        guard["validate"](json.loads(self.render()))
        privileged = json.loads(self.render(privileged=True))
        with self.assertRaisesRegex(ValueError, "privileged requires explicit approval"):
            guard["validate"](privileged)
        guard["validate"](privileged, allow_privileged=True)

    def test_real_config_rejects_boundary_changes(self):
        cases = [
            ("loopback", {"services": {"controller": {"ports": ["0.0.0.0:18081:8080"]}}}),
            ("project-local internal network", {"networks": {"scratch": {"internal": False}}}),
            ("project-owned named volumes", {"volumes": {"agent-work": {"external": True}}}),
            ("named volumes only", {
                "services": {"agent": {"volumes": ["/var/run/docker.sock:/var/run/docker.sock"]}},
            }),
            ("pulling disabled", {"services": {"controller": {"pull_policy": "always"}}}),
            ("unexpected top-level", {
                "services": {"agent": {"secrets": ["outside"]}},
                "secrets": {"outside": {"external": True}},
            }),
        ]
        for pattern, override in cases:
            with self.subTest(pattern=pattern), self.assertRaisesRegex(ValueError, pattern):
                guard["validate"](json.loads(self.render(override=override)))


if __name__ == "__main__":
    main()
