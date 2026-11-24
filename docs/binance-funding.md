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

2026-11-24 로컬에서 전용 HTTPS 호스트의 `exchangeInfo`·`fundingInfo` 및 10개 계약의 `fundingRate?limit=12`를
확인했다. 모두 HTTP 200이었고, 계약 모두 `TRADING`이었다. 10개 모두 12개 결제 행에 마크 가격 필드가 있었다.
삼성전자·SK하이닉스는 현재 4시간 조정, 나머지 8개는 8시간 조정과 같은 간격을 표본에서 확인했다. 결제 시각에는
정시부터 수 밀리초 차이가 있는 행이 있었다. 테스트 및 CI는 합성 응답으로 검증하며 운영 컨테이너의 도달 여부는
별도 확인 범위다.

[바이낸스 USDⓈ-M 시장 데이터](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)
