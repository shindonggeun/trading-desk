# Git 워크플로

> 저장소 전체 기준이다. 엔트리는 루트 `CLAUDE.md`. 이슈 · 브랜치 · 커밋 · PR · 머지 규칙의 정본이다.
> commerce-foundry 의 규칙을 1인 · 비공개 저장소에 맞게 줄인 것이다.

## 1. 전체 흐름

```text
이슈(/issue) → 브랜치(/issue-start) → 작업 · 커밋 → PR(/pr) → CI 통과 → Rebase and merge → 브랜치 정리 → 이슈 갱신
                                                                          └→ 배포는 수동(scripts/deploy.sh, §8)
```

**이슈 없이 브랜치를 만들지 않는다.** 이슈가 작업 단위이자 추적 지점이다.

| 브랜치 | 용도 |
|--------|------|
| `main` | 배포본. 릴리스 PR(develop → main)로만 바뀐다 |
| `develop` | 통합 브랜치. 모든 작업 PR 의 base |
| `<type>/<영역>/<번호>-<요약>` | 이슈 하나의 작업 브랜치 |

## 2. 이슈

- **화면 / 기능 단위**로 쪼갠다. 하나의 이슈 = 하나의 PR = **30파일 이내**.
- 명세 · 구현 · 테스트를 한 이슈에 묶는다. 같은 기능의 백엔드 · 프론트는 `[BE]` · `[FE]` 이슈로 나누고 서로 참조한다.
- 제목: `[영역] type: 요약`. 담당자는 언제나 `shindonggeun`.
- 템플릿: `.github/ISSUE_TEMPLATE/` (기능 / 버그)

| 영역 | 범위 | 영역 라벨 |
|------|------|-----------|
| `[BE]` | `backend/` | `backend` |
| `[FE]` | `frontend/` | `frontend` |
| `[INFRA]` | `docker-compose*.yml` · `deploy/` · `scripts/` · `.github/` · `.claude/` | `infra` |
| `[DOCS]` | 문서만 | (없음) |

| 타입 | 타입 라벨 |
|------|-----------|
| `feat` | `enhancement` |
| `fix` | `bug` |
| `docs` | `documentation` |

인증 · 세션 · 브로커 키를 건드리는 이슈에는 `security` 라벨을 더한다.

## 3. 브랜치

```text
<type>/<영역>/<이슈번호>-<요약>
```

| 자리 | 값 |
|------|-----|
| `type` | `feature` \| `fix` \| `chore` \| `refactor` \| `docs` \| `test` \| `style` |
| `영역` | `be` \| `fe` \| `infra` \| `common` (`[DOCS]` 나 양쪽에 걸치면 `common`) |
| `요약` | 영문 kebab-case 2~4단어 |

```bash
git fetch origin
git switch --no-track -c feature/be/2-session-login origin/develop
# 워크트리로 시작할 때 — 저장소 밖 ../trading-desk-wt/<번호>-<요약>
git worktree add --no-track -b feature/be/2-session-login ../trading-desk-wt/2-session-login origin/develop
git push -u origin HEAD     # 첫 푸시가 같은 이름의 원격 브랜치를 upstream 으로 잡는다
```

`--no-track` 을 붙인다 — 붙이지 않으면 upstream 이 `origin/develop` 이 되어 맨 `git push` 가 develop 을 향할 수 있다.
`/issue-start <번호>` 가 이 과정을 대신한다.

## 4. 커밋

- 형식: `[영역] type: 요약 (#이슈번호)` — 예: `[BE] feat: 세션 쿠키 로그인 · 로그아웃 API 를 둔다 (#2)`
- **의미 단위로 나눈다.** `Rebase and merge` 라 커밋이 그대로 develop 에 남는다. "wip" · "fix typo" 커밋을 남기지 않는다.
- **본문에 실제 변경을 한국어 목록으로 쓴다.** 인증 · 스키마 · API 계약 · 호환성 판단은 해당 항목에 구체적으로 적는다.
- AI 가 기여한 커밋은 마지막에 실제 도구에 맞는 `Co-Authored-By` trailer 를 빈 줄로 구분해 넣는다.
- 멀티라인 메시지는 UTF-8 임시 파일에 써서 `git commit -F <파일>` 로 넘기고, 직후 `git log -1 --format=%B` 로 확인한다.

### 4-1. 작업 트리를 혼자 쓰지 않을 수 있다

여러 Claude 세션이 같은 작업 트리를 쓸 수 있다.

| 하지 않는다 | 대신 |
|-------------|------|
| `git add -A` · `git add .` · `git commit -a` | 경로를 하나씩 `git add <경로>`, 커밋 전 `git diff --cached --name-only` |
| `git stash` · `git reset --hard` · `git clean` | 중간 상태 검증은 워크트리로 한다 |

### 4-2. 비공개 저장소지만 비밀은 넣지 않는다

`shindonggeun/trading-desk` 는 private 이다. 그래도 아래는 **커밋 · 이슈 · PR 어디에도** 쓰지 않는다.
저장소가 공개로 바뀌거나 이미지 · 로그로 새어 나가도 안전해야 한다.

- 토스증권 Open API client id/secret, 계좌 번호, 보유 수량 · 평가 금액 실값
- DB 비밀번호, 로그인 비밀번호 · 해시, 세션 토큰, ChatGPT 로그인 파일(`chatgpt-auth.json`)
- 서버 `.env` 원문

키 **이름**만 `.env.example` 에 둔다. 값은 서버의 `~/deploy/trading-desk/.env`(권한 600)에만 있다.

## 5. develop 동기화는 rebase

```bash
git fetch origin && git rebase origin/develop
```

## 6. PR

```bash
git push -u origin HEAD
gh pr create --base develop --title "<이슈 제목>" --body-file <본문> --assignee @me --label backend --label enhancement
```

- 본문은 `.github/PULL_REQUEST_TEMPLATE.md`. `/pr` 스킬을 쓴다. **`Issue Number: #N` 을 반드시 채운다.**
- 제목은 이슈 제목과 같게 둔다. 라벨 · assignee 는 생성 시점에 붙인다.
- 1인 저장소라 셀프 머지한다. 단 **PR 은 생략하지 않는다** — CI 와 검토 기록이 남는 곳이다.
- **30파일 / 1,000줄을 넘으면** 이유를 본문에 적는다.

## 7. 머지 · 이슈 갱신 · 릴리스

- 작업 PR: **`gh pr merge <번호> --rebase`** (CI 통과 후). 원격 브랜치는 GitHub 가 자동으로 지운다.
- 이슈는 develop 머지로 자동으로 닫히지 않는다(기본 브랜치가 `main`). 체크박스를 갱신하고 `gh issue close <번호>`.
- 릴리스(develop → main): **`gh pr merge <번호> --merge`** (merge commit).

### 7-1. 저장소 설정

| 설정 | 값 |
|------|-----|
| Allow rebase merging | 켬 — 작업 PR 의 유일한 머지 방식 |
| Allow merge commits | 켬 — 릴리스 전용 |
| Allow squash merging | 끔 |
| Automatically delete head branches | 켬 |

### 7-2. 로컬 정리

```bash
git fetch --prune origin
git switch develop && git merge --ff-only origin/develop
git worktree remove <워크트리 경로>     # 워크트리였다면
git cherry -v origin/develop <브랜치>   # 전부 '-' 인지 확인한 뒤
git branch -D <브랜치>                  # 리베이스 머지라 SHA 가 달라 -d 는 거부된다
```

## 8. 배포

CI 는 GitHub Actions(`.github/workflows/ci.yml` — 백엔드 ruff · pytest, 프론트 빌드)다. 배포는 자동이 아니다.

| 브랜치 | 배포 |
|--------|------|
| `main` | `scripts/deploy.sh` 로 `backend-server`(`192.168.0.9`)에 올린다 — 정본 [docs/deploy.md](deploy.md) |
| 그 외 | 없음 |
