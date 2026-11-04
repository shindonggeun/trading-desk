---
name: issue-start
description: "trading-desk GitHub 이슈 번호로 작업을 시작한다. 이슈를 gh 로 읽어 브랜치명(<type>/<영역>/<번호>-<요약>)을 만들고 최신 origin/develop 에서 --no-track 으로 분기(또는 워크트리 생성)한 뒤, 이슈의 작업 상세를 작업 계획으로 정리한다. /issue-start 2, '2번 이슈 시작하자', '이슈 브랜치 따줘', '워크트리 파줘' 요청이 트리거다."
argument-hint: "<이슈 번호> [--worktree]"
context: fork
agent: git-ops
model: haiku
effort: low
background: false
---

# Issue Start

이슈 하나의 작업을 규칙대로 시작한다. 대화 기록 없이 실행되므로 필요한 정보는 인자(이슈 번호, `--worktree`)와 저장소에서만 얻는다.

## Read First

- [docs/git-workflow.md](../../../docs/git-workflow.md) §3 브랜치, §4-1 작업 트리 공유, §7-2 워크트리 위치

## 절차

1. **이슈 읽기** — `gh issue view <번호> --json number,title,state,assignees,labels,body`. 닫힌 이슈면 멈추고 알린다.

2. **브랜치명** — `<type>/<영역>/<번호>-<요약>`

   | 제목 | 값 |
   |------|-----|
   | `feat` → `feature`, 그 외 타입은 그대로 | `type` |
   | `[BE]` → `be`, `[FE]` → `fe`, `[INFRA]` → `infra`, `[DOCS]` · 양쪽 → `common` | `영역` |
   | 제목 요약을 영문 kebab-case 2~4단어로 | `요약` |

3. **모드 결정** — `git status --short`. 인자에 `--worktree` · `워크트리` 가 있거나 커밋되지 않은 변경이 있으면 **워크트리 모드**다. `git stash` 로 치우지 않는다.

4. **분기**

   ```bash
   git fetch origin
   # 기본
   git switch --no-track -c <브랜치명> origin/develop
   # 워크트리 모드 — 메인 저장소 밖 trading-desk-wt/<번호>-<요약>
   MAIN=$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")
   git worktree add --no-track -b <브랜치명> "$MAIN/../trading-desk-wt/<번호>-<요약>" origin/develop
   ```

   - 분기 뒤 `git rev-parse --abbrev-ref @{u}` 가 비어 있어야 한다. upstream 은 첫 푸시 `git push -u origin HEAD` 가 잡는다.
   - `origin/develop` 이 없으면 멈추고 알린다.
   - 같은 이름의 브랜치 · 워크트리(`git worktree list`)가 있으면 새로 만들지 않고 보고한다.
   - 워크트리면 백엔드 venv 가 없다 — 보고에 `cd backend && python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"` 를 적는다.

5. **작업 계획** — 이슈의 `작업 상세 내용` · `완료 기준` 을 순서대로 정리한다.
   - 인증 · 세션 · 스키마 · 배포 이슈면 `/dev-orchestrator` 로 분류(T2~T3)를 권한다.

## 출력

```text
ISSUE START
===========
Issue:    #2 [BE] feat: 세션 쿠키 로그인 · 로그아웃 API 를 둔다
Branch:   feature/be/2-session-login (from origin/develop @ <short sha>)
Worktree: D:/.../trading-desk-wt/2-session-login   (워크트리 모드일 때만)
Labels:   backend, enhancement, security

Plan:
1. ...

Done When:
- ...
```

## Rules

- 커밋 · 푸시는 하지 않는다. 브랜치(워크트리) 생성과 계획까지만.
- 커밋 제목에 `(#번호)` 를 붙이도록 안내한다.
