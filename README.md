# Trading Desk

TradingAgents의 멀티 에이전트 토론을 **채팅처럼 실시간으로 보고**, 결과(등급 · 진입가 · 손절가 · 목표가)를 **대시보드에 쌓는** 웹 앱.

```
┌───────────── 느린 층 (이 프로젝트) ─────────────┐   ┌──── 빠른 층 (semibot) ────┐
│ TradingAgents  하루 1~3회                        │   │ 토스 API 1분 폴링          │
│  애널리스트 → 강세/약세 토론 → 트레이더          │   │  레짐 × 트리거             │
│  → 리스크 토론 → PM  =  "오늘의 계획"            │   │  = "지금 테이프가 동의하나" │
└──────────────────────┬───────────────────────────┘   └────────────┬──────────────┘
                       └──────────── 대시보드에 나란히 ──────────────┘
                                         ↓
                          (나중에) 실행 엔진 → 토스 주문  ※ 아직 없음
```

## 왜 TradingAgents 포크가 아니라 별도 프로젝트인가

- **업스트림을 계속 받기 위해.** TradingAgents는 한두 주에 한 번 릴리스된다(v0.5 → v0.7.1, 두 달). 포크 안에 웹 · DB · 브로커 코드를 섞으면 매 릴리스가 충돌이다.
- **라이브러리 경계가 이미 있다.** `TradingAgentsGraph.stream_run()` · `create_run_state()` · `save_reports()` 를 CLI와 같은 순서로 부르면 된다. 포크를 고칠 일이 없다.
- **고칠 일이 생기면** 포크(`shindonggeun/TradingAgents`)에 브랜치로 고치고 `backend/pyproject.toml` 의 커밋 SHA만 올린다. 업스트림에 PR 보내기도 쉽다.
- **비밀이 섞이지 않는다.** 토스 키 · DB 비밀번호는 이 앱의 서버 `.env` 에만 있다.

TradingAgents 버전은 `backend/pyproject.toml` 에 **커밋 SHA로 고정**되어 있다. 올릴 때는 업스트림 CHANGELOG를 읽고 SHA를 바꾼다.

## 구조

```
backend/desk/
  api.py        FastAPI. /api/runs (생성·목록·상세), /stream (SSE), /portfolio, /regime
  runner.py     TradingAgents 실행(워커 스레드) · FAKE 러너 · RunManager(동시 실행 제한)
  events.py     stream_run 상태 → 채팅 이벤트 (어느 에이전트가 무엇을 말했나)
  plan.py       최종 상태 → 등급 · bias(long/short/flat) · 진입/손절/목표 · 손익비
  db.py         스키마(SQLAlchemy Core) · 엔진 · 기동 시 Alembic upgrade. 로컬은 SQLite, 서버는 PostgreSQL
  migrations/   Alembic 리비전 (스키마를 바꾸면 리비전 추가 — tests/test_db.py 가 어긋남을 잡는다)
  store.py      runs · events. SSE는 여기서 재생하므로 새로고침해도 대화가 남는다
  broker/       읽기 전용 브로커 포트. mock / toss(holdings)
  regime.py     semibot.db 읽기 전용 (레짐 · 트리거 패널)
frontend/src/
  pages/Dashboard.tsx   새 분석 · 분석 기록 표 · 보유 종목 · semibot 레짐
  pages/RunView.tsx     에이전트 레일 + 채팅 스트림 + 최종 계획 카드
```

### 채팅 이벤트는 어떻게 만드나

TradingAgents는 에이전트별 결과를 정해진 상태 필드에 쓴다(`market_report`, `investment_debate_state.bull_history`, `trader_investment_plan`, `final_trade_decision` …). `events.StateDiffer` 가 스트림의 상태를 이전과 비교해 **새로 생긴 필드 = 그 에이전트의 발언**, **늘어난 토론 기록 = 새 발언 차례**로 바꾼다. LangGraph 노드 이름에 의존하지 않아 업스트림 내부가 바뀌어도 덜 깨진다.

### 계획(plan)은 어떻게 뽑나

TradingAgents는 구조화 출력을 고정 영어 라벨의 마크다운으로 렌더링한다(`**Entry Price**: 182.5`). 출력 언어가 한국어여도 라벨은 그대로라 `plan.py` 가 이걸 읽는다. 모든 가격은 **선택값** — 모델이 확신 못 하면 비운다.

`bias` 는 등급을 읽는 이 앱의 해석이다: Buy/Overweight → `long`, Underweight/Sell → `short`(= 숏 또는 비중 축소), Hold → `flat`. 실제 숏이 가능한지는 계좌 · 상품이 정한다.

## 로컬 개발

```bash
cp .env.example .env            # 먼저 DESK_FAKE_RUNS=true 로 두고 UI부터

# backend
cd backend
python -m venv .venv && .venv/Scripts/activate      # Linux: source .venv/bin/activate
pip install -e ".[dev]"
pytest
uvicorn desk.api:create_app --factory --reload --port 8000

# frontend (다른 터미널)
cd frontend
npm install
npm run dev                     # http://localhost:5173 (/api 는 8000으로 프록시)
```

## ChatGPT 플랜으로 실제 실행

`.env` 에서 `DESK_FAKE_RUNS=false`, `TRADINGAGENTS_LLM_PROVIDER=chatgpt` 로 두고 한 번 로그인한다.

```bash
cd backend && tradingagents login chatgpt     # 브라우저로 로그인 → ~/.tradingagents/chatgpt-auth.json
```

모델 ID는 로그인 후 `tradingagents` CLI의 Subscription 메뉴에 나오는 목록에서 골라 `TRADINGAGENTS_QUICK_THINK_LLM` / `TRADINGAGENTS_DEEP_THINK_LLM` 에 넣는다.

서버(Docker)에서의 로그인은 [docs/deploy.md](docs/deploy.md) 5절.

## 배포 (홈랩)

`https://trading.8llow8llowme.com` — backend-server(`192.168.0.9`)에 컨테이너 둘(웹 · 백엔드), DB 는 홈랩 PostgreSQL.
절차 · 서버 env · 첫 계정 · ChatGPT 로그인 · 토스 IP 등록 · 백업은 [docs/deploy.md](docs/deploy.md).

```bash
scripts/deploy.sh          # HEAD 를 git archive 로 보내 서버에서 빌드 · 기동
```

- 모든 경로가 로그인(ID/비밀번호 + 서버 세션 쿠키) 뒤에 있다 — [docs/security.md](docs/security.md).
- SSE 때문에 앞단 프록시는 `proxy_buffering off`, 긴 `proxy_read_timeout` 이 필요하다(`frontend/nginx.conf.template`).

## 실행(주문) — 아직 없음, 붙이기 전 조건

`broker/base.py` 에는 일부러 주문 메서드가 없다. semibot README의 Phase 7 조건과 같다.

1. **백테스트 먼저.** `tradingagents backtest` 로 대상 종목에서 등급별 성과를 본다.
2. **모의 운영.** 계획을 알림으로만 받으며 기간을 둔다.
3. **주문 붙일 때 필수:** kill switch, 주문 idempotency 키, 일일 최대 주문 수 · 금액 하드캡, 사람 승인 단계(대시보드 버튼), 손절은 토스 조건주문(`/api/v1/conditional-orders`)으로 서버 측에 걸기.
4. LLM 출력은 **계획**이고, 진입 타이밍은 규칙 엔진(semibot 트리거)이 정한다.

## 다음 할 일

- [ ] 실제 ChatGPT 플랜으로 1회 실행해 이벤트 매핑 확인 (FAKE 러너는 같은 `StateDiffer` 를 거친다)
- [ ] 예약 실행 (장 전 08:30 / 장 후 15:40 / 미장 전 22:00)
- [ ] 런 페이지에 가격 차트 + 진입/손절/목표 라인
- [ ] 분석 후 리포트를 문맥으로 넣은 후속 질문 채팅
- [ ] 과거 판단의 실현 수익률(TradingAgents memory log) 대시보드
