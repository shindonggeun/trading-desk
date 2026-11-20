# 보안 — 로그인 · 세션 · 비밀

이 앱은 `https://trading.8llow8llowme.com` 으로 **인터넷에 열려 있고 사용자는 1명**이다. 뚫리면 보유 종목이 보이고,
소유자의 ChatGPT 구독으로 분석이 돌고, 나중에 붙을 주문 기능에 닿는다. 그래서 모든 경로가 로그인 뒤에 있다.

## 1. 계정

- 가입 API 는 없다. 계정은 서버에서 CLI 로만 만든다.

  ```bash
  docker exec -it trading-desk-backend-prod python -m desk.cli create-user <이메일>      # 비밀번호를 두 번 묻는다(12자 이상)
  docker exec -it trading-desk-backend-prod python -m desk.cli set-password <이메일>     # 바꾸면 모든 세션이 끊긴다
  docker exec -it trading-desk-backend-prod python -m desk.cli revoke-sessions <이메일>  # 비밀번호는 두고 전부 로그아웃
  ```

- 비밀번호는 argon2id 해시만 `users.password_hash` 에 저장한다. 해시 파라미터가 바뀌면 다음 로그인 때 다시 해시한다.
- 없는 이메일도 더미 해시를 검증해 응답 시간 · 본문이 틀린 비밀번호와 같다(계정 열거 방지).

## 2. 세션

| 항목 | 값 |
|------|-----|
| 토큰 | `secrets.token_urlsafe(32)`, DB 에는 SHA-256 만 (`sessions.token_hash`) |
| 쿠키 | `__Host-desk_session` — `HttpOnly` · `Secure` · `SameSite=Strict` · `Path=/`, Domain 없음(형제 서브도메인이 심거나 가리지 못한다) |
| 만료 | 로그인부터 `DESK_SESSION_MAX_HOURS`(72), 마지막 요청부터 `DESK_SESSION_IDLE_HOURS`(12) |
| 로그인 | 기존 쿠키의 세션을 지우고 새 토큰을 준다(세션 고정 방지) |
| 로그아웃 | 서버의 세션 행을 지운다 — 복사해 둔 쿠키도 바로 죽는다 |
| 비밀번호 변경 | 그 사용자의 세션을 전부 지운다 |

SSE(`EventSource`)도 쿠키로 인증된다. URL 에 토큰을 싣지 않는다(접근 로그 · 브라우저 기록에 남는다).
열린 스트림도 5초마다(라운드가 더 길면 매 라운드, 어떤 경우에도 15초 안) 세션을 다시 확인해, 로그아웃 · 폐기 · 만료되면 끊긴다.

## 3. 경로

- 세션 없이 열리는 경로는 `GET /api/health`(`{"ok": true}` 만) 와 `POST /api/auth/login` 둘뿐이다(`api.OPEN_PATHS`).
  나머지는 경로 이름과 상관없이 401 이다 — 새 라우트를 만들어도 기본이 닫힘이다.
- FastAPI `/docs` · `/openapi.json` 은 기본으로 꺼져 있고(`DESK_ENABLE_DOCS`), 켜도 로그인 뒤다.
- `POST`/`PUT`/`PATCH`/`DELETE` 는 `Origin` 이 있으면 자기 `Host` 와 같아야 한다(로그인 CSRF 포함). `SameSite=Strict` 와 이중 방어다.
- 계정별 데이터(즐겨찾기 `favorites` #134, 섹터 탭 `sector_groups` #156)는 세션의 `request.state.user.id` 로만 범위를 정한다 — 경로 · 본문의 사용자 id 는 받지 않는다.
- 리포트(#495 `reports`)는 모든 계정이 같이 본다. 그래서 #681 부터 주제 후보는 설정의 순환 목록(`DESK_REPORT_TOPICS`, 비면 기본 목록)과 두 지수뿐이고 **누구의 즐겨찾기도 넣지 않는다**. 새 리포트 푸시는 소유자(첫 계정)에게만 보낸다. 수동 생성(`POST /api/reports`)은 동시에 1개, 직전 리포트 뒤 10분 안에는 429 다.
  경로 · 본문의 자원 id(섹터 id 등)는 언제나 `user_id` 와 함께 찾고, 남의 것이면 없는 것과 똑같이 답한다(수정 404, 삭제 204).
- 채팅 도구 중 밖으로 나가는 것은 `news_search`(#403) 하나다. 보내는 것은 모델이 쓴 짧은 검색어뿐이고(100자, 9자리 이상 숫자열 · 이메일은 보내지 않고 거절),
  받는 것은 기사 제목 · 매체 · 시각 · http(s) 링크 8건까지다(본문 없음, 2MB 넘으면 받다가 끊음, 리다이렉트 따라가지 않음). 도구는 전부 읽기 전용이라
  기사 제목에 섞인 지시가 할 수 있는 일은 답을 흐리는 것까지다. 주문 · 계좌 도구를 채팅에 붙이기 전에 이 전제를 다시 본다.

## 4. 로그인 시도 제한

| 층 | 규칙 |
|----|------|
| 앱(`auth.LoginThrottle`, 메모리) | 15분 안에 IP 당 **시도** 5회 → 429 + `Retry-After`. 비밀번호 검사 **전에** 센다 — 병렬 요청이 검사 중에 한도를 넘지 못한다. 성공하면 그 IP 의 기록을 지운다 |
| argon2 동시 검사 | 2개(`_VERIFY_SLOTS`). 넘치면 503 — 검사 한 번이 ~64MiB 라 몰아치기로 호스트 메모리를 못 태운다 |
| 인그레스 fail2ban (`storage` `.12`) | nginx 접근 로그의 `POST /api/auth/login` 401 을 세어 IP 를 차단한다 — 재시작에도 남는 긴 기억 |

**아이디 단위 잠금은 일부러 없다.** 계정이 하나라 아이디를 아는 누구나 소유자를 계속 잠가 둘 수 있다(2026-11-04 보안 검토).
분산 추측은 fail2ban · 12자 이상 비밀번호 + argon2id · 다음 단계 OTP 가 막는다.

클라이언트 IP: 인그레스가 `X-Real-IP` 를 붙이고, 웹 컨테이너 nginx 가 인그레스(`192.168.0.12`)에서 온 값만 믿어
`X-Forwarded-For` 를 그 한 값으로 **덮어쓴다**. 백엔드 uvicorn 은 `--proxy-headers` 로 그것을 클라이언트 주소로 쓴다.
백엔드 포트는 호스트에 열지 않는다 — 열면 헤더 위조로 시도 제한을 우회할 수 있다.

## 5. 비밀

**운영 env 의 정본은 홈랩 Vault 다(#691)** — `kv/trading-desk/backend/prod/env`(백엔드), `kv/trading-desk/frontend/prod/env`(웹: 네트워크 · 바인드 · 포트).
Jenkins 가 배포할 때마다 읽어 임시 env 파일(600)로 compose 에 넘기고 끝나면 지운다([deploy.md](deploy.md) 2 · 3절). 앱은 env 를 그대로 읽고 Vault SDK 는 쓰지 않는다.
아래 표의 "서버 `.env.runtime`" 은 Vault 로 옮기기 전의 위치이자 손 배포(`scripts/deploy.sh`, Jenkins · Vault 를 못 쓸 때) 대체 경로다 —
값을 바꿀 때는 **Vault 를 고치고**(`vault kv patch`, 값은 채팅 · 로그 · 커밋에 쓰지 않는다) Jenkins 잡을 `FORCE_DEPLOY` 로 다시 돌린다. `.env.runtime` 을 쓰는 날에는 먼저 Vault 와 맞춘다.

- 파이프라인 규칙: 값은 Groovy 변수 · 명령 인자 · 콘솔에 나오지 않는다(`set +x`, 토큰 · AppRole 자격은 표준입력으로 curl 에). 키 이름 · 개수만 찍는다.
  배포 확인 실패 때 찍는 컨테이너 로그는 env 값(8자 이상 또는 비밀처럼 보이는 키)을 `****` 로 가린다. 로그인 토큰은 끝날 때 `revoke-self` 로 폐기한다.
- Jenkins credential 은 폴더 `trading-desk` 범위(`trading-desk-vault-role-id` · `-secret-id` · `trading-desk-github`). AppRole `jenkins-trading-desk` 는 `kv/trading-desk/+/+/env` 읽기만.
- 저장소가 공개라 잡은 fork PR 을 발견하지 않는다. develop 은 소유자만 머지한다 — Jenkinsfile 은 docker 소켓이 있는 운영 agent 에서 돈다.
  PR · 다른 브랜치를 거르는 가드는 `load` 보다 먼저, 얇은 `Jenkinsfile-*` 에 있다. 폴더 `trading-desk` 의 Configure · Replay · 콘솔 읽기 권한은 소유자만 갖는다(Replay = 운영 호스트 root).
- **받아들인 위험 — 공유 deploy agent**: `backend-prod-agent` · `frontend-prod-agent` 는 다른 홈랩 프로젝트의 prod 배포도 쓰고 docker 소켓(= 호스트 root)이 있다.
  그 프로젝트의 배포 브랜치 · 잡을 바꿀 수 있는 사람은 `docker inspect trading-desk-backend-prod` 로 이 앱의 env(DB · 브로커 · 알림 키)를 읽을 수 있다.
  지금은 홈랩의 모든 배포 권한자가 소유자 한 명이라 공유한다. **다른 사람이 커밋 · 잡 권한을 갖게 되면** trading-desk 전용 agent(라벨 `deploy-trading-desk`, 다른 폴더 잡은 못 쓰게 제한)로 옮긴다.
- AppRole 은 CIDR 에 묶이지 않았다(Vault 가 인그레스 nginx 뒤라 출발지 주소가 보이지 않는다). role-id · secret-id 가 새면 어디서나 읽힌다 —
  credential 은 Jenkins 폴더에만 두고, 샌 의심이 들면 바로 secret-id 를 회전하고 옛 것을 destroy 한다. 로그인 토큰 수명은 10분(`token_ttl`)이다.
- secret-id 회전: Infra 저장소 `vault/scripts/rotate-approle-secret.sh jenkins-trading-desk` → 새 값을 Jenkins 폴더 credential 에 바꿔 넣는다(Infra `vault/README.md` trading-desk 절).

| 비밀 | 있는 곳 | 저장소 |
|------|---------|--------|
| DB 비밀번호 · 토스 키 | 서버 `~/deploy/trading-desk/.env.runtime` (권한 600) | `.env.example` 에 이름만 |
| 사용자 비밀번호 | 소유자의 머리 · 비밀번호 관리자. DB 에는 해시만 | 없음 |
| ChatGPT 로그인 | 볼륨 `trading-desk-tradingagents-home` 의 `chatgpt-auth.json` | 없음 |
| 한국투자증권(KIS) 앱키 · 앱시크릿 | 서버 `.env.runtime` 의 `DESK_KIS_APP_KEY` · `DESK_KIS_APP_SECRET` | `.env.example` 에 이름만 |
| KIS 웹소켓 접속키(approval key) | 백엔드 프로세스 메모리(12시간마다 새로 발급) | 없음 |
| KIS REST 접근 토큰(access token) | 백엔드 프로세스 메모리(20시간마다 새로 발급, 1분에 1번 이하) | 없음 |
| 텔레그램 봇 토큰 · chat_id · 카카오 REST 키 · 클라이언트 시크릿 · `DESK_NOTIFY_KEY` (#580) | 서버 `.env.runtime` | `.env.example` 에 이름만 |
| 카카오 액세스 · 리프레시 토큰 (#580) | DB `notify_channels.secret`, `DESK_NOTIFY_KEY`(Fernet)로 봉인 | 없음 |
| KRX OPEN API 인증키 (#601) | 서버 `.env.runtime` 의 `DESK_KRX_API_KEY`. 요청 헤더 `AUTH_KEY` 로만 보내고 로그 · URL · DB 에 남기지 않는다 | `.env.example` 에 이름만 |

### KIS 실시간 시세 키 (#125)

- **이 앱의 용도는 시세 구독뿐이다.** 앱은 `/oauth2/Approval` 로 접속키만 받고, 주문 · 잔고 · 계좌 API 는 부르지 않는다. 계좌번호와 HTS ID 는 받지도 저장하지도 않는다.
  다만 앱키 · 앱시크릿 자체는 같은 계좌의 주문 API 에도 쓸 수 있는 키다. 그래서 토스 키와 같은 등급으로 다룬다.
- **전송 구간:** 앱키 · 앱시크릿은 HTTPS(`openapi.koreainvestment.com:9443`)로만 나간다. KIS 실시간 서버는 평문 `ws://` 만 제공하므로 접속키는 암호화되지 않은 채 전달된다.
  접속키로는 시세 외에 그 계좌의 **실시간 체결통보**(`H0STCNI0` · `H0GSCNI0`, HTS ID 필요)도 구독할 수 있다. 이 앱은 구독하지 않지만, 접속키를 가로챈 사람은
  HTS ID 를 알면 체결 내역(종목 · 수량 · 가격)을 볼 수 있다. KIS 가 평문 ws 만 제공해 허용하는 위험이다. 매매 계좌와 분리하려면 시세 전용 KIS 계좌의 키를 쓴다.
- **로그:** 키 · 시크릿 · 접속키는 로그 · 오류 응답 · 프레임에 남기지 않는다. 연결 오류는 예외 **타입 이름**만, 구독 거절은 KIS 의 짧은 사유(`msg1`)만 남긴다.
  `websockets` 라이브러리는 DEBUG 에서 보낸 프레임(접속키 포함)을 찍으므로 그 로거는 INFO 로 고정한다. `Settings` 의 키 필드는 `repr` 에서 뺀다.
  `DESK_KIS_REST_URL` 이 `https://` 가 아니면 기능을 끈다(키가 평문으로 나가지 않게).
- **회전:** KIS Developers 에서 앱시크릿을 재발급한다 → 서버 `.env.runtime` 을 고친다 → `scripts/deploy.sh HEAD backend`. 앱은 재시작 때 접속키를 버리지만,
  KIS 쪽에서는 발급 후 약 하루 동안 유효하다.
  키가 채팅 · 메일 · 이슈처럼 저장소 밖에 한 번이라도 평문으로 남았다면 바로 재발급한다.
- **끄기:** 두 값 중 하나라도 비면 웹소켓을 열지 않고, 실시간 화면은 토스 5초 폴링으로 동작한다.

### KIS REST 접근 토큰 — 코스피200 선물 현황 (#160)

- **용도:** `desk.futures_flow` 가 `/oauth2/tokenP` 로 접근 토큰을 받아 **시세 조회 두 개**(선물 현재가 `FHMIF10000000` · 시장별 투자자 동향 `FHPTJ04030000`)만 부른다. 주문 · 잔고 · 계좌 API 는 부르지 않는다.
- **보관:** 토큰은 프로세스 메모리에만 두고 20시간 뒤 새로 받는다. KIS 는 발급 때마다 알림을 보내고 잦은 발급을 막으므로 1분에 1번 이하로만 묻고, 발급이 거절되거나 새 토큰도 거절되면 10분 · 30분으로 간격을 늘린다(성공하면 처음으로). 만료 · 무효 응답(HTTP 401 · `EGW00121` · `EGW00123`)이면 그 요청이 쓴 토큰만 버리고 한 번 새로 받는다.
- **로그 · 응답:** 키 · 시크릿 · 토큰은 로그 · 오류 응답에 넣지 않는다. 화면 응답의 실패 문구는 TR 코드 · HTTP 상태 · KIS 메시지 코드(`msg_cd`)뿐이다. KIS 의 사유(`msg1`, 80자)는 서버 로그에만, 키 · 시크릿 · 토큰과 같은 부분을 `***` 로 지운 뒤 남긴다. `KisRest` 의 `repr` 에도 URL 만 나온다.
- **전송 구간 · 끄기:** 웹소켓과 같은 키 · `DESK_KIS_REST_URL` 을 쓴다. 키가 없거나 URL 이 `https://` 가 아니면 부르지 않고 `/api/market/futures` 는 `supported: false` 를 준다. 가짜 실행(`DESK_FAKE_RUNS`)에서도 부르지 않는다.

### 알림 채널 — 텔레그램 · 카카오톡 (#580)

- **용도:** 웹 푸시와 같은 알림(수급 흐름 · 나스닥 선물 · 아침 시황 …)을 텔레그램 봇 `sendMessage` 와 카카오톡 "나에게 보내기"로도 보낸다. 받는 사람은 소유자 한 명이다. 채널 설정(켬 · 종류별 켬 · 시험 · 카카오 연결 · 끊기)은 **소유자(첫 계정)만** 바꾼다 — 알림은 모든 계정의 기기로 가는 공용이라서.
- **텔레그램:** 토큰 · chat_id 는 env 에만 있고 화면 · 응답에는 "설정됨" 여부만 나간다. 토큰이 요청 URL 경로에 들어가므로 `httpx` 로거는 WARNING 으로 고정하고, 혹시 낮아져도 `/bot<토큰>` 을 `/bot***` 로 지우는 필터를 단다. 채널 오류는 예외 메시지(URL 포함)가 아니라 HTTP 상태 · 텔레그램 `error_code` · 예외 타입 이름만 남긴다.
- **카카오 OAuth:** `POST /api/notify/kakao/connect`(세션 · Origin 검사 · 소유자)가 state(32바이트 무작위)를 만들어 해시로 메모리에 10분 두고 authorize URL 을 준다. 카카오는 정적 `/kakao-callback.html` 로 돌려보낸다 — 교차 사이트 최상위 이동이라 `SameSite=Strict` 쿠키가 없으므로, 그 페이지가 주소창의 code · state 를 지우고 **같은 사이트 POST** `/api/notify/kakao/callback` 으로 넘긴다. 그래서 콜백도 세션 · Origin 검사를 그대로 받고, 세션 없이 열리는 경로는 여전히 둘뿐이다. state 는 요청한 계정에 묶여 한 번만 쓰인다(워커 프로세스 1개 전제 — 재시작하면 연결을 다시 시작한다).
- **code 유출:** code 는 쿼리로 와서 인그레스 접근 로그에 남을 수 있다. 토큰 교환에 **클라이언트 시크릿이 필수**라(없으면 카카오 채널이 꺼진다) code 만으로는 토큰을 받을 수 없다. 페이지는 `Referrer-Policy: same-origin`(nginx)에 `no-referrer` 메타까지 둔다.
- **토큰 보관 · 갱신:** 액세스 · 리프레시 토큰은 `DESK_NOTIFY_KEY`(Fernet, OTP 키와 분리 — 하나를 돌려도 다른 쪽이 끊기지 않는다)로 봉인해 `notify_channels.secret` 에 둔다. 액세스 토큰은 만료 60초 전 또는 401 뒤 한 번 갱신하고, 리프레시 토큰은 새로 올 때만 바꾼다. 갱신이 거절(`invalid_grant`)되거나 리프레시 토큰이 7일 안에 끝나면 화면이 "다시 연결"을 띄운다. 키가 없거나 틀리면 카카오만 꺼진다.
- **전송:** 채널은 알림 스레드가 아닌 전용 워커 1개에서 보낸다(429 · 5xx 는 최대 3번, 텔레그램 `retry_after` 는 30초까지). 실패해도 알림 기록 · 웹 푸시 · 다른 채널은 그대로다. 대기열은 50건, 재시작 때 대기 중인 것은 버린다.
- **회전:** 텔레그램은 BotFather `/revoke` → `.env.runtime` → 백엔드 재배포. 카카오는 콘솔에서 시크릿 재발급 → `.env.runtime` → 재배포 → 설정에서 다시 연결. `DESK_NOTIFY_KEY` 를 바꾸면 저장된 카카오 토큰을 못 열므로 다시 연결한다.

## 6. OTP 2단계 인증 (#6)

OTP 앱(Google Authenticator 등, TOTP RFC 6238)으로 두 번째 확인을 한다. **켜면 비밀번호만으로는 세션이 생기지 않는다.**

### 로그인 (같은 경로 두 번)

| 단계 | 요청 `POST /api/auth/login` | 응답 |
|------|-----------------------------|------|
| 1 | `{username, password}` | OTP 꺼짐: 204 + 세션 쿠키. OTP 켜짐: 200 `{otp_required: true, challenge}` — 쿠키 없음 |
| 2 | `{challenge, code}` | 맞으면 204 + 세션 쿠키, 아니면 401 |

- 두 단계 모두 같은 경로라 인그레스 fail2ban(로그인 401)이 코드 추측도 센다. 앱의 IP 당 시도 제한도 두 단계 모두 센다 —
  OTP 가 켜진 계정은 비밀번호가 맞아도 IP 기록을 지우지 않고, 코드까지 통과해야 지운다.
- `challenge` 는 32바이트 무작위, 메모리에만(해시로) 있고 5분 · 5번 시도 뒤 사라진다. 서버를 재시작하면 비밀번호부터 다시.
- `code` 는 6자리 TOTP(앞뒤 30초 허용) 또는 복구 코드. **같은 시간 칸의 코드는 한 번만** 받는다(마지막 칸을 저장하고 비교 후 갱신).
- **계정 단위 상한**: IP 를 바꿔 가며 추측해도 계정 하나에 코드 실패가 1시간 10번이면 새 challenge 를 429 로 막고
  "비밀번호가 새었을 수 있다" 를 오류 로그로 남긴다(이 단계에 왔다는 것은 비밀번호를 안다는 뜻).

### 켜기 · 끄기 (로그인 상태)

화면: 상단 **설정 → 2단계 인증 (OTP)** 에서 "사용하기" / "사용 안 함으로 바꾸기"(#44). 기본은 사용 안 함이다.
켜도 **지금 세션은 그대로**이고 다음 로그인부터 비밀번호 다음에 코드를 묻는다. 로그인 화면은 1단계 응답이 `otp_required` 면
코드 입력으로 넘어가고("복구 코드 사용" 전환), 5분이 지나면 비밀번호 단계로 돌아간다.

| 경로 | 필요한 것 | 하는 일 |
|------|-----------|---------|
| `POST /api/auth/otp/setup` | 비밀번호 재확인 | 대기 비밀값 생성, `otpauth://` 주소(QR) · 비밀값 반환. 아직 꺼진 상태 |
| `POST /api/auth/otp/enable` | 앱에 뜬 코드(setup 후 10분 안) | 켠다. 복구 코드 10개를 **이번 한 번만** 반환. 지금 브라우저 말고 모든 세션을 끊는다 |
| `POST /api/auth/otp/disable` | 비밀번호 + 코드(또는 복구 코드) | 끈다. 복구 코드를 지우고 지금 브라우저 말고 모든 세션을 끊는다 |

- 세 경로는 **호출마다 IP 시도 1회**로 세고, 어느 것도 기록을 지우지 않는다(지우는 것은 로그인 성공뿐).
  훔친 세션 + 비밀번호로 disable 에 코드를 무한히 넣던 경로를 막는다(2026-11-05 보안 검토 HIGH).
- 틀린 비밀번호 · 코드는 403 이다 — 401 은 화면이 "세션 끝남" 으로 읽는다.
| `GET /api/auth/me` | — | `otp_enabled` · 남은 복구 코드 수 · 서버에 키가 있는지 |

### 저장 · 키

- 비밀값은 `DESK_OTP_KEY`(Fernet)로 봉인해 `users.totp_secret` 에 둔다. DB 사본만으로는 코드를 만들 수 없다.
- 복구 코드는 `xxxxx-xxxxx`(약 50비트), DB 에는 SHA-256 만, 쓰면 `used_at` 이 찍힌다.
- **키가 없거나 틀리면 막는다** — OTP 가 켜진 계정의 로그인은 503 이고, 두 번째 확인을 건너뛰지 않는다.
  키 형식이 잘못돼도 앱은 뜬다(OTP 만 못 쓰고 오류 로그). OTP 를 안 켠 로그인은 그대로 된다.
- 키 만들기: `docker exec trading-desk-backend-prod python -m desk.cli otp-key` → 서버 `.env.runtime` 의 `DESK_OTP_KEY` → 백엔드 재배포.
  키를 잃으면 비밀값을 못 연다 — 아래 CLI 로 끄고 다시 등록한다.

### 휴대폰을 잃었을 때

1. 복구 코드로 로그인 → 화면에서 끄고 새로 등록
2. 복구 코드도 없으면 서버에서: `docker exec -it trading-desk-backend-prod python -m desk.cli disable-otp <이메일>` (모든 세션도 끊는다)
