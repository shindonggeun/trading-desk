#!/usr/bin/env python3
"""Claude 원본(CLAUDE.md · .claude/skills · .claude/agents)에서 Codex 용 AGENTS.md · 스킬 · 역할을 만든다.

정본은 Claude 쪽이다. 생성본(AGENTS.md, .agents/skills, .agents/roles, .codex/agents)은 직접 고치지 않는다.

    python -X utf8 scripts/sync-agent-skills.py --write   # 다시 만든다
    python -X utf8 scripts/sync-agent-skills.py           # 어긋남만 검사한다(CI)

표준 라이브러리만 쓴다.
"""

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODEX_MODEL_CONFIG = ROOT / ".codex/agent-models.json"
FAST_ROLES = {"explorer", "git-ops"}
DESIGN_ROLES = {"architect"}
REASONING_EFFORTS = {"minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
# Codex 전용으로 직접 관리하며 Claude 원본이 없는 스킬(상대 경로).
STANDALONE_SKILLS: set[str] = set()
SKILL_NAMES = ("dev-orchestrator", "issue-start", "issue", "pr")
SYNC_COMMAND = "python -X utf8 scripts/sync-agent-skills.py --write"
GENERATED_NOTE = "scripts/sync-agent-skills.py 가 Claude 원본에서 생성한다. 직접 고치지 않는다."

COMMON_REPLACEMENTS = {
    ".claude/agents": ".agents/roles",
    "Write 도구로 세션 scratchpad 의 임시 파일에": "파일 편집 도구로 저장소 밖 임시 파일(운영체제 임시 디렉터리)에",
    "Write 도구로 저장소 밖 임시 파일에": "파일 편집 도구로 저장소 밖 임시 파일에",
    "- `Grep`/`Glob` 으로 후보를 좁힌 뒤 필요한 부분만 `Read` 한다.":
        "- `rg`/`rg --files` 로 후보를 좁힌 뒤 필요한 부분만 읽는다.",
    "읽기 전용이라 **한 메시지에서 병렬로** 부른다.":
        "읽기 전용이라 위임이 허용되면 **병렬로** 부르고(`spawn_agent` 여러 번 → `wait_agent`), 아니면 메인 세션이 차례로 검토한다.",
    "`.github/**` · `.claude/**` → `infra`": "`.github/**` · `.claude/**` · `.agents/**` · `.codex/**` → `infra`",
    "배포 · CI · Claude 설정 → `[INFRA]`": "배포 · CI · Claude · Codex 설정 → `[INFRA]`",
}

# 스킬 호출 표기: Claude `/issue` → Codex `$issue`. 경로(`origin/pr`, `/api/...`)는 건드리지 않는다.
SKILL_CALL = re.compile(r"(?<![\w./$-])/(" + "|".join(SKILL_NAMES) + r")(?![\w-])")

ROUTING = """## 모델 라우팅

| 일 | 맡는 곳 | 모델 |
|----|---------|------|
| 분류 · 계획 · 구현 · 검증 실행 · 완료 판단 | 메인 세션 | 세션 모델 |
| 위치 · 호출 흐름을 모르는 탐색 | `explorer` | `.codex/agent-models.json` 의 `fast` |
| 이슈 시작, 푸시 · PR 생성, 머지 후 정리 | `$issue-start` · `$pr` · `git_ops` | `fast` |
| 설계 | `architect` | `design` |
| 검토 | `security_reviewer` · `reviewer` | `work` |

- 위치를 아는 파일 몇 개 읽기, 커밋 한 번 같은 단일 작업은 메인 세션이 직접 한다. 하위 에이전트는 띄우는 비용이 더 크다.
- 하위 에이전트는 대화 기록을 보지 못한다. 경로 · 브랜치 · 이슈 번호 · 검증 결과 · 원하는 출력 형식을 프롬프트에 담는다.
- Codex 에서는 `spawn_agent(agent_type="explorer", ...)` 처럼 `.codex/agents/` 의 이름(하이픈 대신 밑줄)을 쓴다.
  위임이 허용되지 않았거나 하위 에이전트를 쓸 수 없으면 메인 세션이 `.agents/roles/<역할>.md` 를 읽고 같은 순서로 직접 한다.
"""

FORK_NOTE = (
    "> Claude Code 에서는 이 스킬이 `{agent}` 하위 에이전트로 포크된다. Codex 에서는 메인 세션이 그대로 실행하거나,\n"
    "> 위임이 허용되면 인자 · 저장소 경로를 담아 `{codex_agent}` 에 맡긴다.\n"
)

AGENTS_HEADER = f"""# AGENTS.md

<!-- {GENERATED_NOTE} -->

Codex 가 이 저장소에서 읽는 지침이다. 정본은 `CLAUDE.md`(Claude Code 용)이고, 이 파일은 그 본문을 Codex 표기로 바꾼 생성본이다.
규칙을 바꾸려면 `CLAUDE.md` · `.claude/` 를 고친 뒤 `{SYNC_COMMAND}` — CI(`codex-check`)가 어긋남을 잡는다.
"""

AGENTS_FOOTER = """
## Codex 에서

- 스킬은 `.agents/skills/<이름>/SKILL.md` — `$issue` · `$issue-start <번호>` · `$pr 검증: ...` · `$dev-orchestrator` 로 부른다.
  스킬 본문의 `/스킬명` 이 남아 있으면 같은 워크플로를 뜻한다.
- 역할 지침은 `.agents/roles/*.md`, 하위 에이전트 정의는 `.codex/agents/*.toml`(이름은 `git_ops` · `security_reviewer` 처럼 밑줄).
  모델 · 추론 강도는 `.codex/agent-models.json` 에서 고친다. 대응표와 사용법은 [docs/claude-agents.md](docs/claude-agents.md) 의 Codex 절.
- 하위 에이전트는 위임이 허용된 경우에만 쓴다. 아니면 메인 세션이 같은 역할을 차례로 한다. 쓰기 역할은 한 번에 하나만.
- `AGENTS.md` · `.agents/` · `.codex/agents/` 는 생성본이다. 직접 고치지 않는다.
"""


def split_source(path):
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
    if not match:
        raise ValueError(f"frontmatter 가 없다: {path.relative_to(ROOT).as_posix()}")
    meta = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip().strip('"')
    return meta, text[match.end():].lstrip("\n")


def adapt(text):
    for before, after in COMMON_REPLACEMENTS.items():
        text = text.replace(before, after)
    return SKILL_CALL.sub(r"$\1", text)


def codex_name(role):
    return role.replace("-", "_")


def skill_content(path):
    meta, body = split_source(path)
    name = path.parent.name
    body = adapt(body)
    if meta.get("context") == "fork" and meta.get("agent"):
        heading, rest = body.split("\n", 1)
        note = FORK_NOTE.format(agent=meta["agent"], codex_agent=codex_name(meta["agent"]))
        body = f"{heading}\n\n{note}{rest}"
    if name == "dev-orchestrator":
        body, count = re.subn(r"## 모델 라우팅\n.*?(?=\n## )", lambda _: ROUTING, body, count=1, flags=re.S)
        if count != 1:
            raise ValueError("dev-orchestrator: '## 모델 라우팅' 절이 없다")
    description = json.dumps(adapt(meta["description"]), ensure_ascii=False)
    return f"---\nname: {name}\ndescription: {description}\n---\n\n{body.rstrip()}\n"


def role_content(path):
    _, body = split_source(path)
    return (f"# {path.stem}\n\nCodex 역할 지침. 정본은 `.claude/agents/{path.name}`, "
            f"실행 방식은 `docs/claude-agents.md` 의 Codex 절을 따른다.\n\n{adapt(body).rstrip()}\n")


def load_codex_profiles(path):
    config = json.loads(path.read_text(encoding="utf-8"))
    models, efforts = config["models"], config["reasoning_effort"]
    if set(models) != {"fast", "design", "work"}:
        raise ValueError("models 는 fast · design · work 를 가져야 한다")
    if set(efforts) != FAST_ROLES | {"design", "work"}:
        raise ValueError("reasoning_effort 는 explorer · git-ops · design · work 를 가져야 한다")
    if any(not isinstance(m, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", m) for m in models.values()):
        raise ValueError("Codex 모델 ID 형식이 틀렸다")
    if any(e not in REASONING_EFFORTS for e in efforts.values()):
        raise ValueError("Codex 추론 강도가 틀렸다")
    return {key: (models["fast" if key in FAST_ROLES else key], effort) for key, effort in efforts.items()}


def validate_catalog(path, profiles):
    catalog = json.loads(path.read_text(encoding="utf-8"))
    supported = {
        model["slug"]: {level["effort"] for level in model["supported_reasoning_levels"]}
        for model in catalog["models"] if model.get("visibility") == "list"
    }
    for role, (model, effort) in profiles.items():
        if model not in supported:
            raise ValueError(f"{role}: {model} 은 {path} 에서 고를 수 없다")
        if effort not in supported[model]:
            raise ValueError(f"{role}: {model} 은 {effort} 를 지원하지 않는다")


def profile_of(role):
    if role in FAST_ROLES:
        return role
    return "design" if role in DESIGN_ROLES else "work"


def codex_agent_content(path, profiles):
    meta, _ = split_source(path)
    body = role_content(path)
    if "'''" in body:
        raise ValueError(f"역할 본문에 ''' 가 있어 TOML 리터럴로 못 쓴다: {path.relative_to(ROOT).as_posix()}")
    model, effort = profiles[profile_of(path.stem)]
    description = json.dumps(adapt(meta["description"]), ensure_ascii=False)
    return (f"# {GENERATED_NOTE}\n"
            f'name = "{codex_name(path.stem)}"\n'
            f"description = {description}\n"
            f'model = "{model}"\n'
            f'model_reasoning_effort = "{effort}"\n'
            f"developer_instructions = '''\n{body}'''\n")


def agents_md_content(path):
    text = path.read_text(encoding="utf-8")
    # 첫 제목과 Claude Code 안내 문장을 떼고 본문만 옮긴다.
    body = re.sub(r"\A# CLAUDE\.md\n+(?:This file provides guidance[^\n]*\n+)?", "", text)
    return f"{AGENTS_HEADER}\n{adapt(body).rstrip()}\n{AGENTS_FOOTER}"


def expected_files(profiles):
    skills = sorted((ROOT / ".claude/skills").glob("*/SKILL.md"))
    names = {p.parent.name for p in skills}
    if names != set(SKILL_NAMES):
        raise ValueError(f"SKILL_NAMES 를 원본 스킬과 맞춘다: 추가={sorted(names - set(SKILL_NAMES))}, "
                         f"없음={sorted(set(SKILL_NAMES) - names)}")
    roles = sorted((ROOT / ".claude/agents").glob("*.md"))
    if not FAST_ROLES | DESIGN_ROLES <= {p.stem for p in roles}:
        raise ValueError("FAST_ROLES · DESIGN_ROLES 의 역할이 .claude/agents 에 없다")
    expected = {ROOT / ".agents/skills" / p.parent.name / "SKILL.md": skill_content(p) for p in skills}
    expected.update({ROOT / ".agents/roles" / p.name: role_content(p) for p in roles})
    expected.update({ROOT / ".codex/agents" / f"{codex_name(p.stem)}.toml": codex_agent_content(p, profiles)
                     for p in roles})
    expected[ROOT / "AGENTS.md"] = agents_md_content(ROOT / "CLAUDE.md")
    return expected, len(skills), len(roles)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true", help="생성본을 다시 쓴다. 없으면 어긋남만 검사한다")
    parser.add_argument("--model-catalog", type=Path, help="Codex 로컬 models_cache.json 으로 모델 · 추론 강도를 대조한다")
    args = parser.parse_args(argv)
    try:
        profiles = load_codex_profiles(CODEX_MODEL_CONFIG)
        if args.model_catalog is not None:
            validate_catalog(args.model_catalog, profiles)
        expected, n_skills, n_roles = expected_files(profiles)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))

    actual = (set((ROOT / ".agents/skills").glob("*/SKILL.md")) | set((ROOT / ".agents/roles").glob("*.md"))
              | set((ROOT / ".codex/agents").glob("*.toml")))
    stale = actual - set(expected) - {ROOT / p for p in STANDALONE_SKILLS}
    failures = []
    for path, content in expected.items():
        if args.write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
        elif not path.exists() or path.read_bytes() != content.encode("utf-8"):
            failures.append(path.relative_to(ROOT).as_posix())
    failures.extend(f"stale(원본이 없는 생성본 — 지운다): {p.relative_to(ROOT).as_posix()}" for p in sorted(stale))
    if failures:
        print("Claude 원본과 Codex 생성본이 어긋난다:\n  " + "\n  ".join(failures))
        print(f"고치기: {SYNC_COMMAND}")
        return 1
    print(f"{'Synced' if args.write else 'Verified'} AGENTS.md, {n_skills} skills, {n_roles} roles, {n_roles} Codex agents.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
