import unittest

from check_github_template import ROOT, TEMPLATES, validate


def template(kind):
    return (ROOT / TEMPLATES[kind]).read_text(encoding="utf-8")


def pr_body():
    return template("pr").replace("Issue Number: #", "Issue Number: #745")


class TemplateCheckTest(unittest.TestCase):
    def test_all_repository_templates_pass(self):
        for kind in ("issue-feature", "issue-bug"):
            with self.subTest(kind=kind):
                self.assertEqual(validate(kind, template(kind)), [])
        self.assertEqual(validate("pr", pr_body(), 745), [])

    def test_missing_pr_sections_and_wrong_issue_fail(self):
        body = pr_body().replace("### 주요 변경 사항\n", "").replace("Issue Number: #745", "Issue Number: #742")
        errors = validate("pr", body, 745)
        self.assertTrue(any("주요 변경 사항" in error for error in errors))
        self.assertTrue(any("Issue Number: #745" in error for error in errors))

    def test_pr_checklist_cannot_be_replaced_by_a_fenced_example(self):
        line = next(line for line in pr_body().splitlines() if line.startswith("- [ ] `ruff check`"))
        body = pr_body().replace(line, f"```markdown\n{line}\n```")
        self.assertTrue(any("ruff check" in error for error in validate("pr", body, 745)))

    def test_feature_requires_action_and_acceptance_checklists(self):
        body = template("issue-feature").replace("- [ ] TODO\n", "")
        self.assertTrue(any("작업 상세 내용" in error for error in validate("issue-feature", body)))

    def test_bug_preserves_all_impact_options(self):
        body = template("issue-bug").replace("- [ ] 화면에서만 보이는 문제다\n", "")
        self.assertTrue(any("화면에서만" in error for error in validate("issue-bug", body)))

    def test_headings_must_use_the_repository_order(self):
        impact, references = "## 영향 범위(선택)", "## 참고할만한 자료(선택)"
        body = template("issue-feature").replace(impact, "## 임시", 1)
        body = body.replace(references, impact, 1).replace("## 임시", references, 1)
        self.assertIn("템플릿의 제목 순서와 다릅니다.", validate("issue-feature", body))
