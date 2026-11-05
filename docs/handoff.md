# 인수인계 (2026-11-05 기준)

다음 세션에서 이 파일부터 읽는다. 끝난 일 · 남은 일 · 바로 쓸 명령만 적는다.

## 1. 지금 돌아가는 것

| 항목 | 상태 |
|------|------|
| 사이트 | `https://trading.8llow8llowme.com` — 이메일 로그인(계정 1개, CLI 로 만듦) |
| 서버 | backend-server `192.168.0.9` 컨테이너 `trading-desk-web-prod`(:10088) · `trading-desk-backend-prod` |
| 배포 커밋 | `c118d90` (develop, 배포 기준 브랜치는 develop — #39) |
| DB | main-server `.11` PostgreSQL `trading_desk` / 스키마 `desk` |
| LLM | ChatGPT Pro 플랜, deep `gpt-6-sol` · quick `gpt-6-luna`, FAKE 모드 꺼짐 |
| 브로커 | **토스 연결됨** — 서버 `.env.runtime` 에 `DESK_BROKER=toss` · 키 반영, 허용 IP `1.241.243.70` 등록. 보유 종목 · 종목 목록(15,040종) · 현재가 · 1분/일봉 실호출 확인 |
| 인그레스 | storage `.12` nginx conf · 인증서(만료 2027-02-02) · fail2ban `nginx-trading-login` |

토스 메모: `/api/v1/stocks/all` 은 초당 1회(429 + `Retry-After`) — `TossBroker._get` 이 기다렸다 재시도한다.
토스 봉은 **주봉 없음**(`1w` 400), 한 번에 **최대 200개**.

## 2. 진행 중 — #32 (브랜치 푸시됨, PR 아직 없음)

- 브랜치 `feature/be/32-candles-levels`, 워크트리 `D:\ProjectWorkSpace\trading-desk-wt\32-candles-levels`, origin/develop(`c118d90`) 위로 rebase 됨
- 검증: `ruff` OK · `pytest` 91 passed (rebase 후). `reviewer` 검토 HIGH 1 · MEDIUM 2 · LOW 1(캐시) 반영 완료
- 내용
  - `desk/sr.py` — 일봉 · 주봉 스윙 고저점, 52주 고저, 지난주 고저 · 피벗, 20/50/200일선 → ATR 절반 안에서 구간으로 묶음.
    강도 = 근거 수(주봉 2배, 1년 반감), 현재가에서 min(6 ATR, 35%) 안만, 강도 ÷ 거리로 위 · 아래 4개
  - `GET /api/candles?ticker=&interval=1m|1d|1w&count=` · `GET /api/levels?ticker=` (세션 필요). 주봉 · 200개 초과는 야후
  - 분석 시작 때 거래일까지의 레벨을 계산(20초 제한)해 트레이더 · 리스크 · PM 지침에 참고로 넣고 계획에 `support_resistance` 저장
- 운영 실데이터 확인(배포 전, 계산만): NVDA 205.8 → 저항 208 · 214.5 / 지지 200.5 · 195.6, 삼성전자 221,500 → 지지 205,438 · 191,561
- **남은 일**
  1. PR 생성: `/pr 검증: ruff OK · pytest 91 passed · reviewer HIGH 1 반영(테스트가 실제 야후 호출 → autouse 차단)`
  2. 리뷰 LOW 미반영: 현재가와 같은 구간이 `resistance` 로만 분류됨(`at` 종류 검토)
  3. 머지 후 배포 → 실제 분석 1회로 에이전트가 지지 · 저항을 근거로 쓰는지 확인
- 참고: 초기 pytest 원인 미상 실패 2회는 위 HIGH(실제 야후 호출)로 추정 — 반영 후 재현 없음

## 3. 이번 세션에서 끝난 것

| 이슈 | PR | 내용 |
|------|----|------|
| #12 | #29 | 토스 종목 검색 + 429 재시도 |
| #14 · #13 · #15 · #16 · #23 | #20 · #21 · #22 · #28 · #30 | 투자 성향 · 검색 자동완성 · 한글 풀이 · 90일 보관 · 현재가 기준 타점(BE) |
| #31 | #38 | 손절가가 트레이더 초안으로 남던 버그 — PM 요약의 최종 진입 · 손절을 우선(SOXL 110.77 → 98.26) |

다른 세션이 같은 기간에 #27(바이낸스 추정가) · #34(분석 기록 삭제 · 페이지네이션) · #39 를 머지했다.

## 4. 남은 일

| 이슈 | 내용 | 메모 |
|------|------|------|
| #32 | 위 2절 | PR → 머지 → 배포 |
| #18 | [FE] 분석 화면 차트(lightweight-charts) — 일봉 · 주봉 전환, 거래량, 진입 · 손절 · 목표선 | **다음 작업**. #32 의 `/api/candles` 사용 |
| #25 | [FE] 차트에 지지 · 저항선 · 띠, 근거 툴팁 | #32 `/api/levels` · plan `support_resistance` |
| #26 | [FE] 실시간 차트(1분봉) | |
| #23 | [FE 잔여] 계획 카드에 현재가 · 진입가까지 거리 · 갱신 시각 | BE 는 #30 으로 끝 |
| #17 | 매매 복기(토스 체결 내역) | 토스 연결됨 — 바로 가능 |
| #24 · #35 · #36 · #37 · #6 | 용어 사전 · 원화/달러 환산 · 기록 페이지 · 총평가액 · OTP | #35~#37 은 다른 세션이 연 것 |
| — | 계획 카드: `levels_origin` 이 섞이면(pm/trader) 경고 표시, `trader_levels` 로 바뀐 값 표시 | #31 리뷰 MEDIUM, 프론트 |

## 5. 자주 쓰는 명령

```bash
# 배포 — 이 PC 의 ssh 별칭은 backend-server(LAN) / backend-server-ext(밖, 2219)
DESK_DEPLOY_HOST=backend-server scripts/deploy.sh                 # 전체
DESK_DEPLOY_HOST=backend-server scripts/deploy.sh HEAD backend    # 백엔드만

# 로그 · 상태
ssh backend-server 'docker logs --tail 50 trading-desk-backend-prod; docker ps --filter name=trading-desk; cat ~/deploy/trading-desk/src/REVISION'

# 컨테이너 안에서 파이썬으로 점검(DB) — stdin 으로 스크립트를 넘긴다.
# 토스는 여기서 부르지 않는다: TossBroker 가 토큰을 새로 받아 서버 토큰이 무효화된다(docs/deploy.md 6절, #50). 앱 API 로 확인
ssh backend-server 'docker exec -i trading-desk-backend-prod python -' < script.py
```

## 6. 작업 방식 메모

- 본 저장소 폴더(`D:\ProjectWorkSpace\trading-desk`)는 **다른 세션도 쓴다** — 브랜치가 바뀌어 있을 수 있으니 작업은 워크트리(`trading-desk-wt/<번호>-...`)에서.
- 스택 PR 을 base 브랜치로 rebase 머지하면 위 PR 이 SHA 어긋남으로 CONFLICTING 이 된다 → 새 base 위로 rebase 후 `git push --force-with-lease`(허용 규칙 있음). 가능하면 PR 은 develop 에 바로 쌓지 않고 하나씩 머지.
- `gh pr create/edit` · `--force-with-lease` 푸시는 `.claude/settings.local.json` 에 허용됨. `gh pr merge` 는 사용자가 한다.
