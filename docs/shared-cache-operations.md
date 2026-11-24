# 역할 분리 뒤 공유 Redis 검증 (#689)

2026-11-24 기준으로 운영 전환 기록과 develop의 경로를 대조했다. #776의 기존 기록에는
Jenkins 상위 빌드 #101 성공, API·collector·analyst 각 컨테이너의 Healthy, 인증 상태 조회의
`role=api`, 공유 캐시 켜짐, 시장 스트림 Redis 구독 및 저하 없음이 남아 있다. 전환 전
2026-11-23 16:23 KST 관측은 `all`, 공유 캐시 꺼짐, 시장 SSE 로컬이었다. 이 기록은 모드와
구독 상태의 근거이고 경로별 Redis 적중, 장중 토스·KIS 호출량, 비교 가능한 p95를 담지
않는다. [#776의 #101 운영 기록](https://github.com/shindonggeun/trading-desk/issues/776#issuecomment-7123816411)

## 생산자와 시각

| 응답 경로 | 생산·조회 경로 | 원천과 확인 시각 |
| --- | --- | --- |
| `/api/market/flow` 현물 투자자 수급 | collector 수집에서 재사용하는 공유 경로다. API는 로컬→Redis로 읽고, 없으면 기존 요청 원천 경로를 사용할 수 있다. | `freshness.source_updated_at`은 원천 제공 시각 또는 확인된 수치 변동 시각이다. 확인 시각인 `freshness.fetched_at`과 구분한다. KIS 원천 시각이 없는 항목의 공급 지연은 변동 간격에 따른 근사치다. |
| `/api/market/flow` 일봉의 `program` | collector의 기존 요청을 PostgreSQL에 기록한 뒤 시장·거래일별 Redis로 보낸다. API는 최대 3초 로컬 재사용→Redis→당일 DB 경로다. | `source_updated_at`은 KIS 원천 시각, `fetched_at`은 collector 확인 시각, `at`은 스냅숏 시각이다. DB 복귀에서는 원천 시각이 없으므로 `null`이다. |
| `/api/market/futures` | collector의 KIS 선물 수급 스냅숏과 API의 로컬→Redis→기존 원천 경로를 사용한다. 별도 선물 세션 가격의 로컬 유지값과 구분한다. | 선물 수급 `freshness`의 `fetched_at`은 확인 시각이다. 결측 원천 시각을 확인 시각으로 채우지 않는다. |
| `/api/market/credit` | collector가 KOFIA 두 표를 읽고, 공통 통계일 기준 공개 결과를 봉인해 Redis로 보낸다. API는 Redis→기존 로컬 또는 유효한 마지막 정상값을 쓰며 요청 시 KOFIA를 호출하지 않는다. | `source_date`는 공통 통계일, `refreshed_at`은 두 표를 확인한 시각이다. 휴장 중 확인 시각만 바뀔 수 있다. |
| 시세·차트·시장판 | API가 KIS 실시간 수신과 prewarm을 소유한다. 시세는 KIS 틱→로컬→짧은 공유값→기존 원천 경로를 사용할 수 있다. 지지·저항, 갭, 오실레이터 계산은 공유 경로가 있다. 벤치마크의 일반 봉 요청과 업종 등락은 로컬 경계다. | `/api/quote`의 `fetched_at`은 KIS 수신 또는 REST 확인 시각이며 체결 시각은 미제공이다. `/api/estimates` 일부의 `traded_at`과 구분한다. 봉·추정가·prewarm은 #874 범위다. |
| `/api/stream/market` | collector의 공개 수급·선물·허용된 알림은 봉인 Redis pub/sub를 통해 API에 도달한다. API의 KIS 실시간 틱과 Redis 최신값 조회는 서로 다른 경로다. NQ와 선 알림 판정은 collector 소유다. 개인 선 설정과 기록은 PostgreSQL 경계를 유지한다. | `observed_at`은 데스크 관측 시각, `source_at`은 전달된 원천 시각으로 현재 수급 일부에만 있다. 프로그램·신용융자 최신값은 REST에서 확인한다. |

analyst는 분석 작업을 실행한다. 분석 실행 SSE는 PostgreSQL 이벤트 재생 경로이며 위 시장
SSE와 구분한다. [경로별 캐시 경계](shared-cache-scope.md) · [시장 SSE 계약](market-stream.md) ·
[KOFIA 공유 #932](https://github.com/shindonggeun/trading-desk/pull/932)

## 격리 재현

develop `aab27225`에서 기존 화면·수급 벤치마크를 표본 30개, 합성 원천 지연 20ms, 합성 Redis 지연
1ms로 실행했다. 임시 SQLite와 인프로세스 ASGI를 사용했고, 수급 벤치마크도 이벤트 루프
안에서 화면 벤치마크의 네트워크 차단을 적용했다. 실제 Redis, Sentinel, 브라우저와 브로커는
측정에 포함되지 않는다. 화면의 `peer_warm`은 다른 API의 준비 호출을, 수급의
`collector_warm`은 별도 collector 대역의 준비 호출을 재현한다.

| 대표 요청과 상태 | p95 꺼짐 → 켜짐 (ms) | API 합성 원천 호출 꺼짐 → 켜짐 | Redis 적중 켜짐 |
| --- | ---: | ---: | ---: |
| 대시보드 첫 지수 요약 · peer_warm | 40.746 → 27.547 | 90 → 90 | 0 |
| 차트 · peer_warm | 60.296 → 28.013 | 60 → 30 | 30 |
| 시장판 · peer_warm | 93.744 → 56.180 | 360 → 360 | 120 |
| 현물 수급 · collector_warm | 37.248 → 33.862 | 60 → 0 | 30 |
| 선물 수급 · collector_warm | 36.377 → 19.026 | 120 → 0 | 30 |

두 벤치마크의 `api_warm`에서는 원천 호출이 0이었다. 다른 API의 캐시를 재사용할 때
차트의 오실레이터는 원천 호출이 줄었지만 지수 요약과 시장판 전체에서 줄지는 않았다.
#883·#844와 비교하면 같은 합성 조건에서 호출 수의 방향과 적중 경계는 재현됐다.
p95의 크기는 로컬 스케줄링에 따라 달라지고 시장판의 지지·저항 요청 수, 브라우저 지연과
운영 트래픽의 혼합이 같다는 근거는 없다.

[#883 화면 벤치마크](screen-cache-benchmark.md) · [#844 수급 비교](https://github.com/shindonggeun/trading-desk/pull/844)

## 운영에서 남은 증거

이번 세션에서 승인된 운영 인증 연결을 확인하지 못해 신규 운영 응답을 채집하지 않았다.
운영 담당자가 기존 인증 읽기 경로에서 남길 수 있는 증거는 아래와 같다.

1. 기존 배포 기록에서 운영 SHA와 역할을 대조한다. 실제 KRX 정규장(평시 09:00–15:30 KST)의
   기존 트래픽 구간 시작·끝에
   `GET /api/status`의 `now`, `role`, `outbound.since`, `toss`, `kis`를 보존한다.
   동일 API 프로세스이고 재시작이 없으며 카운터가 단조 증가한 장중 표본의 차이만 사용한다.
   유효한 `outbound` 안의 미등장 제공자 키는 0이고, 블록 자체가 없으면 미측정이다.
   `outbound`는 계측된 REST 요청 횟수로 재시도도 포함한다. 토큰 발급, WebSocket과
   원천별·화면별 요청 분리는 포함하지 않는다. 트래픽이 없던 구간의 0이나 장외 관측은
   장중 원천 호출 감소의 근거가 되지 않는다.
2. 같은 상태 응답의 `shared_cache`에서 `on`, `down_sec`, `hits`, `misses`, `writes`,
   `rejected`, `errors`와 `market_stream`의 `mode`, `subscribed`, `degraded`, 수신·오류
   집계를 대조한다. 구독 상태와 실제 이벤트 수신은 각각 확인한다. 프로세스 총 적중 수는
   개별 경로의 적중을 증명하지 않는다. 경로별 유효값은 기존 공개 응답·승인된 집계에서
   collector 확인 시각과 API 표시 시각을 대조한다. Redis 키나 값, 개인 알림 payload는
   수집 범위에서 제외한다.
3. `GET /api/perf/timings`의 기존 `since`, 라우트별 `count` 및 p95를 읽는다. 라우트당
   최근 최대 500건의 앱 처리시간이고 SSE는 제외된다. `since`만으로 각 라우트 표본 기간을
   확정할 수 없으며 두 p95를 빼서 구간 p95를 얻을 수 없다. 같은 라우트·요일·장중 시간대와
   요청 규모·표본 수가 확인된 전환 전후 기록에서만 비교한다. 합성 요청 묶음 p95와는 비교할
   수 없다. 이번 점검에는 초기화 쿼리, 비교용 운영 부하나 별도 SSE 구독을 쓰지 않는다.

동일 조건의 운영 전후 p95, 장중 호출량, 경로별 실제 적중은 미확인 상태다.
