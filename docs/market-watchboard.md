# 시장 모니터 조회 (#921)

`GET /api/market/watchboard?group=semiconductors&days=30`은 기존 세션 인증이 필요한 읽기
경로다. 관심군은 `semiconductors`, `crypto`, `gold_miners`이며 기간은 1~90일이다.
기간은 UTC 관찰일을 포함한 달력 일수다. 일봉 목록, 최근 가격과 비교 이력을 한 응답으로 받는다.

## 상품과 식별자

| Yahoo 기호 | 상품 | 시장 | 식별 근거 |
| --- | --- | --- | --- |
| `^KS11` | 코스피 지수 | KRX | [KRX 코스피 시장](https://global.krx.co.kr/contents/GLB/02/0201/0201010100/GLB0201010100.jsp) |
| `NQ=F` | 나스닥100 연속 선물 | CME | [CME NQ 상품 명세](https://www.cmegroup.com/markets/equities/nasdaq/e-mini-nasdaq-100.html) |
| `^SOX` | 필라델피아 반도체 지수 | 미국 | [Nasdaq SOX](https://indexes.nasdaq.com/Index/Overview/sox) |
| `^N225` | 닛케이225 | 도쿄 | [Nikkei 지수 정보](https://indexes.nikkei.co.jp/en/nkave/index/profile?idx=nk225) |
| `^TWII` | 대만 가권지수 | TWSE | [TWSE 지수 계열](https://www.twse.com.tw/en/indices/indices/series.html) |
| `005930.KS` | 삼성전자 보통주 | KRX | [삼성전자 상장 정보](https://www.samsung.com/global/ir/stock-information/listing-Info/) |
| `000660.KS` | SK하이닉스 보통주 | KRX | [SK하이닉스 주식 정보](https://www.skhynix.com/ir/UI-FR-IR03/) |
| `NVDA` | 엔비디아 보통주 | 미국 | [NVIDIA 상장 기호](https://investor.nvidia.com/investor-resources/faqs/default.aspx) |
| `285A.T` | 키옥시아 현지 본주 | 도쿄 | [키옥시아 주가 정보](https://www.kioxia-holdings.com/en-jp/ir.html) |
| `2330.TW`, `TSM` | TSMC 현지 본주 및 미국 ADR | TWSE, 미국 | [TSMC 상장 거래소와 기호](https://investor.tsmc.com/english/faq) |

종목은 관련주로 소개하며 지수 편입을 주장하지 않는다. TSM ADR은 비교 지수 밖의 참고 상품이다.
도쿄·대만 기호는 공급자 화면과도 대조했다. [Yahoo 키옥시아](https://finance.yahoo.com/quote/285A.T/) ·
[Yahoo TAIEX](https://finance.yahoo.com/chart/%5ETWII/) · [Yahoo TSMC 본주](https://finance.yahoo.com/quote/2330.TW/)

코인 관심군은 Yahoo의 현물 표시 `BTC-USD`, `ETH-USD`를 사용한다. 금광주는 ETF
`GDX`와 금광기업 `NEM`, `AEM`을 사용한다. 선물·CFD·야간 추정가를 현지 종가로
대체하지 않는다.

## 응답 계약

- 최상위 `group`, `days`, `as_of`, `items`, `comparison`. `as_of`는 서버의 UTC 관찰 시각이다.
- 각 항목은 `ticker`, `name`, `market`, `timezone`, `currency`, `unit`, `kind`,
  `relation`, `chart_href`로 상품을 식별한다. `kind`는 `index`, `future`,
  `stock`, `adr`, `crypto`, `etf`이고 `relation`은 `benchmark`, `related`, `reference`다.
- `market_state`는 `open`, `break`, `closed`, `holiday`, `unknown`이다.
  `status`는 `available`, `delayed`, `missing`이며 `reason`으로 사유를 제공한다.
  `source`는 정상 판독한 원천, `fetched_at`은 수신 시각, `source_delay_min`은 완료 판단에
  적용한 원천 지연 분이다. 사유는 `source_error`, `wrong_instrument`, `source_unverified`,
  `timestamp_unverified`, `no_completed_bar`, `session_unverified`, `latest_session_missing`이다.
- `latest`와 `history[]`는 현지 `trade_date`, 원천 봉 라벨 `at`, 예정 세션 종료 `closed_at`,
  `close`를 제공한다. `at`과 `closed_at`은 UTC다. `change_pct`는 직전 기대
  거래일 봉이 있을 때만 산출하며 값이 없으면 `null`이다.
- `comparison`은 `status`, `base_date`, `series[]`, `excluded[]`를 반환한다. 각 계열은
  `ticker`와 `points[]`의 `trade_date`, `return_pct`를 갖는다. 비교하는 상품들이 기간 안에
  모두 관측된 첫 날짜의 종가에 맞춰 `(close/base_close-1)×100`으로 표시한다. 기준일의 0%는
  정규화 결과다. 기준은 같은 현지 거래일 표기이며 지역별 장 마감 시각은 서로 다르다.
  거래가 없거나 결측인 날짜는 생략한다. 수익률은 각 상품의 현지 통화 가격에서 산출한다.

한국·미국은 기존 달력을 사용하고 도쿄·대만은 설치된 거래소 휴일표를 참조한다. 도쿄 정규장은
09:00~11:30·12:30~15:30, 대만은 09:00~13:30이다. 대만의 13:33 종료 예외를 감안해 봉의
완료 인정에 3분의 여유를 더 둔다. CME 정규 세션은 뉴욕 시간 전날
18:00~거래일 17:00이며 오후 재개장 뒤의 다음 거래일을 구분한다. CME 특별 일정의
마감이 불확실하면 그 세션을 완료로 산정하지 않는다. 코인은 UTC 하루 단위다.
[JPX 거래 시간](https://www.jpx.co.jp/english/equities/trading/domestic/01.html) ·
[TWSE 정규장과 마감 예외](https://www.twse.com.tw/en/products/system/trading.html) ·
[CME 특별 일정](https://www.cmegroup.com/trading-hours.html)

런타임은 기존 PriceSource의 Yahoo 일봉과 일괄 조회 게이트를 사용한다. 공급자가 공표한 지연을
토대로 보수 대기값을 적용하며 공급자 내부 갱신 시각을 확인하는 표식은 별도로 제공되지 않는다.
Yahoo 가격 표본은 데이터 식별과 시간 표기의 대조 자료다. 공급자의 데이터 사용·재배포 조건은
해당 원천의 안내를 따른다. [Yahoo Finance 원천과 지연](https://help.yahoo.com/kb/finance/article-exchanges-data-delays-sln2310.html)

## 공개 표본 대조

2026-11-24에 공개 발행사·거래소 및 기존 시세 공급자의 예시를 확인했다. 아래 시점은
각 원천이 표시한 거래일 또는 현지 마감 기준이다. 정규 마감과 게시 시각은 구분한다.

| 종목 | 거래일과 관찰 기준 | 공개 값 | 근거 |
| --- | --- | --- | --- |
| 코스피 | 2026-11-20 KRX 일봉 | 4,869.96 포인트 | [Yahoo 코스피 일봉](https://finance.yahoo.com/quote/%5EKS11/history/?frequency=1d) |
| NQ=F | 2026-11-20 CME 일봉 | 25,126.50 포인트 | [Yahoo NQ 일봉](https://finance.yahoo.com/quote/NQ%3DF/history/?p=NQ%3DF) |
| SOX | 2026-11-23 Nasdaq 게시 기준일, 시각 미표기 | 9,588.05 포인트 | [Nasdaq SOX](https://indexes.nasdaq.com/Index/Overview/sox) |
| 닛케이225 | 2026-11-20 도쿄 종가 | 56,436.65 포인트 | [Nikkei 일별 자료](https://indexes.nikkei.co.jp/en/nkave/archives/summary?dt=20261120&idx=nk225) |
| TAIEX | 2026-11-23 대만 종가 | 36,811.89 포인트 | [TWSE 월간 TAIEX 종가](https://www.twse.com.tw/en/indicesReport/MI_5MINS_HIST?date=202611&response=html) |
| 키옥시아 | 2026-11-20 15:30 JST 현지 종가 | 7,307 JPY | [키옥시아 IR](https://www.kioxia-holdings.com/en-jp/ir.html) |
| TSMC 본주 | 2026-11-23 대만 거래소 일봉 | 2,030 TWD | [TWSE 2330 일별 거래](https://www.twse.com.tw/rwd/en/afterTrading/STOCK_DAY?date=20261101&response=json&stockNo=2330) |

NQ 공개 표에 재개장 뒤 같은 날짜로 표시된 두 행도 관찰했다. 런타임은 기존 PriceSource의 세션
교정 이후에도 거래일을 판별할 수 없는 행을 제외한다. 공개 표본은 운영 앱의 실시간 가용성
검증과 별개다. 일부 공급자 직접 페이지는 429로 응답해, 위 표는 접근 가능한 공개 색인과 거래소·
발행사 자료를 사용했다. 자동 회귀는 고정 합성 응답과 세션 시계로 수행한다.
