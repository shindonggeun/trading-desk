# 인수인계 (2026-11-06 기준)

다음 세션에서 이 파일부터 읽는다. 끝난 일 · 남은 일 · 바로 쓸 명령만 적는다.

## 0. 진행 중 — #146 시장 투매 · 위험 점수 (2026-11-06, 브랜치 푸시됨 · PR 없음)

- 브랜치 `feature/be/146-risk-regime`, 워크트리 `D:\ProjectWorkSpace\trading-desk-wt\146-risk-regime` (origin/develop `6c87266` 기준)
  - 워크트리에는 venv 가 없다 — `D:/ProjectWorkSpace/trading-desk/backend/.venv/Scripts/python` 을 워크트리 `backend` 에서 실행하면 워크트리의 `desk` 가 잡힌다
  - 다른 PC 에서는 `git fetch && git switch feature/be/146-risk-regime`
- 내용
  - (새) `desk/risk_regime.py`: 야후 일봉(^VIX · ^VIX9D · ^VIX3M · ^NDX · ^SOX · DX-Y.NYB · ^TNX, 한국 종목은 ^KS11 · KRW=X 추가)으로 투매 점수 0~100,
    국면 `selling`(점수 ≥ 55) · `exhausting`(최근 10일 고점 ≥ 50 + 끝물 신호 2개 이상: VIX 꺾임 · 기간 구조 역전 해소 · 갭 하락 회복 · 연속 하락 뒤 반등) · `calm`.
    지표가 빠져도 남은 것으로 계산(`missing` 에 표시)
  - `apply(pb, risk)`: selling → 하따(dip_catch) 사다리 1차만 남기고 2·3차는 `gate.held` 로 보류(평단 · rr · then_stop 재계산), exhausting → 숏을 `none` 으로. `pb.risk_regime` 에 점수 · 근거
  - `runner._playbooks` 뒤에 적용 + 에이전트 노트(`risk_regime.prompt_note`), `api.create_app` 이 앱 PriceSource 의 야후로 `Cache`(15분) 생성, fake_runs 면 끔
  - `playbook_live`: 분석 없이 볼 때만 게이트 적용(분석의 플레이북은 이미 게이트됨)
  - `backtest`: `MarketRisk`(날짜별 점수 메모, 한국 계획은 같은 날 미국 종가를 안 봄), `Report.by_risk` · `risk_gate`(규칙 on/off 비교), CLI `--no-risk`
- 검증: `ruff` OK · `pytest` 560 passed / 1 failed — `test_related.py::test_a_run_hands_the_related_brief...` 는 develop 에서도 단독 실패하는 불안정 테스트(이 브랜치에서 단독 실행은 통과)
- 실데이터 백테스트(`backtest --basket --horizon swing --from 2019-06-01`, 비용 차감 기대 R, on / off)
  - 하따 2020-03 −0.040 / −0.084 · 2025-04 −0.363 / −0.408 · 2022 −0.184 / −0.185, 검증 구간 전체 −0.196 / −0.198, 최대 연속 손실 37 / 37
  - 숏 규칙(끝물에 숏 금지)은 약 190건을 빼도 숏 기대 R 이 그대로(−0.267 / −0.266) — 효과 없음, 유지할지 결정 필요
  - exhausting 국면의 하따는 +0.078R(calm −0.103R) — 끝물 신호 자체는 롱에 유의미해 보임
  - Windows 에서 출력을 파일로 돌리면 note 의 `—` 가 cp949 로 깨져 `--out` 이 안 써질 수 있음 → `PYTHONIOENCODING=utf-8` 로 실행
- **남은 일**
  1. 리뷰: `reviewer`(최종 diff `git diff origin/develop...`) 돌리고 CRITICAL/HIGH 만 반영 → 다시 `ruff` · `pytest`
  2. 숏 게이트 유지/삭제 또는 임계값(SELLING · STRESS · EXHAUST_SIGNALS) 조정 결정 — 백테스트로 확인
  3. 이슈 완료 기준의 "사다리 평균 진입가" 비교는 리포트에 없음(기대 R 만) — 필요하면 `backtest.gate_view` 에 추가
  4. PR: `/pr 검증: ruff OK · pytest 560 passed(불안정 1 제외) · 백테스트 하따 폭락 구간 기대 R 개선 · reviewer ...`
- 겹치는 작업: #166(`trading-desk-wt/166-direction`) · #149(b9 세션)도 `backtest.py` · `runner.py` · `playbook_live.py` 를 고친다. 덩어리가 달라 늦게 머지되는 쪽이 리베이스

## 1. 지금 돌아가는 것

| 항목 | 상태 |
|------|------|
| 사이트 | `https://trading.8llow8llowme.com` — 이메일 로그인(계정 1개, CLI 로 만듦) |
| 서버 | backend-server `192.168.0.9` 컨테이너 `trading-desk-web-prod`(:10088) · `trading-desk-backend-prod` |
| 배포 커밋 | 서버 `~/deploy/trading-desk/src/REVISION` 으로 확인(배포 기준 브랜치는 develop — #39) |
| DB | main-server `.11` PostgreSQL `trading_desk` / 스키마 `desk` |
| LLM | ChatGPT Pro 플랜, deep `gpt-6-sol` · quick `gpt-6-luna`, FAKE 모드 꺼짐 |
| 브로커 | **토스 연결됨** — 서버 `.env.runtime` 에 `DESK_BROKER=toss` · 키 반영, 허용 IP `1.241.243.70` 등록. 보유 종목 · 종목 목록(15,040종) · 현재가 · 1분/일봉 실호출 확인 |
| 인그레스 | storage `.12` nginx conf · 인증서(만료 2027-02-02) · fail2ban `nginx-trading-login` |

토스 메모: `/api/v1/stocks/all` 은 초당 1회(429 + `Retry-After`) — `TossBroker._get` 이 기다렸다 재시도한다.
토스 봉은 **주봉 없음**(`1w` 400), 한 번에 **최대 200개**.

## 2. 진행 중 — #148 이벤트 직전 진입 보류 (브랜치 푸시됨, PR 아직 없음)

- 브랜치 `feature/be/148-event-hold`, 워크트리 `D:/ProjectWorkSpace/trading-desk-wt/148-event-hold`(집에서는 `git fetch && git switch feature/be/148-event-hold` 또는 새 워크트리)
- 배경: #148 은 닫혀 있지만 "주요 이벤트 직전 플레이북 신규 진입 보류"는 프롬프트 문구로만 들어가 있었다 → `playbook.py` 에 실제로 연결
- 내용(`backend/desk/playbook.py` · `backend/tests/test_playbook.py` 만)
  - `build(..., live=...)` 가 live 일 때만(= 오늘 분석 · 실시간 플레이북, 백테스트 · 지난 날짜는 아님) `events_calendar.CACHE.blackout()` 을 본다
  - 새 `hold_for_event(pb, event)`: 사다리 2 · 3차에 `held: "event"`, 각 방향에 미충족 조건 `event_clear`, 점수 재계산, `pb["event_hold"]` = 이벤트 + `until`
  - `track()` 이 지금 일정으로 다시 적용 — 창이 지나면 표시 제거. `prompt_note` 에 "Event window" 한 줄
- 검증: `ruff` OK · `pytest` 560 passed(새 테스트 2개)
- **남은 일**
  1. `reviewer` 검토 — 회사에서 띄운 검토는 결과를 받기 전에 퇴근. 집에서 다시 돌린다. 볼 점: `track()` 이 `run_pb` 얕은 복사라 `hold_for_event` 가 원본을 건드리는지, 미국 지표 일정이 **국내 종목에도** 보류를 거는 게 맞는지, `prompt_note` 1200자 제한에 줄이 밀리는지, 프론트 타입(`frontend/src/types.ts`)과 키 추가만인지
  2. CRITICAL/HIGH 반영 → `pytest` · `ruff` → `/pr 검증: ruff OK · pytest 560 passed · reviewer ...` (Closes 대신 `Refs #148`, 닫힌 이슈)
  3. 화면 표시(보류 배지 · 경제 일정)는 #150(FE)에서

## 3. 이번 세션(11-06)에서 끝난 것

| 이슈 | PR | 내용 |
|------|----|------|
| #134 · #135 | — | 즐겨찾기(서버 저장) · 대시보드 즐겨찾기 시세 |
| #148 | #159 | 경제 일정(연준 · BLS) · SEC 공시 · GDELT 쉬기 |
| #143 | — | KIS 실시간 연결 상태 API(운영 확인은 남음, 이슈 열림) |
| #154 · #155 | — | 공매도 · 옵션 포지션, 빚투 · 예탁금 API |
| #156 · #157 | #175 | 섹터 그룹 API · 섹터 탭 |
| #158 | #177 | 분석 · 차트 포지션 카드 · 옵션 벽 선 · 대시보드 빚투 구역 |

## 4. 남은 일 · 다른 세션

| 이슈 | 내용 | 메모 |
|------|------|------|
| #148 잔여 | 위 2절 | 리뷰 → PR |
| #146 | 투매 · 위험 점수 | 워크트리 `146-risk-regime`(다른 세션, runner · playbook_live · api 수정 중) |
| #149 | 5분봉 진입 트리거 | b9 세션(trigger.py · playbook_live · backtest) |
| #171 | 분석 속도 | 14 세션(timing.py) |
| #145 · #147 · #150 · #167 | 타점 후보 확장 · 확률 보정 · 플레이북 카드(FE) · 하락장 가치 구간 | 아직 맡은 세션 없음 — 겹침 확인 후 |
| #143 | 미국 장중 `last_tick.US` 운영 확인 | |
| #160 | 코스피200 선물 현황 | |

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
