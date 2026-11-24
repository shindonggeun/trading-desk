# SOXL·KORU 극단 공포 가설 검증 (#919)

미국 정규장이 끝난 뒤 같은 미국 거래일의 ETF Wilder RSI(14) ≤30, CNN 주식 Fear & Greed ≤10,
Cboe VIX 종가 ≥30이 모두 확정됐는지 평가한다. 캡처 속 RSI의 종목·봉 주기·진입 시점은 확인되지 않아
ETF별 미국 일봉을 기본 가설로 고정했다. KORU에는 SOXL의 RSI를 공통 적용하는 가설도 별도로 집계한다.
임계값은 모든 기간에서 동일하다. 이슈에는 캡처의 신호일과 원본 값이 기록돼 있지 않아 일치 여부는 미검증이다.
RSI 경계에는 공용 Wilder RSI가 반환하는 소수 둘째 자리 값을 사용한다.

## 원천과 공표 시각

| 입력 | 검증 조건 | 확인한 범위 |
| --- | --- | --- |
| SOXL·KORU | 하나의 가격 기준으로 전진 조정한 O/H/L/C와 자료 버전. 분할·분배금이 효력을 갖고 확인된 때부터 해당 거래일과 이후 봉에만 동일 조정 계수를 적용하고 이전 값은 유지한다. | Direxion의 SOXL·KORU 상장일과 KORU 2025-02-10 역분할을 확인했다. 앱에서 이 가설에 재사용할 수 있는 조정 OHLC 보관본은 확인되지 않았다. |
| CNN | 미국 거래일, 해당 거래일의 첫 확인 가능한 확정 점수 공표 시각과 정정 이력. | #462는 현재 점수·시각과 전일·전주 요약을 읽는다. 과거 그래프의 일자나 조회 시각만으로 당시 공표 시각 또는 최초 값을 확인할 수 없다. |
| VIX | 같은 미국 거래일의 Cboe 종가와 최초 가용 시각. 날짜만 있는 파일에서 시각을 임의로 채우지 않는다. | Cboe의 정규장 산출·전송은 통상 16:15 ET 부근까지이며 조기 마감은 별도다. 현재 #462 Yahoo 일봉의 봉 날짜는 확정 종가 발행 시각을 뜻하지 않는다. |
| 미국 거래 일정 | 실제 정규장 시가·종가 및 휴장·조기 마감이 반영된 전 거래일 목록. | 요청 시 최신 완료일 표시는 기존 `market_calendar`를 사용한다. 과거 리포트 생성에는 버전이 명시된 거래 일정 입력을 사용한다. |

공개 열람과 앱 재사용·배포는 별도다. 이 작업에서 제3자 제공 원본이나 실제 성과를 패키지에 포함할 수 있는
사용 근거를 확인하지 못했다. 현재 리포트는 미검증이며 기본 API는 빈 과거 신호·전략과 조건별 결측 사유를
반환한다. #462 캐시에서 보이는 최신 관측은 `observations`에 시각·원천과 함께 표시할 수 있고 조건 충족 수에는
포함하지 않는다. 과거 재현 입력이 승인되면 시간순으로 저장된 최초 확정 관측을 사용한다.
FRED의 VIXCLS도 Cboe 일별 종가이며, ALFRED 빈티지는 날짜 단위라 같은 날 장중 공표 시각을 복구할 수 없다. [FRED VIXCLS](https://fred.stlouisfed.org/series/VIXCLS/) · [ALFRED 실시간 기간](https://fred.stlouisfed.org/docs/api/fred/realtime_period.html).

[CNN Fear & Greed](https://www.cnn.com/markets/fear-and-greed),
[Cboe VIX 과거 자료](https://www.cboe.com/tradable-products/vix/vix-historical-data),
[Cboe VIX 방법론](https://cdn.cboe.com/resources/indices/governance/Volatility_Index_Methodology_Cboe_Volatility_Index.pdf),
[Cboe 자료 이용 조건](https://www.cboe.com/terms/),
[Direxion SOXL](https://www.direxion.com/product/daily-semiconductor-bull-bear-3x-etfs),
[Direxion KORU](https://www.direxion.com/product/daily-msci-south-korea-bull-3x-etf),
[KORU 역분할 공지](https://www.direxion.com/press-release/direxion-announces-reverse-split-of-koru),
[NYSE 정규장·휴장 일정](https://www.nyse.com/trade/hours-calendars)

## 과거 자료 확인 기록

2026-11-24 06:06 UTC에 Cboe가 공개한 일별 CSV를 읽었다. 파일 열은 `DATE, OPEN, HIGH, LOW, CLOSE`이며
1990-01-02~2026-11-23의 9,322행에 중복 날짜가 없었다. SOXL·KORU 설정일부터 이 파일의 마지막 날짜까지
잘라 얻은 범위는 다음과 같다. 구간의 Cboe 종가 행 수이며 ETF 가격과 실제로 겹치는 거래일 수는 확인하지 못했다.
VIX 30 이상 열에는 나머지 두 지표와 공표 시각을 대조할 수 없는 날짜도 포함돼 있다.
[Cboe 공개 VIX CSV](https://cdn-api.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv)

| 종목 | 설정일 | 해당 구간의 VIX 종가 행 | VIX 종가 ≥30 | 종가 공란 |
| --- | --- | ---: | ---: | ---: |
| SOXL | 2010-03-11 | 4,236 | 258 | 0 |
| KORU | 2013-04-10 | 3,461 | 160 | 0 |

같은 공개 파일로 재대조할 때 원본 바이트의 SHA-256을 먼저 확인한다. 위 조회본은
`409c4e80171f82285054875e0095aab5fd19b59670c987bc0e58f5919a69f3ee`다. 일별 갱신으로 해시가 달라질 수 있다.
`DATE`를 미국 거래일로 읽고 위 설정일과 2026-11-23 양끝을 포함해 행을 센 다음, `CLOSE`를 숫자로 읽어
30 이상인 행을 센다. 원본은 저장소나 API에 포함하지 않았다.

- VIX CSV의 날짜에는 최초 확정 공표 시각과 당시 정정 이력이 없다. 유료 EOD 계산 입력은 2022-05-09부터
  일반일 16:35~19:00 ET의 배송 창을 명시한다. 별도 Main Channel EOD 파일은 다음 거래일 09:30 ET 직후
  배송된다고 명시한다. 각 상품의 배송 창으로 공개 CSV 행의 최초 가용 시각을 확정할 수 없다.
  유료 상품의 사용 조건을 충족하는 아카이브나 당시 수신 기록은 확인하지 못했다.
  [VIX EOD 계산 입력](https://datashop.cboe.com/vix-index-eod-calculation-inputs) ·
  [Main Channel EOD 요약](https://datashop.cboe.com/main-channel-end-of-day-summary)
- CNN은 #462에서 현재 점수의 시각과 전일·전주 요약을 읽는다. 같은 미국 거래일의 첫 확정값과 최초 공표 시각,
  이후 정정 이력, 연구 재사용 조건을 입증하는 과거 자료를 확인하지 못했다.
  [CNN Fear & Greed](https://www.cnn.com/markets/fear-and-greed)
- Direxion은 SOXL의 2021-03-02 15대 1 분할, KORU의 2025-02-10 1대 10 역분할과 2026-07-15
  20대 1 분할을 공지했다. 상품 페이지의 분배 내역과 NAV 재투자 성과는 확인할 수 있지만, 설정일부터 적용할
  시점별 기업행동과 확정 거래소 O/H/L/C의 사용 가능 시각을 이어 붙일 수 있는 아카이브는 확인하지 못했다.
  기존 Yahoo 경로의 `auto_adjust=False` 봉도 이 연구의 전진 조정 입력을 보증하지 않는다.
  [SOXL 분할 공지](https://www.direxion.com/uploads/Direxion-Announces-Forward-and-Reverse-Splits-of-Five-ETFs.pdf) ·
  [KORU 역분할 공지](https://www.direxion.com/press-release/direxion-announces-reverse-split-of-koru) ·
  [KORU 분할 공지](https://www.direxion.com/press-release/direxion-to-split-nine-etfs)
- 캡처 제공일은 2026-11-24로 기록됐지만 개별 신호의 거래일·시각·RSI 대상과 봉 주기는 확인할 수 없다.
  직전 완료 미국 거래일인 11-23으로 해석하면 공개 VIX 종가 26.62는 기준 30을 충족하지 않는다.
  11-20 종가는 35.89로 VIX 기준을 충족하지만 그날의 확정 CNN 값 및 RSI와 캡처 시점을 대조할 수 없었다.
  11-24 미국 세션 종가는 조회 시점의 공개 파일에 없었다.
  [Cboe 공개 VIX CSV](https://cdn-api.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv)

따라서 이번에 세 자료를 모두 승인된 시간순 입력으로 확인한 구간은 SOXL·KORU 모두 없다.
실측 신호 수·승률·고정 기간 수익률·최대 불리 움직임 및 기준 전략 성과는 비교 불가로 남긴다.
표본이 감지된 뒤 252거래일이 경과했는지 확인할 실측 조합도 없으며, API의 미검증 기본 상태를 유지한다.

## 성과 계산

- 세 확정 항목 중 마지막 항목을 알게 된 후 첫 정규장 시가에 진입한다. 거래일은 NYSE 날짜로 맞추며
  휴장·결측에 값을 이전 날짜에서 채우지 않는다. ETF 상장 이전에는 진입하지 않는다.
- 진입일을 포함해 5·20·60·252번째 거래일의 종가로 청산한다. 진입·청산 편도 10 bps를 기본 적용한다.
  시가·종가의 호가 체결을 가정한다. 호가·가격이 없는 경우는 진입 또는 결과 미확정으로 집계한다.
- 최악의 불리 움직임은 진입~청산 중 장중 최저가와 진입가의 비용 전 비교다. 252일 내 장중 최고가는 전체
  252일이 지난 뒤의 별도 사후 참고치로 집계한다. 고정 청산 규칙의 이익 비율에 합산하지 않는다.
- 동일 조건 연속 구간은 한 에피소드로 센다. 같은 전략의 보유구간 겹침은 기간별로 제외한다.
  CNN과 VIX가 모두 검증된 동일 날짜 집합에서 각 ETF의 RSI 단독 조건과 단순 보유를 비교한다.
  두 기준 전략도 공표 시각 이후에 진입한다. 이익은 순수익률이 0보다 큰 성숙 거래의 수다.
- 분모·이익·손실·보합·중앙값·최악값과 이익 비율 Wilson 95% 구간을 제공한다. 표본이 없는 기간의 성과는
  `null`이다. 표본 간 시장 충격은 연관될 수 있으므로 구간은 투자 성과의 독립성이나 미래 승률을 보장하지 않는다.
  SOXL과 KORU는 일일 3배 목표 ETF라 장기 성과가 기초지수 누적 수익률의 세 배로 고정되지 않는다.

## 합성 재현 결과

`cd backend`에서 아래 테스트로 두 ETF에 같은 합성 가격 경로를 각각 적용한다. 고정 표본에서
세 조건을 30·10·30에 맞췄고 마지막 공표 시각 뒤 다음 정규장 시가 100에 진입했다. RSI 단독도 같은
시가에 진입했다. 단순 보유는 두 원천이 가용했던 하루 앞선 기준일을 사용해 시가 16에 진입했으며
뒤의 두 후보는 기간별 중복 보유로 제외했다. 아래 수익률은 편도 10 bps를 차감한 인위적 결과다.

| 전략 | 각 기간 성숙 거래 수 | 5일 | 20일 | 60일 | 252일 | 최악의 불리 움직임 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 세 조건 | 1 | -10.18% | -10.18% | -10.18% | -20.16% | -20.00% |
| RSI 단독 | 1 | -10.18% | -10.18% | -10.18% | -20.16% | -20.00% |
| 단순 보유 | 1 | +461.38% | +461.38% | +461.38% | +461.38% | 0.00% |

세 조건의 252일 내 장중 최고가 145로 계산한 사후 참고치는 +44.71%다. 같은 표본의 252일 고정 청산은
손실 1건으로 집계된다. 마지막 근거가 다음 거래일 시가와 동시에 공개되면 그 이후 첫 정규장 시가로 넘기는
휴장 경계, 미경과, 결측과 상장 전, SOXL 신호를 KORU에 적용하는 별도 가설도 고정 입력에서 검증한다.

```bash
python -m pytest -q tests/test_extreme_fear.py tests/test_extreme_fear_reports.py
```

## 오프라인 재실행과 API

`cd backend`에서 승인된 입력 파일을 지정한다. 승인 기록과 원본은 권한이 있는 저장 위치에 보관한다.

```bash
python -m desk.extreme_fear_reports research-input.json extreme_fear_soxl.json --symbol SOXL
python -m desk.extreme_fear_reports research-input.json extreme_fear_koru.json --symbol KORU
```

입력은 JSON `schema_version: 1`, `kind: approved|synthetic`, 오프셋을 포함한 `as_of`,
선택적 편도 `cost_bps`, `price_adjustment: causal_forward_total_return`, `sources`, `sessions`,
`prices`, `cnn`, `vix`다. `sources`의
`calendar/prices/cnn/vix` 각각에 `name/version/evidence/usage`를 기록한다. `approved`는 입력
사용자가 근거를 확인했다는 선언이다. 값의 출처나 이용 권한은 프로그램이 증명하지 않는다.

- `sessions[]`에는 미국 정규장의 `date/opens_at/closes_at`을 거래일 순서대로 넣는다.
- `prices.SOXL[]`과 `prices.KORU[]`에는 `date/open/high/low/close/available_at`을 넣는다.
  사후 일괄 소급 조정 파일이나 서로 다른 가격 기준의 빈티지를 이어 붙인 열은 사용할 수 없다.
- `cnn[]`과 `vix[]`에는 `date/value/available_at`을 넣는다. 실제 당시 처음 확정값을 사용할 수 있었던
  오프셋 포함 시각을 사용한다.

리포트는 입력 SHA-256, 규칙 SHA-256, 산출 기준 시각과 원천 버전을 포함한다. 같은 입력과 코드 버전으로
다시 생성하면 같은 결과가 나온다. 공개 가능한 승인 리포트만 기존 `desk/data/backtest/` 위치에 넣는다.
합성 산출물은 테스트에 사용하며 API는 무시한다.

세션 인증 `GET /api/research/extreme-fear?symbol=SOXL`에서 `SOXL` 또는 `KORU`를 지정한다.
`current`에는 최근 완료 거래일·충족 수·조건별 시각과 사유, `signal_history`에는 제한된 신호,
`strategies`에는 고정 기간 비교, `dataset`에는 자료의 범위와 버전이 들어간다. 조회는 오프라인 산출물과
기존 관측 캐시를 읽는다.
