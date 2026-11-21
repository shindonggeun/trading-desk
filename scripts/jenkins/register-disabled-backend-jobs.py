"""Plan or create the three disabled backend jobs in an existing Jenkins folder."""

import argparse
import subprocess
from pathlib import Path
from xml.etree import ElementTree

FOLDER = "trading-desk"
CONFIG_DIR = Path(__file__).resolve().parents[2] / "deploy" / "jenkins" / FOLDER
ROLES = ("api", "collector", "analyst")


def read_configs():
    configs = {}
    for role in ROLES:
        job = f"trading-desk-backend-{role}"
        config = (CONFIG_DIR / job / "config.xml").read_bytes()
        root = ElementTree.fromstring(config)
        definition = root.find("definition")
        if (
            root.tag != "flow-definition"
            or [node.tag for node in root] != [
                "description", "keepDependencies", "properties", "definition", "triggers", "disabled"
            ]
            or root.findtext("disabled") != "true"
            or root.findtext("keepDependencies") != "false"
            or any(node.attrib for node in root.iter() if node is not definition)
            or any(len(node) for node in root if node is not definition)
            or definition.attrib != {"class": "org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition"}
            or [node.tag for node in definition] != ["script", "sandbox"]
            or any(len(node) for node in definition)
            or definition.findtext("sandbox") != "true"
            or definition.findtext("script") != f"error('TD-785: {role} deployment is not wired')"
        ):
            raise ValueError(f"{job}: expected a disabled, triggerless, error-only Pipeline")
        configs[job] = config
    return configs


def jenkins(cli, command, job, config=None):
    try:
        result = subprocess.run(
            [*cli, command, job], input=config if config is not None else b"",
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60, check=True,
        )
        return result.stdout.decode("utf-8")
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise RuntimeError(f"Jenkins {command} failed for {job}") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="create missing disabled jobs")
    parser.add_argument("cli", nargs=argparse.REMAINDER, help="Jenkins CLI command after --")
    args = parser.parse_args()
    if len(args.cli) < 2 or args.cli[0] != "--":
        parser.error("supply a Jenkins CLI command after --")
    cli = args.cli[1:]

    try:
        configs = read_configs()
        # A bare name can select a root view before Jenkins looks for the folder.
        existing = {name.casefold() for name in jenkins(cli, "list-jobs", f"/{FOLDER}").splitlines()}
        for job, config in configs.items():
            path = f"{FOLDER}/{job}"
            if job.casefold() in existing:
                print(f"present {path}")
            elif args.apply:
                jenkins(cli, "create-job", path, config)
                print(f"created {path}")
            else:
                print(f"missing {path}")
    except (OSError, ElementTree.ParseError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
