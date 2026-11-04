# 보안 — 로그인 · 세션 · 비밀

이 앱은 `https://trading.8llow8llowme.com` 으로 **인터넷에 열려 있고 사용자는 1명**이다. 뚫리면 보유 종목이 보이고,
소유자의 ChatGPT 구독으로 분석이 돌고, 나중에 붙을 주문 기능에 닿는다. 그래서 모든 경로가 로그인 뒤에 있다.

## 1. 계정

- 가입 API 는 없다. 계정은 서버에서 CLI 로만 만든다.

  ```bash
  docker exec -it trading-desk-backend-prod python -m desk.cli create-user <아이디>      # 비밀번호를 두 번 묻는다(12자 이상)
  docker exec -it trading-desk-backend-prod python -m desk.cli set-password <아이디>     # 바꾸면 모든 세션이 끊긴다
  docker exec -it trading-desk-backend-prod python -m desk.cli revoke-sessions <아이디>  # 비밀번호는 두고 전부 로그아웃
  ```

- 비밀번호는 argon2id 해시만 `users.password_hash` 에 저장한다. 해시 파라미터가 바뀌면 다음 로그인 때 다시 해시한다.
- 없는 아이디도 더미 해시를 검증해 응답 시간 · 본문이 틀린 비밀번호와 같다(아이디 열거 방지).

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
열린 스트림도 매 라운드(최대 15초) 세션을 다시 확인해, 로그아웃 · 폐기 · 만료되면 끊긴다.

## 3. 경로

- 세션 없이 열리는 경로는 `GET /api/health`(`{"ok": true}` 만) 와 `POST /api/auth/login` 둘뿐이다(`api.OPEN_PATHS`).
  나머지는 경로 이름과 상관없이 401 이다 — 새 라우트를 만들어도 기본이 닫힘이다.
- FastAPI `/docs` · `/openapi.json` 은 기본으로 꺼져 있고(`DESK_ENABLE_DOCS`), 켜도 로그인 뒤다.
- `POST`/`PUT`/`PATCH`/`DELETE` 는 `Origin` 이 있으면 자기 `Host` 와 같아야 한다(로그인 CSRF 포함). `SameSite=Strict` 와 이중 방어다.

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

| 비밀 | 있는 곳 | 저장소 |
|------|---------|--------|
| DB 비밀번호 · 토스 키 | 서버 `~/deploy/trading-desk/.env.runtime` (권한 600) | `.env.example` 에 이름만 |
| 사용자 비밀번호 | 소유자의 머리 · 비밀번호 관리자. DB 에는 해시만 | 없음 |
| ChatGPT 로그인 | 볼륨 `trading-desk-tradingagents-home` 의 `chatgpt-auth.json` | 없음 |

## 6. 다음 — OTP (#6)

비밀번호 다음 단계로 TOTP 를 붙인다. 활성화 뒤에는 비밀번호만으로 세션이 생기지 않아야 한다.
