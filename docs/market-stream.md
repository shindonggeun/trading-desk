# 시장 데이터 스트림 — `GET /api/stream/market` (#690)

시세 · 수급 · 알림을 폴링 대신 **밀어 주는** SSE 경로다. 폴링은 끊겼을 때의 대체로만 남는다(FE #764).
분석 run 의 이벤트(`/api/runs/{id}/stream`, DB 재생)와 실시간 차트(`/api/live/stream`)는 별개이고 그대로다.

## 1. 경로

```
GET /api/stream/market?channels=quote,flow,futures,alert&tickers=AAPL,005930.KS
```

| 매개변수 | 뜻 |
|----------|----|
| `channels` | 받을 채널(쉼표). 비우면 `flow,futures,alert`. 알 수 없는 이름은 422 |
| `tickers` | `quote` 채널에서 받을 종목(쉼표, 최대 50). 비우면 시세를 보내지 않는다. 서버가 `/api/quote` 와 같이 정규화한다(대문자 · `005930.KS` · 이름 → 코드) — 이벤트의 `key` 는 그 정규형이다 |

- 세션 쿠키가 필요하다(기본 닫힘, 없으면 401). 열린 동안 15초마다 세션을 다시 확인하고(활동으로 세지 않는다), 로그아웃 · 폐기 · 만료면 스트림을 끝낸다.
- 한 세션에 동시에 3개까지(넘으면 429). **탭 하나에 연결 하나**를 쓰고 채널 · 종목은 쿼리로 묶는다(종목이 바뀌면 다시 연다).
- `DESK_MARKET_STREAM=off` 면 503 — FE 는 폴링만 쓴다.
- 응답 헤더 `Cache-Control: no-cache`, `X-Accel-Buffering: no`. 첫 줄 `retry: 3000`.

## 2. 이벤트

SSE 형식 그대로다: `event:` 가 종류, `data:` 가 JSON 한 줄, 데이터 이벤트에는 `id:`(= `event_id`).

### 데이터 이벤트 — `quote` · `flow` · `futures` · `alert` (메시지 v1)

```json
{"v": 1, "kind": "quote", "key": "AAPL", "event_id": "q-AAPL-1795147788123",
 "source": "kis", "source_at": "2026-11-20T14:31:05+00:00", "observed_at": "2026-11-20T14:31:05.412+00:00",
 "payload": { ... }}
```

| 필드 | 뜻 |
|------|----|
| `v` | 계약 버전. 지금 1. 모르는 버전은 무시하고 폴링을 유지한다 |
| `kind` | `quote` · `flow` · `futures` · `alert` (= `event:`) |
| `key` | 채널 안의 대상. 아래 표 |
| `event_id` | 이벤트 하나의 ID. 같은 ID 는 한 번만 반영한다(재접속 스냅숏과 겹칠 수 있다) |
| `source` | 원천: `kis` · `toss` · `yahoo` · `kis_futures` · `toss_investors` · `kis_spot_investors` · `desk`(알림) |
| `source_at` | 원천이 붙인 시각(ISO, 없으면 `null`) |
| `observed_at` | 데스크가 받은(확인한) 시각(ISO UTC, 밀리초). 화면 지연 계측의 기준 |
| `payload` | 아래 표 — 같은 값의 REST 응답 모양 그대로 |

| kind | key | payload | 같은 REST |
|------|-----|---------|-----------|
| `quote` | 종목(`AAPL`, `005930.KS`) | `{ticker, price, currency, source, fetched_at}` | `GET /api/quote?ticker=` |
| `flow` | `KOSPI:1d` 처럼 `시장:간격` | 그 시장 · 간격의 응답, `records` 최근 20개. **`premarket` · `program` 은 없다** — 장 전 · 프로그램매매 칸은 REST 그대로 | `GET /api/market/flow?market=&interval=&count=20` |
| `futures` | `view` | 선물 화면 응답 | `GET /api/market/futures` |
| `alert` | 알림 `kind`(`flow_turn` · `flow_shift` · `flow_warn` · `nq_alert` · `holding_alert` · `sector_mover`) | 알림 한 건(`/api/alerts/today` 의 `items[]` 한 줄 모양, `tone` 포함) | `GET /api/alerts/today` |

- 같은 `quote` 키는 **초당 최대 1번**(그 사이 값은 마지막 것으로 합친다). 한투 실시간 틱과 토스 · 야후 폴링 시세가 같은 채널로 온다.
- `flow` · `futures` 는 collector 의 1분 스냅숏(`DESK_FUTURES_SNAPSHOT_MIN`)이 성공한 직후에 온다. 실패한 읽기는 오지 않는다.
  스냅숏은 `1d` 만 읽는다 — 다른 간격(`1w` …)은 누가 그 간격을 새로 읽었을 때만 오므로 화면은 그 키를 폴링으로 둔다.
- `alert` 는 DB 에 기록된 뒤에 온다. 보유 종목 알림(`holding_alert`)은 소유자 세션에만 간다. 놓친 알림은 `GET /api/alerts/today` 로 다시 읽는다(스트림은 재생하지 않는다).
- 알림 타임라인이 숨기는 종류(`holding_alert` · `sector_mover`)도 스트림에는 온다 — 거를지는 화면이 정한다.

### 제어 이벤트

| event | data | 언제 | FE 가 할 일 |
|-------|------|------|-------------|
| `ready` | `{"v":1, "channels":[...], "bus":"local"\|"redis", "degraded":[...]}` | 연결 직후 스냅숏(그 프로세스가 가진 채널별 마지막 값, 데이터 이벤트 모양)을 다 보낸 뒤 한 번 | `degraded` 에 없는 채널만 폴링을 멈춘다. 스냅숏에 없던 키는 REST 로 한 번 읽는다 |
| `degraded` | `{"v":1, "degraded":[...], "reason": "bus_down"\|"stale"\|...}` | 낡은 채널 집합이 바뀔 때마다(빈 목록 = 다 회복) | 목록에 든 채널은 폴링을 다시 켜고, 빠진 채널(회복)은 REST 한 번 뒤 다시 멈춘다 |
| `heartbeat` | `{"v":1, "t": ISO, "degraded":[...]}` | 15초마다 | 45초 넘게 아무 이벤트도 없으면 연결이 죽은 것으로 보고 닫은 뒤 폴링 + 지터 재연결 |
| `reset` | `{"v":1, "reason": "overflow"\|"shutdown"}` | 이 연결의 대기열이 넘쳤거나 서버가 내려갈 때. 바로 뒤에 스트림이 끝난다 | REST 로 다시 맞추고 재연결 |

- **낡음(`stale`)**: 장중(수급 · 선물 세션)인데 그 채널의 마지막 이벤트가 3분보다 오래됐으면 `degraded` 에 든다. 구독 소켓이 살아 있어도 원천(collector)이 멈췄을 수 있어서다.
- **버스 끊김(`bus_down`)**: api 가 Redis 구독을 잃으면 Redis 로 오는 채널(`flow` · `futures` · `alert`)이 `degraded` 다. 시세는 api 안(한투 실시간 · prewarm)에서도 오므로 `degraded` 에 넣지 않는다.
- 연결이 끊기면(네트워크 · 401 · 재배포) FE 는 바로 REST 한 번 → 기존 폴링 → 지터를 둔 재연결. REST 가 401 이면 로그인 처리.

## 3. 서버 구성

```
collector ─(수급 스냅숏 · 알림 · 시세 읽기)─▶ Redis pub/sub(봉인) ─▶ api ─▶ SSE
api       ─(한투 실시간 틱 · prewarm 시세)──────────────────────────▶ api ─▶ SSE   (프로세스 안)
all       ─(전부 프로세스 안)───────────────────────────────────────▶ SSE
```

- `desk/market_bus.py`. `DESK_ROLE=all` 이거나 Redis(#689)가 없으면 프로세스 안 fan-out 만 쓴다. 기본 켜짐, `DESK_MARKET_STREAM=off` 로 끈다.
- 역할이 나뉘고 Redis 가 있으면 발행한 프로세스가 Redis 채널에도 내고, api 가 구독한다. 채널 이름은 `td:bus:<HMAC>`, 메시지는
  `DESK_REDIS_CACHE_KEY` 로 봉인한다(공유 Redis — 보유 종목 알림이 실린다, [security.md](security.md) 5.2). 다른 빌드 · 다른 키의 메시지와 자기 메시지는 버린다.
- Kafka 는 쓰지 않는다: 다루는 것이 최신값과 알림뿐이라 보존 · 오프셋 재생이 필요 없고, 놓친 것은 재접속 스냅숏 · REST 타임라인으로 회수한다.
  Redis 는 이미 있는 LAN 서버라 왕복이 ~1 ms 다. 재생이 필요해지면(예: 알림 누락 0 보장) 그때 Kafka(ollama-01) 를 다시 본다.
- 계측: `GET /api/status` 의 `market_stream` — 채널별 이벤트 수, `observed_at` → api 송출까지 지연 p50 · p95(초), 연결 수, 버스 상태 · `degraded`.
