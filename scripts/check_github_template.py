"""Check a GitHub issue or PR body against the repository template before posting."""

import argparse
from collections import Counter
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = {
    "issue-feature": ".github/ISSUE_TEMPLATE/feature-issue.md",
    "issue-bug": ".github/ISSUE_TEMPLATE/bug-issue.md",
    "pr": ".github/PULL_REQUEST_TEMPLATE.md",
}
HEADING = re.compile(r"^#{2,6} +\S")
CHECKBOX = re.compile(r"^- \[[ xX]\] (.+)$")
ISSUE_NUMBER = re.compile(r"^Issue Number: +#(\d+)$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def sections(markdown):
    result = []
    fence = None
    for line in re.sub(r"<!--.*?-->", "", markdown, flags=re.S).splitlines():
        delimiter = FENCE.match(line)
        if fence is not None:
            if delimiter and delimiter[1].startswith(fence) and not delimiter[2].strip():
                fence = None
            continue
        if delimiter:
            fence = delimiter[1]
            continue
        if HEADING.match(line):
            result.append((line.rstrip(), []))
        elif result:
            result[-1][1].append(line.strip())
    return result


def validate(kind, markdown, issue=None):
    template = sections((ROOT / TEMPLATES[kind]).read_text(encoding="utf-8"))
    headings = [heading for heading, _ in template]
    body = [(heading, lines) for heading, lines in sections(markdown) if heading in headings]
    actual = [heading for heading, _ in body]
    errors = []
    if actual != headings:
        missing = Counter(headings) - Counter(actual)
        duplicated = Counter(actual) - Counter(headings)
        if missing:
            errors.append("누락된 제목: " + " / ".join(missing))
        if duplicated:
            errors.append("중복 제목: " + " / ".join(duplicated))
        if not missing and not duplicated:
            errors.append("템플릿의 제목 순서와 다릅니다.")

    by_heading = dict(body)
    for heading, lines in template:
        if heading not in by_heading:
            continue
        expected = [match[1] for line in lines if (match := CHECKBOX.match(line))]
        actual_checks = [match[1] for line in by_heading[heading] if (match := CHECKBOX.match(line))]
        if kind == "issue-feature":
            if expected and not actual_checks:
                errors.append(f"{heading} 체크리스트가 비어 있습니다.")
            continue
        for item in expected:
            if not any(check.startswith(item) for check in actual_checks):
                errors.append(f"{heading} 체크리스트 누락: {item}")

    if kind == "pr":
        linked = [
            number[1] for line in by_heading.get("## 연관된 이슈", ())
            if (number := ISSUE_NUMBER.fullmatch(line))
        ]
        if linked != [str(issue)]:
            errors.append(f"연관된 이슈 절에 Issue Number: #{issue} 한 줄이 필요합니다.")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=TEMPLATES)
    parser.add_argument("body", type=Path)
    parser.add_argument("--issue", type=int)
    args = parser.parse_args()
    if args.kind == "pr" and not args.issue:
        parser.error("PR 검사에는 --issue <번호>가 필요합니다.")
    errors = validate(args.kind, args.body.read_text(encoding="utf-8-sig"), args.issue)
    if errors:
        print("\n".join(errors))
        return 1
    print("GitHub 템플릿 검사 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
