---
name: pr
description: "trading-desk Pull Request 를 한국어 템플릿으로 작성하고 gh 로 develop 대상 PR 을 만든다. 브랜치명에서 이슈 번호를 읽어 Issue Number 를 채우고, 변경 경로로 영역 라벨을, assignee 는 @me 로 지정한다. /pr 요청, PR body, pull request 생성, '[BE] feat: ... PR 올려줘' 가 트리거다."
argument-hint: "검증: <실행한 명령과 결과> [참고: ...] [마이그레이션: ...] [--draft]"
context: fork
agent: git-ops
model: claude-haiku-5-5
effort: low
background: false
---

# PR

trading-desk PR 본문을 작성하고 **gh 로 생성**한다. 대화 기록 없이 실행되므로 필요한 정보는 인자와 저장소에서만 얻는다.

## Read First

- [docs/git-workflow.md](../../../docs/git-workflow.md) §6~7
- [.github/PULL_REQUEST_TEMPLATE.md](../../../.github/PULL_REQUEST_TEMPLATE.md)

## 모드

| 요청 | 동작 |
|------|------|
| 기본 (`/pr`) | 본문 작성 → 푸시 → **PR 생성** → URL 출력 |
| "초안만", `--draft-body` | 제목 · 본문만 출력 |
| "드래프트 PR", `--draft` | `gh pr create --draft` |

## 입력 (인자)

| 인자 | 쓰는 곳 | 없을 때 |
|------|---------|---------|
| `검증:` 실제로 돌린 명령과 결과 | `검증 내역` | 본문 파일까지만 만들고 **푸시 · 생성하지 않는다** — "검증 내역 필요" 로 보고 |
| `참고:` 리뷰 결과 요약, 30파일 초과 이유, 배포 메모 | `참고 사항` | 저장소에서 확인한 사실만 |
| `마이그레이션:` 새 Alembic 리비전 · 운영 적용 메모 | `DB 마이그레이션` | `backend/desk/migrations/versions/` 가 바뀌었으면 PR 을 만들지 않는다 |

## 절차

1. **변경 범위**

   ```bash
   git status --short
   git fetch origin
   git log --oneline origin/develop..HEAD
   git diff --stat origin/develop...HEAD
   git diff --name-only origin/develop...HEAD
   ```

   - 커밋되지 않은 변경은 PR 에 들어가지 않는다고 알린다. 대신 커밋하지 않는다.
   - **30파일 / 1,000줄을 넘으면** 인자 `참고:` 의 이유를 적는다. 이유가 없으면 PR 을 만들지 않고 보고한다.

2. **이슈 번호** — 브랜치명 `<type>/<영역>/<번호>-<요약>` 에서 읽는다. `gh issue view <번호> --json title` 로 **PR 제목을 이슈 제목과 같게** 둔다. 번호를 못 찾으면 멈춘다.

3. **라벨** — 경로로 정한다: `backend/**` → `backend`, `frontend/**` → `frontend`, `docker-compose*.yml` · `deploy/**` · `scripts/**` · `.github/**` · `.claude/**` → `infra`.
   타입 라벨: `feat` → `enhancement`, `fix` → `bug`, `docs` → `documentation`. 인증 · 세션 · 브로커 키를 건드렸으면 `security`.

4. **본문** — 템플릿을 채워 Write 도구로 저장소 밖 임시 파일에 저장한다.
   - 실제 템플릿의 절 제목, 순서, 타입과 사전 확인 체크리스트를 유지한다. DB 변경이 없으면 마이그레이션 절에 `해당 없음`을 적는다.
   - 확인하지 않은 체크박스는 체크하지 않는다.
   - `검증 내역` 에는 인자로 받은 **실제로 돌린 명령과 결과**만 적는다.
   - 생성과 기존 PR 본문 갱신 모두 게시 전에 검사한다. 실패하면 본문을 고쳐 재검사한다.

   ```bash
   python -X utf8 scripts/check_github_template.py pr <본문 파일> --issue <번호>
   ```

5. **PR 상태 확인 · 푸시 · 생성**

   ```bash
   gh pr view --json number,state,url
   ```

   - `MERGED` · `CLOSED` 면 푸시하지 않고 보고한다.
   - `OPEN` 이면 푸시 후 `gh pr edit <번호> --body-file <본문 파일>`.
   - 없으면:

   ```bash
   git push -u origin HEAD
   gh pr create --base develop --title "<이슈 제목>" --body-file <본문 파일> \
     --assignee @me --label backend --label enhancement
   ```

6. **출력** — PR URL, 라벨, 그리고 머지 방법: `gh pr merge <번호> --rebase` (CI 통과 후). 머지 뒤 이슈 체크박스 갱신 · `gh issue close <번호>`, 로컬 정리는 `docs/git-workflow.md` §7-2.

## Rules

- 한국어로 쓴다. 기능 단위로 묶고 파일 나열식 changelog 를 피한다.
- 제목 prefix: `backend/` → `[BE]`, `frontend/` → `[FE]`, 문서만 → `[DOCS]`, 배포 · CI · Claude 설정 → `[INFRA]`.
- 토스 키 · 계좌 · 비밀번호 · 세션 토큰 · 서버 `.env` 값은 넣지 않는다.
