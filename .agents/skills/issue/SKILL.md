---
name: issue
description: "trading-desk GitHub 이슈를 한국어 템플릿으로 작성하고 gh CLI 로 바로 등록한다. 담당자는 언제나 shindonggeun, 영역 라벨(backend·frontend·infra)과 타입 라벨을 붙인다. $issue 요청, '이슈 만들어줘/등록해줘/쪼개줘', [BE]/[FE] feat: ... 제목 생성, 기능을 여러 이슈로 나눠 일괄 등록할 때 사용한다."
---

# Issue

trading-desk 이슈를 작성하고 **gh 로 바로 등록**한다.

## Read First

- [docs/git-workflow.md](../../../docs/git-workflow.md) §2 — 이슈 단위, 영역, 라벨
- 게시할 유형의 실제 템플릿을 읽는다. [기능](../../../.github/ISSUE_TEMPLATE/feature-issue.md) · [버그](../../../.github/ISSUE_TEMPLATE/bug-issue.md)

## 모드

| 요청 | 동작 |
|------|------|
| 기본 (`$issue ...`) | 작성 → **바로 등록** → 결과 표 출력 |
| "초안만", `--draft` | 제목 · 본문만 출력하고 등록하지 않는다 |
| 큰 기능 ("~ 이슈 쪼개줘") | 화면/기능 단위로 나눈 목록을 표로 먼저 보여주고 등록한다 |

## 1. 쪼개기

- **화면 / 기능 단위**. 하나의 이슈 = 하나의 PR = **30파일 이내**. 명세 · 구현 · 테스트를 한 이슈에 묶는다.
- 같은 기능의 백엔드 · 프론트는 **[BE] 이슈와 [FE] 이슈를 따로** 만들고 본문에서 서로 참조한다.
- 홈랩 인프라(nginx · 인증서 · DB 생성 · fail2ban)는 이 저장소가 아니라 `D:\ProjectWorkSpace\Infra`(`8llow8llowMe/Infra`, **공개**) 이슈다.

## 2. 제목

`[영역] type: 요약` — 영역 `BE` / `FE` / `INFRA` / `DOCS`, 타입 `feat` / `fix` / `chore` / `refactor` / `docs` / `test` / `style`.

예: `[BE] feat: 세션 쿠키 로그인 · 로그아웃 API 를 둔다`, `[FE] feat: 로그인 화면과 로그아웃 버튼을 붙인다`

## 3. 담당자 · 라벨

- assignee: 언제나 `shindonggeun`.
- 영역 라벨: `[BE]` → `backend`, `[FE]` → `frontend`, `[INFRA]` → `infra`, `[DOCS]` → 없음. 양쪽에 걸치면 둘 다.
- 타입 라벨: `feat` → `enhancement`, `fix` → `bug`, `docs` → `documentation`, 그 외 없음.
- 인증 · 세션 · 브로커 키를 건드리면 `security` 를 더한다.
- 표에 있는데 저장소에 없는 라벨은 `gh label create <이름> --color <색> --description <설명>` 로 만든다. **표에 없는 라벨은 만들지 않는다.**

## 4. 본문 템플릿

```markdown
## 어떤 기능인가요? ✏

- [1~3줄]

## 작업 상세 내용 📝

- [ ] [해야 할 일]
- [ ] [해야 할 일]

## 완료 기준 ✅

- [ ] [끝났다고 말할 수 있는 조건 — API 응답, 화면 동작, 테스트]

## 영향 범위(선택)

- BE: ...
- FE: ...

## 참고할만한 자료(선택)

- [관련 문서 · 이슈: #N]
```

- 체크박스는 2~6개. **[BE] 이슈면 API 초안**(메서드 · 경로 · 인증 여부)을 작업 상세에 적는다.
- 버그는 `.github/ISSUE_TEMPLATE/bug-issue.md` 구조(어떤 버그 / 재현 / 예상 · 실제 / 영향도)를 따른다.
- 실제 템플릿의 제목과 순서를 유지한다. Markdown 본문에 YAML 메타데이터를 넣지 않는다. 선택 절에 내용이 없으면 절을 유지하고 `해당 없음`으로 적는다.

## 5. 등록 절차

1. **중복 확인** — `gh issue list --state all --search "<키워드> in:title" --limit 10`. 같은 일이 있으면 알리고 멈춘다.
2. **본문 파일 저장** — 파일 편집 도구로 저장소 밖 임시 파일(운영체제 임시 디렉터리)에 쓴다(셸 문자열은 한글 · 백틱 · `$` 가 깨진다).
3. **게시 전 검사** — 아래 명령에서 기능은 `issue-feature`, 버그는 `issue-bug`를 쓴다. 실패하면 본문을 고쳐 재검사한다.

   ```bash
   python -X utf8 scripts/check_github_template.py issue-feature <본문 파일>
   ```

4. **등록**

   ```bash
   gh issue create --title "[BE] feat: ..." --body-file <본문 파일> \
     --assignee shindonggeun --label backend --label enhancement
   ```

5. 여러 건이면 선후 관계 순서대로 등록하고 뒤 이슈의 `참고` 에 앞 번호를 적는다.
6. 실패(권한, 없는 라벨)는 숨기지 않고 보고한다.

## 6. 출력

```text
| # | 제목 | 라벨 | 브랜치 제안 |
|---|------|------|-------------|
| 2 | [BE] feat: 세션 쿠키 로그인 · 로그아웃 API 를 둔다 | backend, enhancement, security | feature/be/2-session-login |
```

브랜치 제안은 `<type>/<영역>/<번호>-<영문 kebab 2~4단어>` (`feat` → `feature`, `[DOCS]` → `common`). 바로 시작하려면 `$issue-start <번호>`.

## Rules

- 한국어로 쓴다.
- 토스 키 · 계좌번호 · 보유 수량 · 비밀번호 · 서버 `.env` 값을 본문에 쓰지 않는다(`docs/git-workflow.md` §4-2).
