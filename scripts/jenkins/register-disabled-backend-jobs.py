"""Plan or create the three disabled backend jobs in an existing Jenkins folder."""

import argparse
import subprocess
from pathlib import Path
from xml.etree import ElementTree

FOLDER = "trading-desk"
CONFIG_DIR = Path(__file__).resolve().parents[2] / "deploy" / "jenkins" / FOLDER
ROLES = ("api", "collector", "analyst")
PARAMETERS = ("TD_RELEASE", "TD_PARENT_BUILD", "TD_SHA", "TD_IMAGE_ID", "TD_AGENT", "TD_DAEMON")


def expected_job(role):
    parameters = "".join(
        f"<hudson.model.StringParameterDefinition><name>{name}</name><defaultValue/>"
        "</hudson.model.StringParameterDefinition>" for name in PARAMETERS
    )
    return ElementTree.fromstring(f"""
    <flow-definition>
      <description>백엔드 {role} 배포 잡 (#785). 비운영 검증과 승인 전까지 비활성입니다.</description>
      <keepDependencies>false</keepDependencies>
      <properties><hudson.model.ParametersDefinitionProperty>
        <parameterDefinitions>{parameters}</parameterDefinitions>
      </hudson.model.ParametersDefinitionProperty></properties>
      <definition class="org.jenkinsci.plugins.workflow.cps.CpsScmFlowDefinition">
        <scm class="hudson.plugins.git.GitSCM">
          <configVersion>2</configVersion>
          <userRemoteConfigs><hudson.plugins.git.UserRemoteConfig>
            <url>https://github.com/shindonggeun/trading-desk</url>
            <credentialsId>trading-desk-github</credentialsId>
          </hudson.plugins.git.UserRemoteConfig></userRemoteConfigs>
          <branches><hudson.plugins.git.BranchSpec><name>refs/heads/develop</name></hudson.plugins.git.BranchSpec></branches>
          <doGenerateSubmoduleConfigurations>false</doGenerateSubmoduleConfigurations>
          <extensions/>
        </scm>
        <scriptPath>Jenkinsfile-backend-role</scriptPath>
        <lightweight>true</lightweight>
      </definition>
      <triggers/>
      <disabled>true</disabled>
    </flow-definition>
    """)


def signature(element):
    return (element.tag, element.attrib, (element.text or "").strip(), (element.tail or "").strip(),
            tuple(signature(child) for child in element))


def read_configs():
    configs = {}
    for role in ROLES:
        job = f"trading-desk-backend-{role}"
        config = (CONFIG_DIR / job / "config.xml").read_bytes()
        root = ElementTree.fromstring(config)
        if signature(root) != signature(expected_job(role)):
            raise ValueError(f"{job}: expected a disabled, triggerless, trusted develop Pipeline")
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
