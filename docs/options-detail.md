# 미장 옵션 만기별 분포 계약 (#887)

예정된 화면 연결은 `GET /api/positioning/options?ticker=NVDA`에서 유효 만기 목록과 가장 가까운 만기를
받는다. `expiration=YYYY-MM-DD`를 보내면 그 만기를 선택한다. `strike_min`과 `strike_max`는
표시할 행사가의 포함 범위다. 세션 인증은 기존 포지셔닝 API와 같다. [화면 이슈 #889](https://github.com/shindonggeun/trading-desk/issues/889)

## 출처와 기준 시각

기존 #154 요약은 Cboe 견적 JSON과 Yahoo 체인을 사용한다. 새 상세는 출처가 확인된 별도 원천을 주입받는다.
Yahoo Finance는 OPRA 옵션 시세의 명목 지연을 15분으로 안내하지만, yfinance 체인의 `lastTradeDate`는 개별 계약의
마지막 체결 시각이다. 거래량 집계와 OI의 기준 시각은 원천에서 받아야 한다. Yahoo 약관은 자동 수집에 사전 허가를
요구한다. 신규 상세 조회에 적용할 원천의 허가와 업체별 시각 계약은 별도 확인이 필요하다. [Yahoo 지연 안내](https://help.yahoo.com/kb/finance/article-exchanges-data-delays-sln2310.html) · [Yahoo 이용약관](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html) · [yfinance 체인 구현](https://github.com/ranaroussi/yfinance/blob/main/yfinance/ticker.py)

거래량의 기준일, OI의 기준일, 기초자산 가격의 기준 시각은 서로 다를 수 있다. Cboe DataShop은 일중 데이터의 OI에
전일 OCC 종료 값을 사용하는 사례를 설명한다. API는 원천이 준 값만 각 필드에 넣고, 미제공 값은 `null`로 둔다.
`fetched_at`은 Trading Desk 조회 시각이다. [Cboe DataShop FAQ](https://datashop.cboe.com/faqs)

## 상태와 집계

| 응답 상태 | 의미 |
|----------|------|
| `ok` | 선택 만기의 표준 계약 분포가 있다. |
| `unavailable` | 승인된 원천이 구성되지 않았거나 원천 조회가 실패했다. |
| `no_options` | 원천이 사용 가능한 만기 목록을 비어 있는 것으로 반환했다. |
| `invalid_expiration` | 요청한 만기가 유효 만기 목록에 없다. |
| `no_data` | 선택 만기에서 표시할 수 있는 계약이 없다. |

- `expirations`와 `expiration`은 만기 선택 목록과 현재 선택이다. `spot`은 기초자산 `price`, `as_of`,
  `delay_sec`을 제공한다.
- `volume`과 `oi`는 각각 선택한 만기의 풋·콜 합계와 `put_call_ratio`를 제공한다. 콜이 0이거나 한쪽 집계가
  결측이면 비율은 `null`이다. `trade_date`, `as_of`, `delay_sec`은 각 지표의 원천 메타데이터다.
- `strikes`는 행사가별 `call_volume`, `put_volume`, `call_oi`, `put_oi`다. `top`은 표시 한도를 적용하기 전
  요청 범위 전체에서 지표와 콜·풋별 상위 최대 3개의 `{strike,count}`다. 만기 전체 비율은 행사가 범위와 관계없이
  같은 값을 유지한다.
- `excluded_contracts`는 비표준, 표준 여부 미확인, 동일 계약 중복으로 제외한 행 수다. `range`는 표시 범위와 행 제한에
  따른 잘림을 전달한다. 집중 계약 수는 매수·매도 방향을 뜻하지 않는다.

만기 목록은 종목별, 표준 계약 집계는 종목·만기별로 180초 동안 보관한다. 같은 키의 동시 요청은 원천 조회
한 건을 공유한다. 원천 장애는 30초 동안 유지하며, 원천 동시 요청은 3건으로 제한한다.
응답은 가까운 만기부터 최대 128개와 선택 범위에서 최대 500개 행사가를 제공한다. 요청한 범위에 더 많은 행사가가
있으면 기초자산 가격에 가까운 행부터 표시하고 `range.truncated`를 설정한다.
