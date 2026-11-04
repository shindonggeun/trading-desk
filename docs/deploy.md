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
```

서버 `~/deploy/trading-desk/` 구조:

```
.env.runtime   비밀 · 설정 (권한 600) — 저장소에 없다
src/           방금 배포한 소스 (REVISION 에 커밋)
src.old/       직전 배포 — 되돌릴 때: src.old 로 들어가 같은 compose 명령
```

`main` 이 배포본이다(`docs/git-workflow.md` §8). 배포 뒤 확인:

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
ssh -t homelab-backend-server docker exec -it trading-desk-backend-prod python -m desk.cli create-user <아이디>
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

## 6. 토스증권 Open API

- `.env.runtime` 에 `DESK_BROKER=toss`, `TOSS_CLIENT_ID` · `TOSS_CLIENT_SECRET`(· `TOSS_ACCOUNT_SEQ`).
- **토스 WTS → 설정 → Open API → 허용 IP** 에 홈랩 공인 IP(지금 `1.241.243.70`)를 등록한다. 등록되지 않은 IP 는 403 이다.
- 공인 IP 는 SK브로드밴드 동적 IP 라 바뀔 수 있다. 바뀌면 대시보드 보유 종목 패널에 403 이 보인다 → WTS 에서 다시 등록.
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
