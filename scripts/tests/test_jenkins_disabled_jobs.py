"""Offline checks for the #785 disabled Jenkins Pipeline registration files."""

import subprocess
import sys
from pathlib import Path
from shutil import copytree
from tempfile import TemporaryDirectory
from unittest import TestCase, main
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[2]
JOB_DIR = ROOT / "deploy" / "jenkins" / "trading-desk"
REGISTER = ROOT / "scripts" / "jenkins" / "register-disabled-backend-jobs.py"
ROLES = ("api", "collector", "analyst")


class DisabledJobsTest(TestCase):
    def test_only_the_three_role_jobs_are_defined(self):
        self.assertEqual(
            {path.relative_to(JOB_DIR).as_posix() for path in JOB_DIR.rglob("*.xml")},
            {f"trading-desk-backend-{role}/config.xml" for role in ROLES},
        )

    def test_each_job_is_disabled_triggerless_and_fixed_to_trusted_develop(self):
        for role in ROLES:
            with self.subTest(role=role):
                job = ElementTree.parse(JOB_DIR / f"trading-desk-backend-{role}" / "config.xml").getroot()
                self.assertEqual(job.tag, "flow-definition")
                self.assertEqual(
                    [entry.tag for entry in job],
                    ["description", "keepDependencies", "properties", "definition", "triggers", "disabled"],
                )
                self.assertEqual(job.findtext("disabled"), "true")
                self.assertEqual(job.findtext("keepDependencies"), "false")
                self.assertEqual(len(job.find("triggers")), 0)
                definition = job.find("definition")
                self.assertEqual(
                    definition.attrib, {"class": "org.jenkinsci.plugins.workflow.cps.CpsScmFlowDefinition"},
                )
                self.assertEqual(definition.findtext("scriptPath"), "Jenkinsfile-backend-role")
                self.assertEqual(definition.findtext("scm/branches/hudson.plugins.git.BranchSpec/name"),
                                 "refs/heads/develop")
                self.assertEqual(
                    [item.text for item in job.findall(
                        "properties/hudson.model.ParametersDefinitionProperty/parameterDefinitions/*/name"
                    )],
                    ["TD_RELEASE", "TD_PARENT_BUILD", "TD_SHA", "TD_IMAGE_ID", "TD_AGENT", "TD_DAEMON"],
                )


class RegistrationTest(TestCase):
    def setUp(self):
        temp = self.enterContext(TemporaryDirectory())
        self.directory = Path(temp)
        self.cli = self.directory / "fake_cli.py"
        self.cli.write_text(
            """
import sys
from pathlib import Path

command, name = sys.argv[1:]
if command == "list-jobs":
    assert name == "/trading-desk"
    assert not sys.stdin.buffer.read()
    if Path("fail-list").exists():
        sys.exit(1)
    print("trading-desk-backend")
    print("trading-desk-web")
    for config in sorted(Path.cwd().glob("*.xml")):
        print(config.stem)
elif command == "create-job":
    folder, job = name.split("/")
    assert folder == "trading-desk"
    assert job in ("trading-desk-backend-api", "trading-desk-backend-collector",
                   "trading-desk-backend-analyst")
    if Path("fail-" + job).exists():
        sys.exit(1)
    with Path(job + ".xml").open("xb") as target:
        target.write(sys.stdin.buffer.read())
else:
    sys.exit(2)
""",
            encoding="utf-8",
        )

    def register(self, *args, script=REGISTER):
        return subprocess.run(
            [sys.executable, "-B", str(script), *args, "--", sys.executable, str(self.cli)],
            cwd=self.directory, capture_output=True, text=True, timeout=15, check=False,
        )

    def test_plan_uses_inventory_without_creating_jobs(self):
        existing = self.directory / "trading-desk-backend-api.xml"
        existing.touch()
        result = self.register()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            "present trading-desk/trading-desk-backend-api",
            "missing trading-desk/trading-desk-backend-collector",
            "missing trading-desk/trading-desk-backend-analyst",
        ])
        self.assertEqual(list(self.directory.glob("*.xml")), [existing])

    def test_apply_stops_on_inventory_failure(self):
        (self.directory / "fail-list").touch()
        result = self.register("--apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("list-jobs failed", result.stderr)
        self.assertEqual(list(self.directory.glob("*.xml")), [])

    def test_apply_validates_all_configs_before_contacting_jenkins(self):
        repo = self.directory / "repo"
        script = repo / REGISTER.relative_to(ROOT)
        script.parent.mkdir(parents=True)
        script.write_bytes(REGISTER.read_bytes())
        jobs = repo / JOB_DIR.relative_to(ROOT)
        copytree(JOB_DIR, jobs)
        config = jobs / "trading-desk-backend-analyst" / "config.xml"
        config.write_bytes(config.read_bytes().replace(b"<disabled>true", b"<disabled>false"))
        (self.directory / "fail-list").touch()
        result = self.register("--apply", script=script)
        self.assertEqual(result.returncode, 1)
        self.assertIn("analyst: expected a disabled, triggerless, trusted develop Pipeline", result.stderr)
        self.assertEqual(list(self.directory.glob("*.xml")), [])

    def test_apply_can_resume_after_partial_failure(self):
        fail = self.directory / "fail-trading-desk-backend-collector"
        fail.touch()
        result = self.register("--apply")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            [path.stem for path in self.directory.glob("*.xml")], ["trading-desk-backend-api"]
        )
        fail.unlink()
        result = self.register("--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("present trading-desk/trading-desk-backend-api", result.stdout)
        for role in ROLES:
            job = f"trading-desk-backend-{role}"
            self.assertEqual(
                (self.directory / f"{job}.xml").read_bytes(),
                (JOB_DIR / job / "config.xml").read_bytes(),
            )


if __name__ == "__main__":
    main()
