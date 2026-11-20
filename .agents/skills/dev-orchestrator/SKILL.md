---
name: dev-orchestrator
description: "trading-desk 개발 작업을 SIMPLE·FEATURE·BUG·REFACTOR·ARCHITECTURE·LARGE_FEATURE 유형과 복잡도 T0–T3 로 분류한 뒤, 필요한 하위 에이전트(.agents/roles)만 골라 탐색 → 계획 → 구현 → 검증 → 검토 → 반영 순서로 진행한다. 인증·세션·브로커 키·DB 스키마·배포처럼 실수 비용이 큰 신호를 세어 검토 깊이를 정한다. $dev-orchestrator 요청과 비단순 개발 작업이 트리거다."
---

# Development Orchestrator

메인 세션이 작업 유형과 복잡도를 정한 뒤 필요한 역할만 실행한다. 역할 표의 정본은 [docs/claude-agents.md](../../../docs/claude-agents.md),
Git 규칙의 정본은 [docs/git-workflow.md](../../../docs/git-workflow.md) 다.

## 모델 라우팅

모델 · 추론 강도의 정본은 `.codex/agent-models.json` 이다(아래는 Claude 쪽과 같은 무게 배치).

| 일 | 맡는 곳 | 모델 등급 · effort |
|----|---------|--------------------|
| 분류 · 계획 · 검증 실행 · 완료 판단 | 메인 세션 | 세션 모델 |
| 위치 · 호출 흐름을 모르는 탐색 | `explorer` | fast · medium |
| 이슈 시작, 푸시 · PR 생성, diff 확인, 머지 후 정리 | `$issue-start` · `$pr` · `git_ops` | fast · low |
| 설계 (`ARCHITECTURE`, `T3`) | `architect` | design · max |
| 단순 구현 (`T0` ~ `T1`) | `implementer` 또는 메인 세션 | work · high |
| 어려운 구현 · 치명적 버그 · 중요한 기능 (`T2` ~ `T3`) | `senior_implementer` | work · xhigh |
| 검토 | `security_reviewer` · `reviewer` | work · xhigh |

- 위치를 아는 파일 몇 개 읽기, 커밋 한 번 같은 단일 작업은 메인 세션이 직접 한다. 하위 에이전트는 띄우는 비용이 더 크다.
- `max` 는 설계에만 쓴다 — 토큰이 많이 들고 과하게 생각하기 쉽다.
- 하위 에이전트는 대화 기록을 보지 못한다. 경로 · 브랜치 · 이슈 번호 · 검증 결과 · 원하는 출력 형식을 프롬프트에 담는다.
- Codex 에서는 `spawn_agent(agent_type="explorer", ...)` 처럼 `.codex/agents/` 의 이름(하이픈 대신 밑줄)을 쓴다.
  위임이 허용되지 않았거나 하위 에이전트를 쓸 수 없으면 메인 세션이 `.agents/roles/<역할>.md` 를 읽고 같은 순서로 직접 한다.

## 1. CLASSIFY

| 유형 | 대상 |
|------|------|
| `SIMPLE` | 위치와 계약이 분명한 문구 · 설정 · 작은 테스트 |
| `FEATURE` | 백엔드 또는 프론트 한쪽의 일반 기능 |
| `BUG` | 원인 분석이 필요한 버그. 스트림(SSE) 순서 · 워커 스레드 · 동시 실행이 얽히면 여기 |
| `REFACTOR` | 동작을 보존하는 구조 변경 |
| `ARCHITECTURE` | 저장소 · 인증 구조 · TradingAgents 경계 · 브로커 연동 · 배포 설계 |
| `LARGE_FEATURE` | 백엔드 + 프론트에 걸친 기능 — `[BE]` · `[FE]` 이슈로 나눠 순서대로 |

## 2. COMPLEXITY

| 등급 | 의미 | 기본 깊이 |
|------|------|-----------|
| `T0` | 위치 · 계약 · 검증이 확정된 국소 변경 | 메인 세션이 직접, 대상 테스트로 끝 |
| `T1` | 한 모듈 안의 일반 구현 | 구현 + 대상 테스트 |
| `T2` | 여러 모듈, 공개 API(프론트 계약), 스키마 중 하나 | 구현 + 검토 1~2 |
| `T3` | 실수 비용이 큰 작업 | 설계 고정 + 구현 + 해당 검토 전부 + `reviewer` |

`T3` 는 아래 신호 중 **2개 이상**이 실제로 범위에 있을 때다.

- **인증 · 세션 · 쿠키 · CSRF** (이 앱은 계좌와 ChatGPT 플랜을 쥐고 있다)
- **브로커(토스) 키 · 계좌 정보 · 주문 경로**
- DB 스키마 변경 · 마이그레이션
- 프론트가 보는 API 계약 변경
- TradingAgents 의존성(SHA) 변경 또는 `events.py` · `plan.py` 매핑 변경
- 실행 동시성(RunManager · Hub · 워커 스레드)
- 배포 · 인그레스 · 비밀 주입
- 장애 원인 미상

## 3. EXPLORE

- `T0` 이거나 위치가 이미 특정되면 생략한다.
- 위치가 불명확할 때만 `explorer` 를 부른다. 질문과 범위(`backend/desk/...`, `frontend/src/...`)를 좁혀 넘긴다.
- TradingAgents 내부 동작은 설치된 패키지(`backend/.venv/Lib/site-packages/tradingagents`) 또는 포크 `D:\ProjectWorkSpace\TradingAgents` 에서 확인한다. 포크는 고치지 않는다.

## 4. PLAN

- `T0` · `T1` 은 메인 세션이 범위와 순서를 짧게 고정한다.
- `T2` 이상은 구현 전에 범위 · 보존할 동작 · 검증 방법을 고정한다.
- `ARCHITECTURE` 와 `T3` 는 `architect` 의 설계가 저장소 증거와 맞을 때만 구현으로 넘긴다.

## 5. IMPLEMENT

| 등급 | 맡는 곳 |
|------|---------|
| `T0` | 메인 세션이 직접 |
| `T1` | 메인 세션 또는 `implementer` (범위 · 대상 테스트를 고정해 넘긴다) |
| `T2` · `T3` · 치명적 버그 | `senior_implementer` (고정한 설계 · 보존할 동작 · 검증 방법을 넘긴다) |

- 공유 파일(`api.py`, `store.py`, `settings.py`, `frontend/src/api.ts`)을 고치는 쓰기 작업은 한 번에 하나만. 구현 역할을 병렬로 띄우지 않는다.
- 구현 역할이 범위 밖 신호(인증 · 브로커 · 스키마 · 동시성)로 멈추면 등급을 올려 다시 고정한다.

## 6. VERIFY

메인 세션이 직접 돌린다. 하위 에이전트의 "통과했다" 를 완료 근거로 쓰지 않는다.

```bash
cd backend && .venv/Scripts/python -m ruff check desk tests && .venv/Scripts/python -m pytest -q
cd frontend && npm run build
```

- UI 를 바꿨으면 `DESK_FAKE_RUNS=true` 로 백엔드 · 프론트를 띄워 실제로 화면을 확인한다(실제 LLM 호출은 쿼터를 쓴다).
- PostgreSQL 경로를 바꿨으면 `DESK_TEST_DATABASE_URL` 로 PostgreSQL 대상 테스트도 돌린다(없으면 건너뛰었다고 보고).

## 7. REVIEW

| 범위 | 검토 역할 |
|------|-----------|
| 인증 · 세션 · 쿠키 · 비밀 · 브로커 키 · 인그레스 | `security_reviewer` |
| 최종 diff 전반 | `reviewer` |

- `T1` · `T2` 는 해당 검토 1개, `T3` 는 둘 다 — 읽기 전용이라 위임이 허용되면 **병렬로** 부르고(`spawn_agent` 여러 번 → `wait_agent`), 아니면 메인 세션이 차례로 검토한다.
- CRITICAL/HIGH 만 고친 뒤 다시 검증한다.

## 8. SHIP

- 커밋은 메인 세션이 경로를 하나씩 스테이징해 만든다(`docs/git-workflow.md` §4).
- PR 은 `$pr` 에 **검증 결과를 인자로** 넘긴다. 예: `$pr 검증: pytest 24 passed · ruff OK · npm run build OK · 참고: security-reviewer HIGH 0`
- 머지 후 정리 · 이슈 체크박스 갱신은 PR 번호 · 완료 항목을 적어 `git_ops` 에 맡긴다.
- 배포가 필요한 변경이면 [docs/deploy.md](../../../docs/deploy.md) 순서를 따른다.

## 완료 조건

1. 요청한 동작이 구현되었다.
2. ruff · pytest · 프론트 빌드가 실제로 돌아 통과했다.
3. 필요한 검토가 끝났고 CRITICAL/HIGH 가 해결되었다.
4. 불필요한 변경 · 비밀 값 커밋이 없다.
5. 보고에 `TYPE`, `COMPLEXITY`(T3 면 해당 신호), 사용한 역할, 실행한 검증, 남은 위험이 있다.
