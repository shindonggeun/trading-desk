# 배포 — backend-server · trading.8llow8llowme.com

## 1. 배치

```
인터넷 ──443──▶ storage(192.168.0.12)  nginx 인그레스 + certbot + fail2ban      ← Infra 저장소 nginx/conf.d/trading.8llow8llowme.conf
                     │ X-Real-IP 를 붙여 192.168.0.9:10088 로
                     ▼
              backend-server(192.168.0.9, x86_64)
                ├─ trading-desk-web-prod       nginx + React 빌드   192.168.0.9:10088 (사설 IP 에만 바인드)
                │      │ docker 네트워크 trading-desk-prod
                │      ▼
                └─ trading-desk-backend-prod   FastAPI + TradingAgents   호스트 포트 없음
                       │ 볼륨 trading-desk-tradingagents-home (ChatGPT 로그인 · 리포트 · 캐시)
                       ▼
              main-server(192.168.0.11:5432)  PostgreSQL — DB trading_desk · 계정 trading_desk · 스키마 desk
```

| 파일 | 내용 |
|------|------|
| `backend/backend.Dockerfile` (+ `.dockerignore`) | 멀티 스테이지. 의존성 층과 소스 층을 갈라 `desk/` 만 바뀌면 TradingAgents 를 다시 설치하지 않는다. 비루트 `app` |
| `backend/docker-compose-backend.yml` | `backend-prod`. 앱이 읽는 키만 `environment` 로 넘기고 필수 키는 `${KEY:?}` 로 막는다 |
| `frontend/frontend.Dockerfile` (+ `.dockerignore`) | 멀티 스테이지. Vite 빌드 → nginx. `nginx.conf.template` 의 `${BACKEND_UPSTREAM}` 을 기동 시 치환 |
| `frontend/docker-compose-frontend.yml` | `web-prod`. 외부에 여는 유일한 컨테이너 |
| `docker-compose-local.yml` | PC 에서 PostgreSQL 경로를 시험할 때만 쓰는 DB |
| `scripts/deploy.sh` | 커밋 하나를 `git archive` 로 보내 서버에서 빌드 · 기동 |

compose 프로젝트 이름은 `trading-desk-backend-prod` · `trading-desk-web-prod` 로 고정한다(`-p`). 빠지면 디렉터리 이름이 프로젝트가 돼
`--remove-orphans` 가 남의 컨테이너를 지울 수 있다. 이미지는 `trading-desk-backend:prod` · `trading-desk-web:prod`.

## 2. 배포

```bash
scripts/deploy.sh                  # HEAD 를 올린다 (커밋 안 된 변경이 있으면 멈춘다)
scripts/deploy.sh <커밋|태그>
scripts/deploy.sh HEAD backend     # 백엔드만 (web | backend | all)
scripts/deploy.sh --dry-run <커밋> backend   # 진행 중인 분석만 확인하고 배포하지 않는다
scripts/deploy.sh --force <커밋> backend     # 진행 중인 분석이 있어도 바로 올린다 (그 분석은 실패로 끝난다)
```

**진행 중인 분석을 기다린다**(#431). 백엔드를 다시 띄우면 진행 중인 분석이 끊긴다("서버가 다시 시작돼 분석이 중단됐습니다").
그래서 `backend` · `all` 배포는 먼저 서버의 백엔드 컨테이너 안에서 `python -m desk.cli unfinished` 로
queued/running run 을 묻는다(SSH 와 `docker exec` 만 쓰고 세션 · 비밀값은 쓰지 않는다).

- 없으면 그대로 진행한다.
- 있으면 종목 · 상태를 보이고 30초마다 다시 묻는다. `DESK_DEPLOY_WAIT_MIN` 분(기본 30)이 지나도 남아 있으면 종료 코드 3 으로 멈춘다 — 끊어도 되면 `--force`.
- 새 백엔드 이미지를 빌드한 뒤, 컨테이너를 바꾸기 직전에 서버에서 한 번 더 묻는다. 빌드하는 몇 분 사이에 제출된 분석도 같은 규칙으로 기다린다. 거기서 멈추면 `src/` 를 직전 배포로 되돌린다.
- SSH 로 묻지 못하면(접속 실패) 배포하지 않고 종료 코드 3 으로 멈춘다.
- `--dry-run` 은 확인만 한다. 진행 중인 분석이 있으면 종료 코드 3, 없으면 0.
- `web` 만 올릴 때는 묻지 않는다. 컨테이너가 없거나 이 명령이 없는 옛 이미지면 확인할 수 없다고 알리고 진행한다.

끊긴 분석은 실패 화면의 "다시 분석" 버튼으로 같은 종목 · 같은 설정의 새 run 을 낼 수 있다.

서버 `~/deploy/trading-desk/` 구조:

```
.env.runtime   비밀 · 설정 (권한 600) — 저장소에 없다
src/           방금 배포한 소스 (REVISION 에 커밋)
src.old/       직전 배포 — 되돌릴 때: src.old 로 들어가 같은 compose 명령
```

**`develop` 이 배포본이다**(`docs/git-workflow.md` §8 — `main` 은 쓰지 않는다). 순서:

1. `git fetch origin` 후 올릴 커밋 = `origin/develop` 최신. 그 커밋의 CI 가 통과했는지 본다 — `gh run list --branch develop --limit 1`
2. 지금 서버 커밋(`REVISION`)과 비교해 바뀐 쪽만 올린다 — `git diff --stat <서버 커밋> origin/develop`
3. 커밋을 지정해 올린다. 작업 트리가 다른 브랜치에 있어도 지정한 커밋만 나간다

```bash
scripts/deploy.sh "$(git rev-parse --short origin/develop)" backend   # backend/ 만 바뀌었을 때 (web | all)
```

배포 뒤 확인:

```bash
ssh homelab-backend-server 'docker ps --filter name=trading-desk; cat ~/deploy/trading-desk/src/REVISION'
curl -s https://trading.8llow8llowme.com/api/health        # {"ok":true}
```

## 3. 서버 env (`~/deploy/trading-desk/.env.runtime`)

키 이름은 루트 `.env.example` 과 같다. 처음 만들 때만 손으로 쓴다(권한 600). 필수:

| 키 | 값 |
|----|-----|
| `DESK_DATABASE_URL` | `postgresql+psycopg://trading_desk:<비밀번호>@192.168.0.11:5432/trading_desk?options=-csearch_path%3Ddesk` |
| `DESK_DOCKER_NETWORK` | `trading-desk-prod` |
| `DESK_WEB_BIND` · `DESK_WEB_PORT` | `192.168.0.9` · `10088` |

DB · 계정은 Infra 저장소의 `PostgreSQL/scripts/create-project-db.sh trading_desk trading_desk desk` 로 만들었다(2026-11-04).
비밀번호를 바꿀 때는 같은 스크립트를 새 값으로 다시 돌리고 `.env.runtime` 을 고친 뒤 `scripts/deploy.sh HEAD backend`.

## 4. 처음 한 번 — 로그인 계정

가입 API 는 없다. 서버에서 직접 만든다(비밀번호를 두 번 묻는다, 12자 이상).

```bash
ssh -t homelab-backend-server docker exec -it trading-desk-backend-prod python -m desk.cli create-user <이메일>
```

비밀번호 변경 · 전체 로그아웃은 같은 방식으로 `set-password` · `revoke-sessions` (`docs/security.md` §1).

## 5. 처음 한 번 — ChatGPT 로그인

`.env.runtime` 의 `DESK_FAKE_RUNS=true` 인 동안은 대본 러너가 돈다. 실제 분석은 ChatGPT 구독으로 로그인한 뒤 켠다.
로그인 콜백은 컨테이너의 `127.0.0.1:1455` 로 돌아오므로 PC → 서버 → 컨테이너로 터널을 잇는다.

```bash
# PC 터미널 1: PC 의 1455 → 서버의 1455
ssh -L 1455:127.0.0.1:1455 homelab-backend-server
# 그 SSH 세션 안: 같은 볼륨으로 1회용 컨테이너를 띄워 로그인 (서버 루프백 1455 만 연다)
docker run --rm -it -p 127.0.0.1:1455:1455 -v trading-desk-tradingagents-home:/home/app/.tradingagents \
  trading-desk-backend:prod tradingagents login chatgpt
```

브라우저가 열라는 주소를 PC 브라우저에서 연다. 끝나면 `chatgpt-auth.json` 이 볼륨에 남는다.
그다음 `.env.runtime` 에서 `DESK_FAKE_RUNS=false`, 모델 ID(`TRADINGAGENTS_QUICK_THINK_LLM` · `TRADINGAGENTS_DEEP_THINK_LLM`)를 채우고 `scripts/deploy.sh HEAD backend`.
속도 옵션 env 4개(`TRADINGAGENTS_DEBATE_PARALLEL_FIRST_ROUND` · `TRADINGAGENTS_RISK_PARALLEL_FIRST_ROUND` · `TRADINGAGENTS_QUICK_THINK_REASONING_EFFORT` · `TRADINGAGENTS_DEEP_THINK_REASONING_EFFORT`)는 비워 두면, 보고서 재사용 `DESK_REUSE_REPORTS_MIN` 은 0 이면 꺼진다 — README "ChatGPT 플랜으로 실제 실행".

## 6. 토스증권 Open API

- `.env.runtime` 에 `DESK_BROKER=toss`, `TOSS_CLIENT_ID` · `TOSS_CLIENT_SECRET`(· `TOSS_ACCOUNT_SEQ`).
- **토스 WTS → 설정 → Open API → 허용 IP** 에 홈랩 공인 IP(지금 `1.241.243.70`)를 등록한다. 등록되지 않은 IP 는 403 이다.
- 공인 IP 는 SK브로드밴드 동적 IP 라 바뀔 수 있다. 바뀌면 대시보드 보유 종목 패널에 403 이 보인다 → WTS 에서 다시 등록.
- **토큰은 키(client)당 1개만 유효하다.** 같은 키로 다른 곳에서 토큰을 받으면 서버 토큰이 즉시 무효화된다(401 `token-revoked`).
  서버는 401 을 받으면 새로 받아 한 번 다시 부르지만(#50), 그 순간 다른 쪽 토큰이 죽는다. 그래서 **운영 점검은 앱 API**(`/api/quote` · `/api/portfolio` · `/api/review` …)로 하고,
  컨테이너 안 스크립트에서 `TossBroker` 를 직접 만들지 않는다. PC 로컬 개발은 `DESK_BROKER=mock`(또는 별도 키)로 띄운다 —
  PC 와 서버가 같은 공인 IP 라 허용 IP(403)로도 막히지 않는다. 다른 앱(semibot 등)에 토스를 붙일 때는 키를 따로 발급받는다.
  두 곳이 같은 키를 계속 쓰면 서로 무효화를 반복한다(핑퐁) — 서버는 재발급을 1분에 한 번으로 묶고 로그에 `toss token revoked` 를 남긴다.
- 시세 · 보유 정보를 다른 사람에게 보여주지 않는다(재배포 금지). 로그인이 소유자 한 명으로 막는 이유다.

## 7. 백업

| 대상 | 방법 |
|------|------|
| PostgreSQL `trading_desk` | dev 인스턴스라 정기 백업이 없다. `ssh homelab-main-server 'docker exec postgresql pg_dump -U postgres -Fc trading_desk' > trading_desk.dump` |
| 볼륨 `trading-desk-tradingagents-home` | ChatGPT 로그인 · 메모리 로그 · 리포트. `docker run --rm -v trading-desk-tradingagents-home:/v -v $PWD:/b alpine tar czf /b/ta-home.tgz -C /v .` — **로그인 토큰이 들어 있어 사본도 비밀로 다룬다** |

## 8. 인그레스 (Infra 저장소)

`D:\ProjectWorkSpace\Infra` 는 공개 저장소다. 이 앱에 관한 것은 도메인 · 사설 IP · 포트까지만 적는다.

- `nginx/conf.d/trading.8llow8llowme.conf` — HTTP → HTTPS, `X-Real-IP $remote_addr`, SSE 용 `proxy_buffering off` · `proxy_read_timeout 1h`
- 인증서: `certbot/init-cert-non-www.sh trading.8llow8llowme.com`
- fail2ban: 로그인 401 반복 IP 차단 (`fail2ban/filter.d`, `jail.local`)

## 9. 한국투자증권(KIS) 실시간 시세 (#125)

- 한국투자증권 계좌 → **KIS Developers(apiportal.koreainvestment.com) → API 신청** 으로 실전투자 앱키 · 앱시크릿을 받는다.
- 서버 `.env.runtime` 에 `DESK_KIS_APP_KEY` · `DESK_KIS_APP_SECRET` 을 넣고 `scripts/deploy.sh HEAD backend`. 모의투자 키면 `DESK_KIS_WS_URL` · `DESK_KIS_REST_URL` 도 모의 주소로(`.env.example`).
- 서버에서 `ops.koreainvestment.com:21000`(ws) · `openapi.koreainvestment.com:9443`(https) 로 나가는 연결이 열려 있어야 한다.
- 확인: 장중에 실시간 화면을 열면 시세 출처가 실시간(`kis`)으로 바뀌고 1초 간격으로 갱신된다.
  로그인한 브라우저에서 `GET /api/live/kis`(#143) 로 상태를 본다 — `connected` · 구독 종목(`tickers`) · 시장별 마지막 체결 수신 시각(`last_tick.KR` · `last_tick.US`) ·
  거절된 종목(`refused`) · 접속키가 마지막으로 거절된 시각(`approval_refused_at`) · 마지막 끊김(`last_drop`: 시각 · 예외 타입, 정기 접속키 교체는 빼고). 키 · 접속키는 들어 있지 않다.
  INFO 로그는 출력하지 않는다(상태는 이 API 로 본다). 문제는 백엔드 로그(WARNING)의 `kis stream dropped: <예외 타입>` · `kis stream refused <종목>: <사유>` · `approval key refused` 로도 남는다. 그동안 화면은 토스 폴링으로 계속 돈다.
- 규칙(키 보관 · 회전 · 로그)은 [security.md §5](security.md#5-비밀).

## 10. 알림 채널 — 텔레그램 · 카카오톡 (#580)

값은 서버 `.env.runtime` 에만 넣고, 넣은 뒤 `scripts/deploy.sh HEAD backend`. 하나라도 비면 그 채널만 꺼진다(보안 규칙: [security.md §5](security.md)).

- 공통: `DESK_PUBLIC_URL=https://trading.8llow8llowme.com` (메시지 링크 · 카카오 Redirect URI 의 앞부분)
- **텔레그램**
  1. 텔레그램에서 `@BotFather` → `/newbot` → 토큰을 `DESK_TELEGRAM_BOT_TOKEN` 에.
  2. 만든 봇에게 아무 말이나 한 번 보낸 뒤, 서버에서 `curl -s "https://api.telegram.org/bot<토큰>/getUpdates"` 의 `message.chat.id` 를 `DESK_TELEGRAM_CHAT_ID` 에(이 명령은 셸 기록에 토큰이 남으니 끝나면 `history -d`).
  3. 재배포 → 화면 설정 → 알림 채널 → 텔레그램 "켜기" → "시험 보내기".
- **카카오톡 "나에게 보내기"**
  1. [카카오 디벨로퍼스](https://developers.kakao.com) → 앱 만들기 → 앱 키의 **REST API 키**를 `DESK_KAKAO_REST_KEY` 에.
  2. 카카오 로그인 켜기 → Redirect URI 에 `https://trading.8llow8llowme.com/kakao-callback.html` 등록.
  3. 보안 → Client Secret 발급 · **사용함** → `DESK_KAKAO_CLIENT_SECRET` 에.
  4. 동의 항목 → "카카오톡 메시지 전송(talk_message)" 선택 동의. 플랫폼 → Web 사이트 도메인에 `https://trading.8llow8llowme.com`(메시지 링크).
  5. `docker exec trading-desk-backend-prod python -m desk.cli otp-key` 로 새 키를 만들어 `DESK_NOTIFY_KEY` 에(OTP 키와 다른 값).
  6. 재배포 → 설정 → 카카오톡 "연결" → 카카오 로그인 · 동의 → 돌아오면 "시험 보내기". 리프레시 토큰은 약 2달이라, 화면에 "다시 연결"이 뜨면 같은 순서로 한 번 더.

## 11. KRX OPEN API 일별 이력 (#601)

- openapi.krx.co.kr 에서 인증키를 받고, 쓸 서비스마다 **이용 신청**을 해 승인을 받는다(목록 · 필드는 [krx.md](krx.md)). 승인 안 된 서비스는 401 로 답하고 6시간 건너뛴다.
- 서버 `.env.runtime` 에 `DESK_KRX_API_KEY` 를 넣고 `scripts/deploy.sh HEAD backend`. 비어 있으면 수집이 꺼지고 다른 기능은 그대로다. `DESK_MACRO_INTERVAL_SEC=0` 이어도 꺼진다.
- 그다음부터 KRX 영업일 08:30 KST 뒤 첫 패스(15분 간격)가 전 영업일치를 `krx_daily` 에 쌓는다. 서버가 쉬었으면 최근 2주 중 빠진 날을 채운다.
- 과거치: `ssh -t homelab-backend-server docker exec -it trading-desk-backend-prod python -m desk.cli krx backfill --api kospi_dd_trd --from 2015-01-01`
  (이미 받은 날은 건너뛴다 · 기본 `--max-calls 3000`, 키 한도는 하루 10,000건). 진행은 `python -m desk.cli krx status`.
