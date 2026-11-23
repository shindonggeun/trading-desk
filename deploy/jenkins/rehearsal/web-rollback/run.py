"""Run the webhook-free web rollback fixture on a disposable Jenkins controller."""

import argparse
import json
import re
import subprocess
import time
from urllib.parse import urlencode

IMAGE = "td-jenkins-web-rollback-fixture"
DOCKER = ("docker", "--host", "unix:///var/run/docker.sock")
HOME = "/var/jenkins_home"
FIXTURE = "/opt/td-web-fixture"
JOB = "/job/td-web-rollback"
COOKIE = "/tmp/td-web-rehearsal.cookie"


def require(condition, detail):
    if not condition:
        raise RuntimeError(detail)


def docker(*args, allowed=(0,)):
    try:
        result = subprocess.run([*DOCKER, *args], capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=60, check=False)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Docker {args[0]} timed out") from None
    # Never emit command arguments or stderr; a request can contain a CSRF crumb.
    require(result.returncode in allowed, f"Docker {args[0]} exited {result.returncode}")
    return result


def inspect_isolation(container):
    info, = json.loads(docker("inspect", container).stdout)
    host = info["HostConfig"]
    require(info["State"]["Status"] == "created", "container must be inspected before starting")
    require(info["Config"]["User"] == "1000:1000", "controller must run as non-root")
    require(host["NetworkMode"] == "none" and not host["PortBindings"], "unexpected network or ports")
    require(not host["Privileged"] and not host["CapAdd"] and host["CapDrop"] == ["ALL"],
            "unexpected controller capabilities")
    require(any(opt in ("no-new-privileges", "no-new-privileges:true") for opt in host["SecurityOpt"]),
            "no-new-privileges missing")
    require(not host["Binds"] and not host["VolumesFrom"] and not host["Devices"],
            "unexpected shared host resources")
    require(host["PidMode"] != "host" and host["IpcMode"] != "host", "unexpected host namespace")
    require(len(info["Mounts"]) == 1 and info["Mounts"][0]["Type"] == "volume"
            and info["Mounts"][0]["Destination"] == HOME, "unexpected mount")
    env = dict(entry.split("=", 1) for entry in info["Config"]["Env"])
    forbidden = re.compile(r"TOKEN|SECRET|PASSWORD|CREDENTIAL|^GITHUB_|^DOCKER_", re.IGNORECASE)
    require(not any(forbidden.search(key) for key in env),
            "unexpected credential or Docker environment")
    require(env.get("VAULT_ADDR") == "http://127.0.0.1:9", "fixture must use the loopback placeholder for Vault")
    print("Controller isolation passed")


def wait_for(probe, detail):
    deadline = time.monotonic() + 100
    while time.monotonic() < deadline:
        if value := probe():
            return value
        time.sleep(0.4)
    raise RuntimeError(f"Timed out waiting for {detail}")


class Jenkins:
    def __init__(self, container):
        self.container = container
        self.crumb = ()

    def request(self, path, data=None):
        command = ["curl", "--noproxy", "*", "--silent", "--show-error", "--max-time", "5",
                   "--cookie", COOKIE, "--cookie-jar", COOKIE, "--write-out", "\n%{http_code}"]
        if data is not None:
            command += [*self.crumb, "--data", urlencode(data)]
        result = docker("exec", self.container, *command, "http://127.0.0.1:8080" + path, allowed=(0, 7))
        if result.returncode == 7:
            return 0, ""
        body, status = result.stdout.rsplit("\n", 1)
        return int(status), body

    def api(self, path, tree):
        status, body = self.request(path + "/api/json?" + urlencode({"tree": tree}))
        if status in (0, 404, 503):
            return {}
        require(status == 200, f"Failed to read fixture Jenkins API: HTTP {status}")
        return json.loads(body)

    def ready(self):
        def initialized():
            status = docker("exec", self.container, "test", "-f", f"{HOME}/td-web-fixture-ready",
                            allowed=(0, 1)).returncode
            return status == 0 and self.api(JOB, "nextBuildNumber")

        wait_for(initialized, "the fixture job")
        status, body = self.request("/crumbIssuer/api/json")
        require(status == 200, "fixture CSRF crumb unavailable")
        crumb = json.loads(body)
        self.crumb = ("--header", f"{crumb['crumbRequestField']}: {crumb['crumb']}")

    def build(self, revision, expected, suffix):
        number = self.api(JOB, "nextBuildNumber")["nextBuildNumber"]
        status, _ = self.request(JOB + "/buildWithParameters", {"DEPLOY_COMMIT": revision})
        require(status == 201, f"Fixture build request returned HTTP {status}")
        path = f"{JOB}/{number}"

        def finished():
            info = self.api(path, "building,result,description")
            return info if info.get("building") is False else None

        info = wait_for(finished, f"fixture build #{number}")
        status, log = self.request(path + "/consoleText")
        require(status == 200, f"Could not read fixture build #{number} log: HTTP {status}")
        description = info["description"] or ""
        require(info["result"] == expected and description.endswith(suffix),
                f"Unexpected fixture build #{number} result: {info}\n{log}")
        docker("exec", self.container, "test", "!", "-e", f"{HOME}/workspace/td-web-rollback/.deploy-secrets")
        docker("exec", self.container, "test", "!", "-e", f"{HOME}/workspace/td-web-rollback/.deploy-src")
        print(f"Jenkins build #{number}: {info['result']} ({info['description']}); workspace cleanup checked")
        return log

    def state(self):
        return json.loads(docker("exec", self.container, "cat", f"{HOME}/td-web-fixture-state.json").stdout)


def prove_rollback(jenkins):
    baseline = docker("exec", jenkins.container, "cat", f"{FIXTURE}/baseline-sha").stdout.strip()
    rejected = docker("exec", jenkins.container, "cat", f"{FIXTURE}/rejected-sha").stdout.strip()
    require(baseline != rejected and all(re.fullmatch("[0-9a-f]{40}", rev) for rev in (baseline, rejected)),
            "fixture revisions are invalid")
    jenkins.build(baseline, "SUCCESS", f"web {baseline[:8]} 배포됨")
    good = jenkins.state()
    prod = "trading-desk-web:prod"
    prior = good["container"]["image"]
    require(prior == good["tags"][prod] and good["revisions"][prior] == baseline,
            "the baseline must be mounted at the production tag and running")
    require(good["events"] == [["build", prior], ["up", prior], ["probe", prior, True], ["probe", prior, True]],
            "the baseline did not perform the expected build and health probes")

    log = jenkins.build("", "FAILURE", f"web {rejected[:8]} 되돌림")
    for marker in ("확인 실패: web-prod 상태 확인(health) 응답이 없다",
                   f"직전 이미지로 되돌렸다({baseline})",
                   f"배포 확인이 실패해 직전 이미지로 되돌렸다({rejected[:8]})"):
        require(marker in log, f"Missing rollback marker: {marker}\n{log}")
    restored = jenkins.state()
    bad_image = restored["tags"][f"trading-desk-web:{rejected[:12]}"]
    require(bad_image != prior and restored["revisions"][bad_image] == rejected,
            "the rejected immutable image must remain available")
    require(restored["tags"][prod] == restored["container"]["image"] == prior,
            "the production tag and recreated container must use the prior immutable image")
    require(restored["container"]["id"] != good["container"]["id"], "fixture did not recreate the web container")
    require(restored["events"][len(good["events"]):] == [
        ["build", bad_image], ["up", bad_image], ["probe", bad_image, False],
        ["up", prior], ["probe", prior, True], ["probe", prior, True],
    ], "the rollback did not health-check the restored image after the rejected release")
    print(f"Restored the prior immutable fixture image {prior}; the rejected image remains tagged")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", choices=[IMAGE])
    args = parser.parse_args()
    container = docker(
        "create", "--pull", "never", "--network", "none", "--user", "1000:1000",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--env", "JAVA_OPTS=-Djenkins.install.runSetupWizard=false",
        args.image, "--httpListenAddress=127.0.0.1",
    ).stdout.strip()
    try:
        inspect_isolation(container)
        docker("start", container)
        docker("exec", "-d", container, "python3", "-m", "http.server", "--bind", "127.0.0.1",
               "--directory", f"{FIXTURE}/http", "8765")
        wait_for(lambda: docker(
            "exec", container, "curl", "--noproxy", "*", "--silent", "--fail", "--max-time", "5",
            "http://127.0.0.1:8765/scm.git/HEAD", allowed=(0, 7),
        ).returncode == 0, "the loopback-only SCM fixture")
        jenkins = Jenkins(container)
        jenkins.ready()
        prove_rollback(jenkins)
    except Exception:
        try:
            diagnostic = docker("logs", "--tail", "150", container)
            print(diagnostic.stdout + diagnostic.stderr)
        except RuntimeError as error:
            print(f"Fixture diagnostics were unavailable: {error}")
        raise
    finally:
        docker("rm", "--force", "--volumes", container)


if __name__ == "__main__":
    main()
