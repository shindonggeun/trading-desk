"""Exercise an isolated, credential-free Jenkins controller already built as an image."""

import argparse
import json
import re
import subprocess
import time
from urllib.parse import urlencode

DOCKER = ["docker", "--host", "unix:///var/run/docker.sock"]
BASE = "/job/trading-desk-rehearsal/job"
COOKIE = "/tmp/td-rehearsal.cookie"  # Inside the fixture container.


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def docker(*args):
    try:
        stderr = subprocess.STDOUT if args[0] == "logs" else subprocess.PIPE
        result = subprocess.run([*DOCKER, *args], stdout=subprocess.PIPE, stderr=stderr,
                                text=True, timeout=60, check=False)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Docker {args[0]} timed out") from None
    if result.returncode:
        # subprocess errors normally include command arguments, which would log the crumb.
        raise RuntimeError(f"Docker {args[0]} exited {result.returncode}")
    return result.stdout


def controller_state(container):
    return docker("inspect", "--format",
                  "{{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}}", container).strip()


def require_running(container):
    state = controller_state(container)
    require(state.startswith("running "), f"Controller stopped before readiness ({state})")


def inspect_isolation(container):
    config = json.loads(docker("inspect", container))[0]
    host = config["HostConfig"]
    mounts = config["Mounts"]
    require(config["State"]["Status"] == "created", "controller must be inspected before startup")
    require(config["Config"]["User"] == "1000:1000", "controller must use a non-root UID")
    require(host["NetworkMode"] == "none" and not host["PortBindings"], "unexpected controller network")
    require(not host["Privileged"] and not host["CapAdd"] and host["CapDrop"] == ["ALL"],
            "unexpected controller capabilities")
    require(any(opt in ("no-new-privileges", "no-new-privileges:true") for opt in host["SecurityOpt"]),
            "no-new-privileges missing")
    require(not host["Binds"] and not host["VolumesFrom"] and not host["Devices"],
            "unexpected shared host resources")
    require(host["PidMode"] != "host" and host["IpcMode"] != "host", "unexpected host namespace")
    require(len(mounts) == 1 and mounts[0]["Type"] == "volume"
            and mounts[0]["Destination"] == "/var/jenkins_home",
            "unexpected mount; only the disposable image-owned Jenkins home is allowed")
    names = [entry.partition("=")[0] for entry in config["Config"]["Env"]]
    require(not any(re.search("TOKEN|SECRET|PASSWORD|CREDENTIAL|^GITHUB_|^DOCKER_HOST$", n, re.IGNORECASE)
                    for n in names), "credential-like or host Docker environment")
    print("Controller isolation passed: non-root, network none, no ports or host mounts, all capabilities dropped")


def wait_for(predicate, description, seconds=75):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if result := predicate():
            return result
        time.sleep(0.35)
    raise RuntimeError(f"Timed out waiting for {description}")


class Jenkins:
    def __init__(self, container):
        self.container = container
        self.crumb = []

    def request(self, path, data=None, *, ready=False, include_headers=False):
        curl = ["curl", "--silent", "--show-error", "--max-time", "5",
                "--cookie", COOKIE, "--cookie-jar", COOKIE, "--write-out", "\n%{http_code}"]
        if data is not None:
            curl += [*self.crumb, "--data", urlencode(data)]
        if include_headers:
            curl.append("--include")
        try:
            result = docker("exec", self.container, *curl, "http://127.0.0.1:8080" + path)
        except RuntimeError:
            if ready:
                return 0, ""
            raise
        body, status = result.rsplit("\n", 1)
        return int(status), body

    def api(self, path, **query):
        code, body = self.request(path + "/api/json?" + urlencode(query))
        if code == 404:
            return {}
        require(code == 200, f"read {path} returned {code}")
        return json.loads(body)

    def log(self, job, number):
        code, body = self.request(f"{BASE}/{job}/{number}/consoleText")
        return body if code == 200 else ""

    def number(self, job):
        return self.api(f"{BASE}/{job}", tree="nextBuildNumber")["nextBuildNumber"]

    def start(self, job, **parameters):
        result = self.number(job)
        code, _ = self.request(f"{BASE}/{job}/buildWithParameters", parameters)
        require(code == 201, f"start {job} returned {code}")
        return result

    def stop(self, job, number):
        code, _ = self.request(f"{BASE}/{job}/{number}/stop", {})
        # WorkflowRun redirects the browser after handling a Stop POST.
        require(code in (200, 302, 303), f"stop {job} #{number} returned {code}")

    def assert_denied(self, path, permission):
        code, response = self.request(path, {}, include_headers=True)
        headers, _, _ = response.partition("\n\n")
        permission_id = f"hudson.model.Item.{permission.removeprefix('Job/')}"
        denied = any(
            name.casefold() == "x-required-permission" and value.strip().casefold() == permission_id.casefold()
            for name, _, value in (line.partition(":") for line in headers.splitlines()[1:])
        )
        require(code == 403 and denied, f"expected anonymous denial of {permission} at {path}, got {code}")
        print(f"Anonymous {permission} denied")

    def terminal(self, job, number, expected):
        def result():
            record = self.api(f"{BASE}/{job}/{number}", tree="building,result")
            return record.get("result") if record.get("building") is False else None

        actual = wait_for(result, f"{job} #{number} to finish")
        require(actual == expected, f"{job} #{number}: expected {expected}, got {actual}\n{self.log(job, number)}")
        return actual

    def api_queue(self, parent):
        data = self.api("/queue", tree="items[id,task[name],actions[causes[upstreamBuild,upstreamProject]]]")
        for item in data["items"]:
            if item["task"]["name"] == "api" and any(
                cause.get("upstreamProject") == "trading-desk-rehearsal/coordinator"
                and cause.get("upstreamBuild") == parent
                for action in item.get("actions", []) for cause in action.get("causes", [])
            ):
                return item["id"]
        return None

    def ready(self):
        def initialized():
            try:
                docker("exec", self.container, "test", "-f", "/var/jenkins_home/td-fixture-ready")
            except RuntimeError:
                require_running(self.container)
                return False
            return True

        def serving():
            if self.request(f"{BASE}/analyst/api/json", ready=True)[0] == 200:
                return True
            require_running(self.container)
            return False

        wait_for(initialized, "Jenkins fixture authorization setup", seconds=100)
        wait_for(serving, "Jenkins fixture jobs", seconds=100)
        code, body = self.request("/crumbIssuer/api/json")
        require(code == 200, "fixture crumb is unavailable")
        crumb = json.loads(body)
        self.crumb = ["--header", f"{crumb['crumbRequestField']}: {crumb['crumb']}"]


def cancel_case(jenkins, queued):
    child = jenkins.number("api")
    before_workers = tuple(jenkins.number(role) for role in ("collector", "analyst"))
    parent = jenkins.start("coordinator", TD_CASE="cancel-queued-api" if queued else "cancel-api")
    if queued:
        queue_id = wait_for(lambda: jenkins.api_queue(parent), "the API in Jenkins' quiet-period queue", seconds=30)
        evidence = {"TD_QUEUE_ID": str(queue_id)}
    else:
        wait_for(lambda: "TD_CANCEL_POINT" in jenkins.log("api", child), "running API cancellation point")
        require(jenkins.api(f"{BASE}/api/{child}", tree="building")["building"], "API must be running")
        jenkins.assert_denied(f"{BASE}/api/{child}/stop", "Job/Cancel")
        evidence = {"TD_CHILD_BUILD": str(child)}

    competitor = jenkins.start("competitor", TD_PARENT_BUILD=str(parent), **evidence)
    wait_for(lambda: "waiting" in jenkins.log("competitor", competitor).lower(),
             "independent competitor waiting in the lock step", seconds=20)
    jenkins.stop("coordinator", parent)
    print(f"Stop requested for coordinator #{parent} with {evidence}")
    jenkins.terminal("coordinator", parent, "ABORTED")
    jenkins.terminal("competitor", competitor, "SUCCESS")
    log = jenkins.log("competitor", competitor)
    require("TD_COMPETITOR_OBSERVED_TERMINAL" in log, "competitor did not record controller terminal state")
    print(next(line for line in log.splitlines() if line.startswith("TD_COMPETITOR_OBSERVED_TERMINAL")))
    if queued:
        require(jenkins.number("api") == child, "cancelled queue entry started an API build")
    require(tuple(jenkins.number(role) for role in ("collector", "analyst")) == before_workers,
            "worker dispatch continued after cancellation")
    blocked = jenkins.start("coordinator", TD_CASE="success")
    jenkins.terminal("coordinator", blocked, "FAILURE")
    require("TD_ORPHAN_BLOCKED" in jenkins.log("coordinator", blocked), "orphan did not block a new parent")
    recovery = jenkins.start("competitor", TD_PARENT_BUILD=str(parent), TD_RECOVER="yes", **evidence)
    jenkins.terminal("competitor", recovery, "SUCCESS")
    require("recovered=true" in jenkins.log("competitor", recovery), "verified orphan was not recovered")
    jenkins.terminal("coordinator", jenkins.start("coordinator", TD_CASE="success"), "SUCCESS")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", choices=["td-jenkins-controller-fixture"])
    args = parser.parse_args()
    container = docker(
        "create", "--pull", "never", "--network", "none", "--user", "1000:1000",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--env", "JAVA_OPTS=-Djenkins.install.runSetupWizard=false",
        args.image, "--httpListenAddress=127.0.0.1",
    ).strip()
    try:
        inspect_isolation(container)
        docker("start", container)
        require(docker("exec", container, "id", "-u").strip() == "1000", "runtime UID differs from config")
        jenkins = Jenkins(container)
        jenkins.ready()
        jenkins.assert_denied(f"{BASE}/api/buildWithParameters", "Job/Build")
        # A real parent Build Step succeeds with the internal Jenkins identity.
        jenkins.terminal("coordinator", jenkins.start("coordinator", TD_CASE="success"), "SUCCESS")
        rollback = jenkins.start("coordinator", TD_CASE="api-rollback")
        jenkins.terminal("coordinator", rollback, "FAILURE")
        require("TD_RESULT_RECOVERED" in jenkins.log("coordinator", rollback), "rollback was not observed")
        cancel_case(jenkins, queued=True)
        cancel_case(jenkins, queued=False)
        print("Jenkins rehearsal passed: ACL, terminal state at first guarded observation, synthetic orphan recovery")
    except Exception:
        try:
            print(f"Controller state: {controller_state(container)}")
        except RuntimeError as diagnostic:
            print(f"Controller state unavailable: {diagnostic}")
        try:
            print(docker("logs", "--tail", "240", container))
        except RuntimeError as diagnostic:
            print(f"Controller logs unavailable: {diagnostic}")
        raise
    finally:
        docker("rm", "--force", "--volumes", container)


if __name__ == "__main__":
    main()
