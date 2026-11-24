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
[KORU 역분할 공지](https://www.direxion.com/press-release/direxion-announces-reverse-split-of-koru)

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
