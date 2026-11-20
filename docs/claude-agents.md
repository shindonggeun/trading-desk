# Claude 스킬 · 에이전트

`.claude/skills/*` 는 `/스킬명` 으로 부르고, `.claude/agents/*` 는 스킬과 메인 세션이 하위 에이전트로 띄운다.
commerce-foundry 의 구성을 이 저장소 규모(1인, Python 백엔드 하나 + React 프론트 하나)에 맞게 줄였다.

## 스킬

| 스킬 | 하는 일 | 실행 |
|------|---------|------|
| `/dev-orchestrator` | 작업을 유형 · 복잡도(T0~T3)로 분류하고 필요한 역할만 골라 탐색 → 계획 → 구현 → 검증 → 검토 → 반영 | 메인 세션 |
| `/issue` | 한국어 템플릿으로 이슈를 쓰고 `gh issue create` 로 등록 | 메인 세션 |
| `/issue-start <번호>` | 이슈를 읽어 브랜치(또는 워크트리)를 `origin/develop` 에서 `--no-track` 으로 따고 작업 계획을 낸다 | `git-ops` 포크 |
| `/pr 검증: ...` | 템플릿을 채워 develop 대상 PR 을 만든다. 검증 결과가 인자에 없으면 만들지 않는다 | `git-ops` 포크 |

## 에이전트

일의 무게에 맞춰 모델 · effort 를 고정한다. `model` 은 전체 모델 ID 로 적는다 — 별칭(`opus` 등)은 메인 세션이 같은 계열의
다른 버전이면 그 버전을 따라가기 때문이다.

| 에이전트 | 역할 | 모델 · effort | 쓰기 |
|----------|------|---------------|------|
| `explorer` | 파일 위치 · 호출 흐름 · 영향 범위 찾기 | `claude-haiku-5-5` · medium | 아니오 |
| `git-ops` | 브랜치 · 워크트리 · 커밋 · 푸시 · 이슈 · PR · diff 확인 · 머지 후 정리 | `claude-haiku-5-5` · low | 저장소 밖 임시 파일만 |
| `architect` | 경계 · 보안 구조 · 저장소 · 배포 설계안 | `claude-fable-5-2` · max | 아니오 |
| `implementer` | 단순 구현(T0~T1) — 위치 · 계약 · 검증이 정해진 한 모듈 안 | `claude-sonnet-5-5` · high (필요하면 xhigh) | 예 |
| `senior-implementer` | 어려운 구현(T2~T3) · 치명적 버그 · 인증/브로커/스키마/동시성 | `claude-opus-5-5` · xhigh | 예 |
| `security-reviewer` | 인증 · 세션 · 쿠키 · CSRF · 비밀정보 · 브로커 키 경계 검토 | `claude-opus-5-5` · xhigh | 아니오 |
| `reviewer` | 최종 diff 의 정확성 · 회귀 · 테스트 검토 | `claude-opus-5-5` · xhigh | 아니오 |

`/issue-start` · `/pr` 는 `git-ops` 로 포크되며 스킬 frontmatter 에 `claude-haiku-5-5` · low 를 적어 둔다.

- `T0` 은 메인 세션이 직접, `T1` 은 메인 세션 또는 `implementer`, `T2` 이상과 치명적 버그는 `senior-implementer` 에 맡긴다.
  구현 역할도 검사를 돌리지만 완료 판단은 메인 세션이 같은 명령을 다시 돌려 한다. 쓰기 역할은 한 번에 하나만.
- 비용 · 쿼터: `max` 는 설계(`architect`)에만 쓴다 — 토큰이 많이 들고 과하게 생각하기 쉽다. `xhigh` 는 검토 · 어려운 구현에만.
  Fable 은 플랜에 따라 사용 크레딧으로 청구될 수 있다(`/model` 피커에 "Requires usage credits").
- `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` 이 켜져 있으면 frontmatter 의 `model` 은 무시된다. `CLAUDE_CODE_EFFORT_LEVEL` 은 `effort` 를 덮는다.
  실제 모델 · effort 는 하위 에이전트가 도는 동안 `/tasks` 에서 확인한다.

## 원칙

- 하위 에이전트는 대화 기록을 보지 못한다. 경로 · 브랜치 · 이슈 번호 · 검증 결과를 프롬프트에 담는다.
- 하위 에이전트의 "통과했다" 를 완료 근거로 쓰지 않는다. 검증 명령은 메인 세션이 직접 돌린다.
- 읽기 전용 검토 역할은 한 메시지에서 병렬로 부른다.

## Codex

Codex 도 같은 스킬 · 역할을 쓴다. **정본은 Claude 쪽**(`CLAUDE.md` · `.claude/skills` · `.claude/agents`)이고,
Codex 파일은 `scripts/sync-agent-skills.py` 가 만드는 생성본이다 — 직접 고치지 않는다.

```text
AGENTS.md                         CLAUDE.md 본문을 Codex 표기로 바꾼 것 (생성본)
.agents/skills/<이름>/SKILL.md     스킬 4개 (생성본)
.agents/roles/<역할>.md            역할 지침 7개 (생성본)
.codex/agents/<역할>.toml          Codex 하위 에이전트 7개 (생성본, 이름은 밑줄)
.codex/agent-models.json          Codex 역할별 모델 등급 · 추론 강도 (직접 고친다)
.agents/skills/codex-team/SKILL.md Codex 전용 스킬 — 독립 Codex 세션 팀(직접 고친다, docs/codex-team.md)
scripts/sync-agent-skills.py      생성 · 검사. CI codex-check 가 PR 마다 검사한다
```

`.claude/` 나 `CLAUDE.md` 를 고치면 같은 커밋에서 다시 만든다:

```bash
python -X utf8 scripts/sync-agent-skills.py --write   # 생성
python -X utf8 scripts/sync-agent-skills.py           # 검사 (CI 와 같다)
python -B -X utf8 -m unittest discover -s scripts -p "test_*.py"
```

| Claude Code | Codex | 비고 |
|-------------|-------|------|
| `/issue` · `/issue-start <번호>` · `/pr 검증: ...` · `/dev-orchestrator` | `$issue` · `$issue-start <번호>` · `$pr 검증: ...` · `$dev-orchestrator` | Codex 는 `/issue-start` · `/pr` 를 포크하지 않는다 — 메인 세션이 실행하거나 `git_ops` 에 맡긴다 |
| `explorer` (Haiku 5.5 · medium) | `explorer` | fast · medium |
| `git-ops` (Haiku 5.5 · low) | `git_ops` | fast · low |
| `architect` (Fable 5.2 · max) | `architect` | design · max |
| `implementer` (Sonnet 5.5 · high) | `implementer` | work · high |
| `senior-implementer` (Opus 5.5 · xhigh) | `senior_implementer` | work · xhigh |
| `security-reviewer` · `reviewer` (Opus 5.5 · xhigh) | `security_reviewer` · `reviewer` | work · xhigh |

Codex 등급은 `.codex/agent-models.json` 의 `models` 에서 실제 ID 로 바뀐다(fast `gpt-6.1-terra` · design `gpt-6.2-astra` · work `gpt-6.2-sol`).
역할마다 `roles.<역할>` 에 등급과 effort 를 적고, 역할 목록은 `.claude/agents` 와 같아야 한다(스크립트가 검사한다).

- 하위 에이전트는 `spawn_agent(agent_type="security_reviewer", ...)` 로 띄우고 `wait_agent` 로 모은다. 위임이 허용되지 않았거나
  쓸 수 없으면 메인 세션이 `.agents/roles/<역할>.md` 를 읽고 차례로 한다.
- Codex 는 프로젝트를 신뢰(trusted)했을 때만 `.codex/` 를 읽는다. 역할 파일은 sandbox · 승인 정책을 바꾸지 못하므로 읽기 전용은 역할 본문으로만 지킨다.
- 모델을 바꿀 때는 `.codex/agent-models.json` 을 고치고, 로컬 Codex 모델 캐시가 있으면 대조한 뒤 생성한다:
  `python -X utf8 scripts/sync-agent-skills.py --model-catalog "$env:USERPROFILE/.codex/models_cache.json" --write`
- 확인: Codex 새 세션에서 이 저장소를 열고 "읽은 지침 파일과 쓸 수 있는 프로젝트 스킬 · 역할을 나열해" 라고 묻는다.
  `AGENTS.md`, 스킬 4개, 역할 7개가 나오면 된다. 이미 열린 세션은 목록이 갱신되지 않으니 새로 연다.
