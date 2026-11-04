# 인수인계 (2026-11-04 18:00 기준)

다음 세션(집 PC)에서 이 파일부터 읽는다. 끝난 일 · 남은 일 · 바로 쓸 명령만 적는다.

## 1. 지금 돌아가는 것

| 항목 | 상태 |
|------|------|
| 사이트 | `https://trading.8llow8llowme.com` — 이메일 로그인(계정 1개, CLI 로 만듦) |
| 서버 | backend-server `192.168.0.9` 컨테이너 `trading-desk-web-prod`(:10088) · `trading-desk-backend-prod` |
| 배포 커밋 | `3111e1b` (로컬 스택 맨 위, `scripts/deploy.sh` 로 배포) |
| DB | main-server `.11` PostgreSQL `trading_desk` / 스키마 `desk`, Alembic `0002` |
| LLM | ChatGPT Pro 플랜 로그인 완료, deep `gpt-6-sol` · quick `gpt-6-luna`, FAKE 모드 꺼짐 |
| 브로커 | **mock** (토스 키 없음) — 종목 검색도 샘플 18종 |
| 인그레스 | storage `.12` nginx conf · 인증서(만료 2027-02-02) · fail2ban `nginx-trading-login` 적용 |

실제 분석 1회 확인(NVDA, 기술 분석가만, 205초): 에이전트 순서 · 한글 출력 · 계획 파싱 정상.

## 2. 이 세션에서 한 것 (이슈 → 브랜치)

스택 PR 이다. **앞에서부터 순서대로 머지**한다(`gh pr merge <번호> --rebase`).

| 이슈 | 브랜치 | PR | 내용 |
|------|--------|----|------|
| #1 | chore/infra/1-claude-workflow | #7 | 스킬 · 에이전트 · Git 규칙 · CI · CLAUDE.md |
| #2 | feature/be/2-postgres-store | #8 | SQLAlchemy · Alembic · PostgreSQL |
| #3 | feature/be/3-session-login | #9 | 로그인 · 세션 쿠키(보안 검토 반영) |
| #4 | feature/fe/4-login-screen | #10 | 로그인 화면 · 이메일 표시 |
| #5 | feature/infra/5-backend-server-deploy | #11 | 앱별 Dockerfile · compose · 배포 스크립트 · 문서 |
| #12 | feature/be/12-instrument-search | (이번에 생성) | 토스 종목 목록 한글 검색 API |
| #14 | feature/be/14-investor-profile | (이번에 생성) | 레버리지 공격형 지침 · ATR 타점 채우기 |
| #13 | feature/fe/13-instrument-search | (이번에 생성) | 종목 검색 자동완성 |
| #15 | feature/fe/15-korean-terms | (이번에 생성) | 등급 · 행동 한글 풀이 · 투자 성향 선택 · 이 문서 |

Infra 저장소: 이슈 #68 · PR #69 (nginx · 인증서 · fail2ban · README). 호스트에는 이미 적용됨.

### ⚠️ 먼저 할 일 — #5 브랜치 강제 푸시

#4 에 커밋을 더하면서 #5 이후를 로컬에서 리베이스했다. 강제 푸시가 권한 정책에 막혀 원격 #5 는 **옛 커밋**이다.
그 위의 #12~#15 PR 이 깔끔하게 보이려면 먼저:

```bash
git -C D:/ProjectWorkSpace/trading-desk push --force-with-lease origin feature/infra/5-backend-server-deploy
```

## 3. 남은 일

| 이슈 | 내용 | 메모 |
|------|------|------|
| #6 | OTP(TOTP) 2단계 인증 | users 에 비밀 · 복구코드 컬럼(Alembic 0003) |
| #16 | 분석 기록 3개월 보관 · 자동 삭제 | 지금은 무기한. events 는 FK CASCADE 라 runs 만 지우면 된다 |
| #17 | 매매 복기 — 토스 체결 내역 vs 분석 타점 | 토스 `GET /api/v1/orders`(읽기). 토스 키 · IP 등록 먼저 |
| #18 | 가격 차트 + 진입 · 손절 · 목표선 | yfinance 일봉 → lightweight-charts |
| — | 토스 키 연결 | 서버 `.env.runtime` 에 `DESK_BROKER=toss` · `TOSS_CLIENT_ID` · `TOSS_CLIENT_SECRET` → `scripts/deploy.sh HEAD backend`. 토스 WTS 허용 IP 에 `1.241.243.70` 등록. 그러면 검색이 국장 · 미장 전 종목이 된다 |
| — | Infra PR #69 머지 후 정리 | storage 에서 미리 올린 3파일(`nginx/conf.d/trading.8llow8llowme.conf`, `fail2ban/filter.d/nginx-trading-login.conf`, 수정된 `fail2ban/jail.local`)을 지우고 `git pull` |
| — | 실제 분석 재확인 | 애널리스트 4명 + 공격형으로 한 번 — 진입 · 손절 · 목표가 숫자로 나오는지, 비면 "계산값" 표시 |

## 4. 확인된 사실 (질문에 대한 답)

- **진입 · 손절 · 목표가가 비던 이유**: 토스 연결과 무관. TradingAgents 가 타점을 "확신할 때만" 쓰는 선택 항목으로 둔다. #14 로 지침에서 항상 요구하고, 그래도 비면 ATR(14)로 채워 "계산값" 으로 표시한다. 가격 데이터는 yfinance(야후).
- **국장**: 된다. 토스 목록의 시장으로 `.KS`(코스피) · `.KQ`(코스닥)을 붙여 야후로 분석한다. 다만 뉴스 · 재무 소스는 영어권 위주라 국내 종목은 기술적 분석 비중이 커진다.
- **국제 정세**: 뉴스 분석가가 `get_global_news`(거시 · 세계 뉴스), `get_macro_indicators`(FRED: CPI · 금리 · 실업률 · 장단기 금리차), `get_prediction_markets`(지정학 · 금리 인하 확률 등)를 쓴다. 심리 분석가는 StockTwits · Reddit.
- **심리 · 뉴스 · 재무 분석가가 활동하지 않았던 것**: 시험 분석에서 사용량을 아끼려고 기술 분석가 1명만 골랐기 때문이다. 화면 기본값은 4명 전부다.
- **분석 기록 보관**: 지금은 무기한 → #16 에서 90일.
- **차트**: 가능 → #18.

## 5. 자주 쓰는 명령

```bash
# 배포 (커밋된 HEAD 를 서버에서 빌드)
scripts/deploy.sh                 # 전체
scripts/deploy.sh HEAD backend    # 백엔드만

# 계정 (서버)
ssh -t homelab-backend-server docker exec -it trading-desk-backend-prod python -m desk.cli set-password <이메일>

# 로그 · 상태
ssh homelab-backend-server 'docker logs --tail 50 trading-desk-backend-prod; docker ps --filter name=trading-desk'

# ChatGPT 재로그인 (토큰이 만료 · 폐기됐을 때) — docs/deploy.md 5절.
# 서버에서 1회용 컨테이너를 --network host 로 띄워야 한다(로그인 서버가 컨테이너 127.0.0.1 에만 붙음).
```

## 6. 작업 방식

- 이슈 → 브랜치 → PR(base develop) → Rebase and merge (`docs/git-workflow.md`). 스킬은 trading-desk 폴더에서 Claude Code 를 열어야 보인다(`/dev-orchestrator` · `/issue` · `/issue-start` · `/pr`).
- PR 머지와 storage(`.12`) 쓰기 · 강제 푸시는 권한 정책상 사람이 하거나 허용 규칙을 추가해야 한다.
