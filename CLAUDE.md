# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 무엇인가

TradingAgents(LangGraph 멀티 에이전트)의 분석 토론을 **채팅처럼 실시간으로 보여주고**, 최종 계획(등급 · bias · 진입/손절/목표가)을
대시보드에 쌓는 **1인용** 웹 앱이다. 운영은 `https://trading.8llow8llowme.com` (홈랩 `backend-server` `192.168.0.9`).

- TradingAgents 는 **라이브러리**로 쓴다. `backend/pyproject.toml` 에 포크 `shindonggeun/TradingAgents` 의 **커밋 SHA 로 고정**.
  포크(`D:\ProjectWorkSpace\TradingAgents`)는 업스트림 추적용이라 고치지 않는다. 패치가 필요하면 포크 브랜치 → 여기 SHA 만 올린다.
- 주문 기능은 **일부러 없다**(`broker/base.py` 에 주문 메서드 없음). 붙이기 전 조건은 README "실행(주문)" 절.

## 명령

```bash
# backend (Windows venv 경로; Linux 는 .venv/bin)
cd backend
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"
.venv/Scripts/python -m ruff check desk tests
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m pytest tests/test_plan.py::test_levels_parsed_and_missing_ones_are_none -q  # 테스트 하나
.venv/Scripts/uvicorn desk.api:create_app --factory --reload --port 8000           # 앱은 팩토리로만 만든다
.venv/Scripts/python -m desk.cli create-user <아이디>                                  # 로그인 계정 (가입 API 없음)

# frontend
cd frontend
npm install
npm run dev      # :5173, /api 는 127.0.0.1:8000 으로 프록시
npm run build    # tsc --noEmit + vite build — 타입 검사는 이것뿐

# 배포 (backend-server, docs/deploy.md) — 서버에서 컨테이너 2개를 빌드 · 기동
scripts/deploy.sh                       # HEAD. 백엔드만: scripts/deploy.sh HEAD backend
docker compose -f docker-compose-local.yml up -d --wait   # PC 에서 PostgreSQL 경로를 시험할 때만
```

UI 작업은 `.env` 에 `DESK_FAKE_RUNS=true` — 대본 러너가 **같은 `StateDiffer`** 를 거쳐 이벤트를 내므로 LLM 쿼터 없이 화면을 끝까지 볼 수 있다.

## 아키텍처 (여러 파일을 읽어야 보이는 것)

**실행 흐름**: `POST /api/runs` → `RunManager.submit()` 이 run 행과 `queued` 이벤트를 쓰고 asyncio task 를 만든다 →
세마포어(`DESK_MAX_CONCURRENT_RUNS`, 기본 1 — 모든 run 이 같은 ChatGPT 플랜을 쓴다) → 러너를 `asyncio.to_thread` 워커에서 실행 →
러너의 `emit()` 은 `loop.call_soon_threadsafe` 로 **이벤트 루프 스레드에서** `store.append_event` + `hub.notify` 한다 →
끝나면 `plan.parse_plan(final_state)` → `plan` 이벤트 → `done`. 실패면 `error` → `done`.

**채팅 이벤트 = 상태 diff** (`events.StateDiffer`): `TradingAgentsGraph.stream_run()` 이 내는 `(messages, state)` 에서
에이전트별 상태 필드(`market_report`, `investment_debate_state.bull_history`, `final_trade_decision` …)를 이전과 비교한다.
새 필드 = 그 에이전트의 발언, 늘어난 토론 기록 = 새 발언 차례. LangGraph 노드 이름에 의존하지 않는 것이 의도다.
TradingAgents SHA 를 올리면 `_SLOTS` 의 필드 이름부터 확인한다.

**계획 파싱** (`plan.py`): TradingAgents 는 구조화 출력을 **고정 영어 라벨** 마크다운(`**Entry Price**: 182.5`)으로 렌더링한다.
출력 언어가 한국어여도 라벨은 영어라 정규식으로 읽는다. 가격은 전부 선택값. `bias` 는 이 앱의 해석(Buy/Overweight→long, Hold→flat, Underweight/Sell→short).

**SSE 는 DB 에서 재생한다** (`/api/runs/{id}/stream`): 이벤트 테이블이 정본이고 `Last-Event-ID`(= `seq`)부터 다시 읽는다.
새로고침 · 재접속해도 대화가 남는 이유다. `Hub.listen()` 을 **DB 읽기 전에** 잡아야 그 사이 이벤트를 놓치지 않는다.
앞단 프록시는 `proxy_buffering off` 와 긴 `proxy_read_timeout` 이 필요하다(`frontend/nginx.conf.template`, 홈랩 인그레스 conf).

**컨테이너** (commerce-foundry 와 같은 형식): 앱마다 `<app>.Dockerfile` + `.dockerignore` + `docker-compose-<app>.yml`,
`-p` 프로젝트 이름 고정, 필수 env 는 `${KEY:?}`, 외부 네트워크 `DESK_DOCKER_NETWORK` 로 웹 → 백엔드(컨테이너 이름). 백엔드는 호스트 포트를 열지 않는다.
compose `environment` 에는 앱이 읽는 키만 나열한다 — 새 env 키는 compose · `.env.example` 을 같이 고친다.

**인증**: `/api/health` · `/api/auth/login` 을 뺀 **모든 경로**가 세션 쿠키를 요구한다(`api.require_session`, 기본 닫힘).
계정은 CLI(`python -m desk.cli create-user`)로만 만든다. 규칙 정본은 [docs/security.md](docs/security.md) — 인증을 건드리면 `security-reviewer` 를 부른다.

**DB**: 스키마는 `db.py`(SQLAlchemy Core), 변경은 Alembic 리비전(`desk/migrations/versions/`)으로, 기동 시 `upgrade head` 자동.
로컬은 SQLite, 서버는 홈랩 PostgreSQL(스키마 `desk`). `tests/test_db.py` 가 리비전과 `db.py` 의 어긋남을 잡는다.
테스트는 `db_url` fixture 를 쓴다 — `DESK_TEST_DATABASE_URL` 이 있으면 PostgreSQL 로 돈다(CI).

**재시작**: 기동 시 `queued`/`running` run 은 `failed` 로 바꾼다 — 워커 스레드는 프로세스와 함께 죽는다.

**외부 의존**:
- LLM 공급자 · 모델은 `TRADINGAGENTS_*` 환경 변수를 라이브러리가 직접 읽는다. 기본은 ChatGPT 구독(`chatgpt`, OAuth 로그인 파일 `~/.tradingagents/chatgpt-auth.json` — compose 의 `tradingagents_home` 볼륨).
- 브로커(`broker/`)는 읽기 전용 포트. `toss` 는 토스증권 Open API(client_credentials, 숫자가 전부 문자열, **등록된 IP 에서만 호출 가능** — 공인 IP 가 바뀌면 403).
- `regime.py` 는 별도 프로젝트 semibot(`D:\ProjectWorkSpace\stock`)의 SQLite 를 `mode=ro` 로 읽는다. 경로가 없으면 패널이 빈다.

## 작업 규칙

- Git 흐름 정본: [docs/git-workflow.md](docs/git-workflow.md) — `이슈(/issue) → 브랜치(/issue-start) → PR(/pr, base develop) → Rebase and merge`.
  커밋 `[BE|FE|INFRA|DOCS] type: 요약 (#번호)`, 본문은 한국어 변경 목록. 경로를 하나씩 스테이징한다(`git add -A` · `git stash` 금지).
- 스킬 · 에이전트: [docs/claude-agents.md](docs/claude-agents.md). 비단순 작업은 `/dev-orchestrator` 로 분류한다 — 인증 · 세션 · 브로커 키 · 스키마 · 배포가 T3 신호다.
- 저장소는 private 이지만 토스 키 · 계좌 정보 · 비밀번호 · 세션 토큰 · 서버 `.env` 값은 어디에도 쓰지 않는다. 키 이름만 `.env.example` 에.
- 모든 파일은 UTF-8(no BOM), LF (`.editorconfig`, `.gitattributes`).
- 홈랩 인프라(nginx 인그레스 · 인증서 · PostgreSQL · fail2ban)는 이 저장소가 아니라 `D:\ProjectWorkSpace\Infra`(**공개** 저장소, 작업 트리를 다른 세션과 공유 — 워크트리로 작업).
