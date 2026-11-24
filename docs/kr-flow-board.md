# 국장 수급 현황판 (#916, #917)

## 원천 확인

| 원천 | 확인한 범위 | 연결 범위 |
| --- | --- | --- |
| 한국투자증권 | 종목별 투자자매매동향 일별 `FHPTJ04160001`의 `output2`에 영업일 `stck_bsop_date`, 종목 누적 거래대금 `acml_tr_pbmn`, 외국인 매수 `frgn_shnu_tr_pbmn`, 매도 `frgn_seln_tr_pbmn`가 정의돼 있다. 요청의 `J`는 KRX, `NX`는 NXT, `UN`은 통합이다. | 기존 저장소의 한국투자 수급은 시장 합계다. 종목별 승인 응답의 기준일·단위·집계와 제공 계정의 이용 범위는 개발 환경에서 확인하지 못했다. 종목별 요청과 키 발급을 추가하지 않는다. [한국투자 공식 요청 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/investor_trade_by_stock_daily/investor_trade_by_stock_daily.py) · [공식 응답 필드 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/investor_trade_by_stock_daily/chk_investor_trade_by_stock_daily.py) · [서비스 이용 안내](https://apiportal.koreainvestment.com/about-howto) |
| 토스증권 | 공개 시세 계약은 호가·가격·최근 체결·상하한가·캔들이다. 저장소에 있는 투자자 매매는 KOSPI/KOSDAQ 시장 단위다. | 종목별 외국인 매수·매도의 하루 거래대금으로 확인된 경로가 없다. 시장 합계만 별도 표시한다. [토스증권 시세 명세](https://developers.tossinvest.com/docs/market-data) |
| KRX 종목 시세 | 유가증권시장과 코스닥의 상장 주권 일별매매정보는 시장별 서비스다. 기존 KRX 수집 목록에는 지수, ETF 등 일곱 서비스가 있다. 두 종목 시세 서비스는 각각 이용 신청·승인이 필요하다. | 승인된 일별 주권 시세가 같은 시장·거래일의 전체 스냅샷으로 저장된 경우 거래대금 순위를 계산한다. 화면에 출처를 표시한다. [유가증권 일별매매정보](https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S2.cmd?BO_ID=JvJFzlAENzZlPBDNGAWC) · [코스닥 일별매매정보](https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S2.cmd?BO_ID=hZjGpkllgCBCWqeTsYFj) · [KRX API 이용약관](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO002.jsp) · [이용 신청](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO003.jsp) |
| KRX 외국인 보유 | 웹 통계는 지분율과 한도소진율을 별도 열과 순위 방식으로 제공한다. 보유수량·한도소진율은 장 개시 시 표시하는 전일 확정치다. | 승인된 자동 수집 경로가 확인되지 않아 웹 통계에 요청하지 않는다. 별도 보유 기준일과 공표 시각이 있는 지분율 계약을 제공한다. [KRX 보유 지분 통계](https://data.krx.co.kr/contents/MDC/EASY/ranking/MDCEASY021.jsp) · [KRX 데이터 수신과 이용](https://openapi.krx.co.kr/contents/OPP/DATA/OPPDATA003.jsp) |

KRX OPEN API 약관은 비상업적 이용, 화면의 원천 표시 및 제3자 정보 제공 제한을 명시한다. 제공 범위는
기존 인증 화면과 승인된 데이터 사용 범위 안에서 확인한다. 삼성전자와 SK하이닉스의 실측 원천 대조는
승인된 종목 응답과 보유 자료가 필요하다. 합성 자료는 라이브 데이터의 단위와 기준일 검증을 대체하지 않는다.

## 지표

- 외국인 참여율(%)은 `100 × (외국인 매수 거래대금 + 외국인 매도 거래대금) / (종목 거래대금 × 2)`다.
  종목 거래대금은 한쪽 기준의 체결 대금이다. 매수와 매도 양쪽을 더한 금액의 분모는 이 값의 두 배다.
- 매수·매도 우위는 `(외국인 매수 거래대금 − 외국인 매도 거래대금) / (외국인 매수 거래대금 + 외국인 매도 거래대금)`이다.
  범위는 −1~1이다. 백분율로 표시하려면 화면에서 100을 곱한다.
- 종목·거래일·시장·거래소·단위가 같은 확정 값끼리 계산한다. KRX와 NXT 또는 통합 금액의 혼합,
  한쪽 금액 결측, 순매수만 있는 경우에는 비율을 제공하지 않는다. 양쪽 모두 0이고 종목 거래대금이
  양수이면 참여율은 0이고 우위는 null이다. 외국인 지분율은 별도 기준일과 원천을 유지한다.

## 조회 계약 (#917)

`GET /api/market/flow-board/kr?market=kospi&days=22`는 세션 인증이 필요한 읽기 전용
요청이다. `market`은 `kospi` 또는 `kosdaq`, `days`는 1~22이며 기본값은 각각
`kospi`와 22다. 이 범위를 벗어난 입력에는 422를 반환한다. 응답의 캐시 정책은
`private, no-store`다.

| 필드 | 표시 기준 |
| --- | --- |
| `latest_session`, `sessions[]` | 한국 현지 정규장이 종료된 마지막 거래일과 조회에 포함된 오름차순 거래일. 주말·휴장과 진행 중인 세션은 제외한다. 최근 마감과 원천의 공표 가능일은 다를 수 있다. |
| `ranking` | `status`, `as_of`, `source`, `published_at`, `items[]`. 각 시장에서 같은 날의 종목 거래대금 내림차순, 동률은 종목 코드순이다. 최근 마감보다 이전에 발표된 순위는 `stale`이다. |
| `favorites` | 인증 계정이 저장한 선택 시장 관심종목만 기존 표시 순서로 제공한다. `items[].history[]`는 자료가 없는 날짜도 포함한다. 다른 계정의 관심종목은 응답이나 공유 캐시에 들어가지 않는다. |
| `market_summary` | `scope: "market"`, `flow`, `index`의 별도 시점·출처와 상태를 표시한다. 시장 전체 외국인 수급이나 지수는 개별 종목 계산에 사용하지 않는다. |
| `sources` | `turnover`, `foreign_flow`, `ownership`의 사용 가능 여부를 표시한다. 각 관측의 세부 상태와 기준일은 행 안에 있다. |

`ranking.items[]`는 `ticker`, `name`, `rank`, `latest`, `latest_ownership`를 제공하고,
`favorites.items[]`는 `ticker`, `name`, `history[]`, `latest_ownership`를 제공한다.
거래일별 항목은 `date`, `turnover`, `foreign_flow`, `ownership`, `participation_pct`,
`buy_sell_imbalance`, `participation_status`, `imbalance_status`를 담는다.

- `turnover.value_krw`와 `foreign_flow.buy_krw/sell_krw/net_krw`는 원화 금액이다.
  `ownership.ratio_pct`는 지분율(%)이다. 각 관측은 `status`, `as_of`, `source`,
  `published_at`, `unit`을 가진다. 원천이 공표 시각을 주지 않으면 `published_at`은 null이다.
- `ownership`은 해당 거래일의 기준일이 명시된 지분율이다. `latest_ownership`에는
  표시 기준일 이하의 최근 확정치를 담고 원래 기준일을 보존한다.
- `status`는 `available`, `partial`, `no_data`, `source_unavailable`, `stale` 등을
  구분한다. 관측·산식 상태는 `scope_mismatch`, `unit_mismatch`, `not_published`,
  `missing_input`, `zero_denominator`, `inconsistent_amounts`도 반환할 수 있다.

## 조회량과 검증 범위

현황판 요청은 DB에 이미 저장된 주권 거래대금·지수와 기존 시장 수급 캐시만 읽는다.
거래대금 순위는 시장별 하루 묶음으로 읽으며 이력은 관심종목을 한 번에 조회한다. 캐시가 없거나 원천이
미연결이면 가능한 항목만 표시한다. 화면에서 상위 50개가 보여도 공급자 요청은 발생하지 않는다.

기본 KRX 자동 수집 목록에는 두 주권 서비스가 들어 있지 않다. 순위를 게시하려면 해당 시장의
승인된 전종목 일별 JSON 응답을 확보한 뒤 아래 검증을 실행한다. 입력은 `OutBlock_1`이 들어 있는
공식 일별 주권 시세 응답이며, 페이지 발췌나 상위 종목만 선택한 파일은 시장 전체를 대표할 수 없다.

```sh
python -m desk.kr_flow_import --market kospi --date 2026-11-24 --input approved-stock-day.json
```

기본 명령은 DB에 접속하지 않고 행 수만 요약한다. 해당 자료를 서비스에서 사용할 권한을 확인한
관리자는 명시적으로 `--apply`를 붙여 기존 DB의 현황판 전용 주권 스냅샷 영역에 저장할 수 있다.
가져오기는 네트워크에서 원천을 수집하지 않는다. 휴장·미마감, 시장·거래일 불일치, 잘못된
거래대금, 중복·누락 코드는 전체 입력을 거절하며 마지막 정상 스냅샷을 유지한다.

스냅샷이 없으면 순위는 `source_unavailable`이다. 개발 환경에서는 주권 스냅샷, 외국인 수급,
지분율을 합성하여 날짜·단위와 응답 상태를 검증했다. 승인된 KIS·KRX 실측에서 확인할 종목은
삼성전자(005930)와 SK하이닉스(000660)다. 실제 수급과 지분율의 적재가 연결될 때까지 두 원천의
종목 값은 null이다.
