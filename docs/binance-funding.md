# 바이낸스 USDⓈ-M 펀딩비 이력 (#912)

기존 세션으로 `GET /api/positioning/funding?symbol=BTCUSDT&days=30`을 조회한다. `symbol`은 아래 목록에서 선택하고
`days`는 1~30의 정수로, 생략하면 30이다. 허용되지 않은 입력은 422, 비로그인은 기존 세션 정책에 따라 401이다.

| 심볼 | 기초자산 | 종류 |
| --- | --- | --- |
| BTCUSDT | 비트코인 | 암호화폐 |
| ETHUSDT | 이더리움 | 암호화폐 |
| XRPUSDT | XRP | 암호화폐 |
| SOLUSDT | 솔라나 | 암호화폐 |
| SAMSUNGUSDT | 삼성전자(005930) | 주식 |
| SKHYNIXUSDT | SK하이닉스(000660) | 주식 |
| NVDAUSDT | 엔비디아 | 주식 |
| AMDUSDT | AMD | 주식 |
| MUUSDT | 마이크론 | 주식 |
| SOXXUSDT | iShares Semiconductor ETF | ETF |

## 원천과 상태

- `exchangeInfo`에서 계약 유형·기초자산·USDT 표시 및 정산·거래 상태를 확인한다. 암호화폐는 `PERPETUAL`,
  표의 주식·ETF는 `TRADIFI_PERPETUAL`이다. 계약이 없거나 지원 조건이 다르면 `unsupported`, 거래 상태가
  `TRADING`이 아니면 `inactive`, 조회가 성공했으나 결제 이력이 없으면 `no_data`다.
- `fundingRate`의 확정 결제만 쓰고 마크 가격은 해당 결제 행의 `markPrice`를 사용한다. 결제 전 예상율은 이력에
  넣지 않는다. 일반 결제와 주식 배당 추가 결제는 서로 다른 유형이며, 둘이 같은 시각에 올 수 있다.
- 펀딩비와 합계·평균은 비율이다. `-0.0001`은 `-0.01%`다. 음수면 해당 결제에서 숏 보유자가 롱 보유자에게 지급한다.
  실제 포지션, 현물 공매도 잔고 및 미래 가격 방향은 이 값으로 알 수 없다.

## UTC 응답

| 필드 | 의미 |
| --- | --- |
| `source`, `fetched_at`, `status`, `reason` | 원천 `binance`, 마지막 원천 조회 시각, 응답 상태와 원인. 캐시 재사용 때 원래 조회 시각을 유지한다. |
| `underlying`, `contract_status`, `funding_interval_hours` | 자산의 이름·종류·티커, 공급자 거래 상태, 현재 결제 간격. |
| `latest_settlement`, `settlements` | 내부 이력의 최신 확정 결제 및 표시 기간의 확정 결제 목록. `settled_at`, `rate`, `mark_price`(USDT, 결측 가능), `rate_type`(`regular` 또는 `special`). 목록은 과거순이다. |
| `daily` | 완료된 UTC 날짜를 과거순으로 표시한다. 각 행의 `date`, `status`, `settlement_count`, `total_rate`, `average_7d`를 사용한다. |
| `current_day` | 현재 UTC 날짜의 확정분만 포함하며 `status`는 `partial`이다. 아직 받은 결제가 없으면 합계는 `null`이다. |
| `negative_streak_days`, `negative_streak_truncated` | 어제부터 완전한 음수 일합계를 연속으로 센다. 0·양수·불완전한 날짜에서 끊는다. 조회한 내부 이력 끝까지 연속이면 잘림을 표시한다. |

`fundingInfo`는 현재 조정 상태이며 과거 일정 변경표를 제공하지 않는다. 일별 합계는 실제 UTC 결제일에 맞춰
누적하며, 명확하게 확인되는 정규 결제 슬롯이 모두 있는 날짜만 `complete`다. 혼합 주기·결제 누락·상장 첫날의
불완전한 경우에는 `partial` 또는 `missing`과 `total_rate: null`을 돌려준다. 주식 배당 추가 결제는 날짜 합계에
포함하되 정규 결제 슬롯을 채우지 않는다. `average_7d`는 해당 날을 포함한 연속 7 UTC 날짜 모두가
`complete`일 때만 일별 합계의 이동평균을 표시한다. 오늘은 평균과 연속 음수 일수에서 제외한다.

원천 오류·허용량 제한·결제 지연은 조회 상태로 구분한다. 마지막 정상값을 보존할 수 있으면 원래 조회 시각과 `stale`을
표시한다. 사용할 이전 값이 없으면 `source_error`이며 가격과 결제율을 추정하지 않는다. 최근 정규 결제 후 현재
결제 간격에 15분 유예를 더한 시간이 지나면 `stale`과 `settlement_delayed`를 반환한다.

계약별 원천 결과는 60초 동안 재사용한다. Redis가 설정된 환경에서는 캐시를 공유하고, Redis에 연결할 수 없으면
API 프로세스 메모리에서 읽는다. 같은 프로세스에 동시에 들어온 동일 계약 조회는 기간이 달라도 원천 요청을
공유한다. 장애 시에는 최대 6시간 전의 조회 결과를 사용하며, 내부 집계에는 표시 기간보다 긴 37일의
완료일을 조회한다. 7일 표시에서도 표시 시작일 이전 유효 일자가 이동평균이나 연속 일수에 반영될 수 있다.

## 공개 표본 확인

2026-11-24 05:49:24.041 UTC에 개발 PC에서 `exchangeInfo`, `fundingInfo`, 계약당 `fundingRate` 1회로
2026-11-17 00:00 UTC부터 조회 시각까지의 공개 응답을 확인했다. 12회 모두 HTTP 200이었다. 초기 10개 계약은
모두 `TRADING`이었고 유형·기초자산·USDT 표시 및 증거금 자산이 허용 조건과 일치했다. 삼성전자·SK하이닉스는
4시간, 나머지 8개는 8시간이었으며, 실제 정규 결제 이력에서도 같은 간격을 확인했다. 결제 260건은 모두
`rateType=Regular`이고 `markPrice`가 있었다. `fundingTime`은 확정 결제의 UTC epoch 밀리초다. 예를 들어
삼성전자의 11월 21일 첫 결제는 `00:00:00.001Z`에 기록됐다. 이 값을 그대로 보존하고 21일 UTC 합계에 넣는다.
[바이낸스 USDⓈ-M 시장 데이터](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)

| 계약 | 현재 간격(시간) | 확정 결제 건수 | 양수 / 음수 / 0 |
| --- | ---: | ---: | ---: |
| BTCUSDT | 8 | 22 | 17 / 5 / 0 |
| ETHUSDT | 8 | 22 | 13 / 9 / 0 |
| XRPUSDT | 8 | 22 | 13 / 9 / 0 |
| SOLUSDT | 8 | 22 | 10 / 12 / 0 |
| SAMSUNGUSDT | 4 | 44 | 15 / 10 / 19 |
| SKHYNIXUSDT | 4 | 44 | 16 / 12 / 16 |
| NVDAUSDT | 8 | 22 | 14 / 0 / 8 |
| AMDUSDT | 8 | 22 | 4 / 0 / 18 |
| MUUSDT | 8 | 22 | 15 / 0 / 7 |
| SOXXUSDT | 8 | 18 | 5 / 0 / 13 |

### UTC 이틀 경계와 표시 조건

공식 응답을 로컬 서비스에 재생하면서 조회 시각을 아래처럼 고정했다. 표의 숫자는 원천의 비율 단위이며
화면은 100을 곱해 백분율로 표시한다. 예를 들어 XRP의 미완료 합계 `-0.00012757`은 `-0.012757%`다.
양수 결제는 롱에서 숏으로, 음수 결제는 숏에서 롱으로 지급된다.
[바이낸스 펀딩비 안내](https://www.binance.com/en/support/faq/detail/360033525031)

| 계약 · 재생 시각 (UTC, 2026년) | 직전 UTC 날짜 두 개의 상태와 합계 | 당일 확정분 | 연속 음수 표시 |
| --- | --- | --- | --- |
| SAMSUNGUSDT · 11-21 04:10 | 11-19 완료 `-0.00095282`, 11-20 완료 `-0.00250968` | 미완료 2건 `-0.00009663` | 2일. 11-18 완료일은 양수다. |
| SOXXUSDT · 11-19 00:10 | 11-17 결측 0건, 11-18 상장 첫날 미완료 2건. 두 날 모두 합계 `null`. | 미완료 1건 `0` | 산정 불가 |
| AMDUSDT · 11-23 00:10 | 11-21 완료 `0.00029257`, 11-22 완료 `0` | 미완료 1건 `0.00002257` | 없음. 11-22의 실제 0은 표에 표시한다. |
| XRPUSDT · 11-24 05:49:24.041 | 11-22 완료 `0.00013559`, 11-23 완료 `0.00014358` | 미완료 1건 `-0.00012757` | 없음. 당일 음수는 연속 일수에서 제외한다. |

시장 수급의 펀딩비 화면은 7일·30일을 선택할 수 있다. 완료일 차트에서 불완전한 날짜의 합계는 비워 두고
오늘 확정분은 별도 영역에 표시한다. 최신 결제의 마크 가격이 있으면 USDT로 표시한다.
`available`, `stale`, `no_data` 응답에서는 이력 영역을 표시하고, `unsupported`, `inactive`,
`source_error`에서는 상태 안내를 표시한다. [화면 구현](../frontend/src/components/FundingRatePanel.tsx)

### 재현과 환경 범위

아래 PowerShell 예제는 인증 없이 표본과 동일한 UTC 범위를 질의한다. `fundingRate`는 시작·종료 시각을
모두 포함한다. 공개 요청이 실패하면 예제를 종료한다. 실패 기록에는 조회 시각, HTTP 상태와 응답에 제공된
`Retry-After`를 남긴다.

```powershell
$hostUrl = 'https://fapi.binance.com/fapi/v1'
$start = ([DateTimeOffset]'2026-11-17T00:00:00Z').ToUnixTimeMilliseconds()
$end = ([DateTimeOffset]'2026-11-24T05:49:24.041Z').ToUnixTimeMilliseconds()
$symbols = 'BTCUSDT ETHUSDT XRPUSDT SOLUSDT SAMSUNGUSDT SKHYNIXUSDT NVDAUSDT AMDUSDT MUUSDT SOXXUSDT'.Split()
$exchange = Invoke-RestMethod "$hostUrl/exchangeInfo" -TimeoutSec 7 -ErrorAction Stop
$funding = Invoke-RestMethod "$hostUrl/fundingInfo" -TimeoutSec 7 -ErrorAction Stop
$history = @{}
foreach ($symbol in $symbols) {
    $history[$symbol] = Invoke-RestMethod (
        "$hostUrl/fundingRate?symbol=$symbol&startTime=$start&endTime=$end&limit=200"
    ) -TimeoutSec 7 -ErrorAction Stop
}
```

- 개발 PC에서는 위의 고정 표본과 별도로 제품의 HTTPS 클라이언트로 `BTCUSDT`를 조회해 `available` 응답을 확인했다.
  #924와 #931의 CI 및 브라우저 검증은 합성 응답을 사용했다. 홈랩 운영 컨테이너에서 공개 원천에 도달하는지는 미확인이다.
- 이번 공개 조회에서 429나 지역 차단은 관측되지 않았다. 429 또는 418이면 `rate_limited`로 처리하고
  대기 시간 안에는 원천을 다시 호출하지 않는다. 예를 들어 공개 호스트가 지역 제한으로 451을 응답하면
  `upstream_error`로 처리한다. 지역 제한 시 그 환경의 조회를 중단한다. 두 경우 모두 남아 있는 정상값은
  `stale`, 정상값이 없으면 `source_error`로 표시한다. 429·451의 캐시 없는 응답은 합성으로 대조했다.
