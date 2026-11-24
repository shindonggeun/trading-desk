# 일봉 마감과 저항 반응 (#925)

`GET /api/chart-read`는 요청 시간봉에 `1d`가 포함되면 지원 상품에 선택 필드
`daily_reaction`을 붙인다. 기존 시간봉, 합성 요약, 중·장기 관찰은 그대로 제공한다. 같은
차트 조회에서 받은 일봉을 사용하고, 다른 상품의 캔들·호가를 대신 사용하지 않는다.

| 종목 | 상품 | 세션과 단위 |
| --- | --- | --- |
| `SOXX` | 반도체 ETF | 미국 정규장 · USD |
| `SOXL` | 반도체 레버리지 ETF | 미국 정규장 · USD |
| `^NDX` | 나스닥100 현물 지수 | 미국 정규장 · 지수 포인트 |
| `NQ=F` | 나스닥100 연속 선물 | CME Globex · 지수 포인트 |

SOXLUSDT 무기한 계약과 NAS100 참고 CFD는 별도 상품이다. 지원 예시로 입력해도
`product_unsupported`를 반환한다. 나머지 종목과 일봉 없는 요청에는 필드가 없다.

## 화면이 읽는 값

- `status`는 `confirmed`, `in_progress`, `unavailable` 중 하나다. `reason`과
  nullable `session`, `bar`, `previous_close`, `resistance`, `metrics`, `reaction`,
  `volume`을 함께 읽는다. `observed_at`은 조회 기준 UTC 시각, `fetched_at`은 일봉을 받은
  시각, `source`는 해당 일봉의 원천이다.
- `instrument.kind`는 `etf`, `cash_index`, `continuous_future`다. 가격 단위는
  `instrument.unit`에서 가져온다. `session`에는 `market`, `trade_date`,
  `opens_at`, `closes_at`, `confirm_after`가 있다. 세션 시각은 UTC다.
- `bar`의 `at`은 차트가 받은 봉의 앵커이며 OHLC는 원천 단위다.
  `previous_close`는 `at`과 `price`를 반환한다.
  `resistance`는 `kind`, `price`, `basis_at`, `projected_at`, `ref`를 반환한다.
  `ref`는 채널 상단의 `id`·`edge` 또는 스윙 고점의 `at`·`side`를 가리킨다.
  현재 차트에 정확히 일치하는 선이 없으면 `projected_at`과 `price`를 함께 표시한다.
- `reaction.kind`는 `close_breakout`, `wick_rejection`, `strong_bullish_close`,
  `resistance_touch`, `resistance_near`, `none`이며 근접·접촉·윗꼬리·강한 양봉·돌파의
  개별 불리언도 있다. 미확정 봉은 관측값을 표시할 수 있어도 `reaction: null`이다.

## 세션과 비교 시점

미국 주식일의 휴장과 단축장을 반영한다. CME는 뉴욕 시간 기준 전날 18시부터 거래일 17시까지의
정규 세션을 적용하며, 휴일·단축장이 의심되어 기존 달력으로 마감을 확정할 수 없는 경우
`session_unverified`를 반환한다. 거래일이 아닌 날은 직전 정상 세션까지 확인한다.
[CME 휴일·거래 일정](https://www.cmegroup.com/trading-hours.html)

원천은 현재 차트에 들어오는 Yahoo 일봉이다. 마감 뒤 미국 종목은 15분, CME는 10분을 기다린다.
그 전에 받은 캐시와 원천의 뉴욕 자정 거래일 라벨에 맞지 않는 봉은 종가 반응에서 제외한다.
현재 세션, 마감 후 확인 대기, 확인 가능 구간은 서로 다른 차트 응답을 사용한다.
마감 시각은 거래소 세션 기준이고 수신 시각은 로컬 관측이다.
원천의 별도 최종화 시각은 제공되지 않는다.
[Yahoo Finance 거래소별 원천 지연](https://help.yahoo.com/kb/finance/article-exchanges-data-delays-sln2310.html)

저항은 관찰할 봉의 직전 확정 세션까지의 같은 일봉으로 계산한다. 직전 종가 위에서 기존
차트의 거리 제한을 만족하는 채널 상단을 먼저 관찰 봉 시점으로 한 칸 투영한다. 채널 근거가
없으면 종가로 넘지 않았던 최근 스윙 고점을 본다. 관찰 봉이
저항선을 만드는 입력에 들어가지 않는다. 뉴스와 사용자 메모는 이 API에서 수집하지 않는다.
별도의 메모나 뉴스가 있어도 출처와 관찰 시각을 가격 근거와 분리한다.

## 관찰 판정

몸통 비중은 `abs(close-open)/(high-low)`, 윗꼬리 비중은
`(high-max(open,close))/(high-low)`, 종가 위치는 `(close-low)/(high-low)`다.
`body_atr`는 몸통을 직전까지의 ATR로 나눈 값이며, 저항 대비 종가 거리는
`(close/resistance-1)*100`이다. 판정은 한 봉에 대한 관찰이다.

| 판정 | 조건 |
| --- | --- |
| 근접 | 고가가 저항 아래 0.5 ATR에 이르고 저가가 저항 위 0.35 ATR 이하 |
| 접촉 | 봉의 범위가 저항 ±0.35 ATR 구간과 겹침 |
| 윗꼬리 밀림 | 접촉했고 윗꼬리가 범위의 40% 이상, 종가가 구간 아래 |
| 강한 양봉 | 양봉 몸통이 범위의 60% 이상, 종가가 범위 상단 25% 안, 몸통이 직전 ATR의 0.8배 이상 |
| 종가 돌파 | 종가가 저항 위 0.3 ATR을 넘음 |

대표 판정은 돌파, 윗꼬리 밀림, 강한 양봉, 접촉, 근접 순으로 선택한다. 조건이 겹치면
개별 불리언에 함께 남긴다. ETF 거래량은 관찰일과 직전 20거래일이 모두 정규 길이이고 양수
거래량이 있을 때만 비교한다. 지수와 연속 선물에는 거래량 비율을 제공하지 않는다.

`session_open`과 `awaiting_confirmation`은 진행 중을 뜻한다. 그 밖에
`session_unverified`, `missing_daily_source`, `source_unverified`,
`confirmation_unverified`, `source_delayed`, `product_mismatch`, `invalid_bar`,
`bar_missing`, `previous_bar_missing`, `resistance_unavailable`, `product_unsupported`,
`reaction_error`를 결측·판정 불가 사유로 사용한다. `bar`가 있고 `reaction`이 비어 있으면
봉을 표시하면서 판정 사유를 함께 보여 줄 수 있다.
