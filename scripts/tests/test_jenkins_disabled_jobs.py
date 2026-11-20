"""Offline checks for the #785 disabled Jenkins Pipeline registration files."""

from pathlib import Path
from unittest import TestCase, main
from xml.etree import ElementTree

JOB_DIR = Path(__file__).resolve().parents[2] / "deploy" / "jenkins" / "trading-desk"
ROLES = ("api", "collector", "analyst")


class DisabledJobsTest(TestCase):
    def test_only_the_three_role_jobs_are_defined(self):
        self.assertEqual(
            {path.relative_to(JOB_DIR).as_posix() for path in JOB_DIR.rglob("*.xml")},
            {f"trading-desk-backend-{role}/config.xml" for role in ROLES},
        )

    def test_each_job_is_disabled_triggerless_and_cannot_deploy(self):
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
                self.assertEqual(len(job.find("properties")), 0)
                self.assertEqual(len(job.find("triggers")), 0)
                definition = job.find("definition")
                self.assertEqual(
                    definition.attrib, {"class": "org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition"},
                )
                self.assertEqual([entry.tag for entry in definition], ["script", "sandbox"])
                self.assertEqual(definition.findtext("sandbox"), "true")
                self.assertEqual(definition.findtext("script"), f"error('TD-785: {role} deployment is not wired')")


if __name__ == "__main__":
    main()
