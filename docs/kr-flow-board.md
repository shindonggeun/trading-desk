# 국장 수급 현황판 (#916, #917)

## 원천 확인

| 원천 | 확인한 범위 | 연결 범위 |
| --- | --- | --- |
| 한국투자증권 | 종목별 투자자매매동향 일별 `FHPTJ04160001`의 `output2`에 영업일 `stck_bsop_date`, 종목 누적 거래대금 `acml_tr_pbmn`, 외국인 매수 `frgn_shnu_tr_pbmn`, 매도 `frgn_seln_tr_pbmn`가 정의돼 있다. 금액마다 거래량 필드(`acml_vol`, `frgn_shnu_vol`, `frgn_seln_vol`)가 따로 있다. 주식현재가 시세 `FHKST01010100`은 외국인 보유 수량 `frgn_hldn_qty`, 상장 주식 수 `lstn_stcn`, HTS 외국인 소진율 `hts_frgn_ehrt`를, 주식현재가 일자별 `FHKST01010400`은 일별 `hts_frgn_ehrt`를 준다. 요청의 `J`는 KRX, `NX`는 NXT, `UN`은 통합이다. 공식 예제는 금액 단위를 밝히지 않는다. | #916에서 연결했다. 관심종목과 거래대금 상위 50개 종목에 세 TR을 `J`로 부른다. 기존 선물 수급과 같은 한국투자 클라이언트를 쓰며 토큰을 새로 발급하지 않는다. 금액 단위는 응답마다 거래량과 가격 범위로 확인한다(아래 [한국투자 종목별 수급](#한국투자-종목별-수급-916)). 삼성전자·SK하이닉스 실측 대조는 배포 뒤에 한다(#1018). [한국투자 공식 요청 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/investor_trade_by_stock_daily/investor_trade_by_stock_daily.py) · [공식 응답 필드 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/investor_trade_by_stock_daily/chk_investor_trade_by_stock_daily.py) · [서비스 이용 안내](https://apiportal.koreainvestment.com/about-howto) |
| 토스증권 | 공개 시세 계약은 호가·가격·최근 체결·상하한가·캔들이다. 저장소에 있는 투자자 매매는 KOSPI/KOSDAQ 시장 단위다. | 종목별 외국인 매수·매도의 하루 거래대금으로 확인된 경로가 없다. 시장 합계만 별도 표시한다. [토스증권 시세 명세](https://developers.tossinvest.com/docs/market-data) |
| KRX 종목 시세 | 유가증권시장과 코스닥의 상장 주권 일별매매정보는 시장별 서비스다. 기존 KRX 수집 목록에는 지수, ETF 등 일곱 서비스가 있다. 두 종목 시세 서비스는 각각 이용 신청·승인이 필요하다. | 승인된 일별 주권 시세가 같은 시장·거래일의 전체 스냅샷으로 저장된 경우 거래대금 순위를 계산한다. 화면에 출처를 표시한다. [유가증권 일별매매정보](https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S2.cmd?BO_ID=JvJFzlAENzZlPBDNGAWC) · [코스닥 일별매매정보](https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S2.cmd?BO_ID=hZjGpkllgCBCWqeTsYFj) · [KRX API 이용약관](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO002.jsp) · [이용 신청](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO003.jsp) |
| KRX 외국인 보유 | 웹 통계는 지분율과 한도소진율을 별도 열과 순위 방식으로 제공한다. 보유수량·한도소진율은 장 개시 시 표시하는 전일 확정치다. | 승인된 자동 수집 경로가 확인되지 않아 웹 통계에 요청하지 않는다. 별도 보유 기준일과 공표 시각이 있는 지분율 계약을 제공한다. [KRX 보유 지분 통계](https://data.krx.co.kr/contents/MDC/EASY/ranking/MDCEASY021.jsp) · [KRX 데이터 수신과 이용](https://openapi.krx.co.kr/contents/OPP/DATA/OPPDATA003.jsp) |

KRX OPEN API 약관은 비상업적 이용, 화면의 원천 표시 및 제3자 정보 제공 제한을 명시한다. 제공 범위는
기존 인증 화면과 승인된 데이터 사용 범위 안에서 확인한다.

### 공식 자료 대조 (2026-11-24)

- 한국투자 공개 예제는 `J`, `NX`, `UN`을 각각 KRX, NXT, 통합 요청으로 구분한다.
  `FID_INPUT_DATE_1`은 조회에 넣는 기준일이며 행의 거래일은 `output2.stck_bsop_date`에서 읽어야 한다.
  `acml_tr_pbmn`과 외국인 매수·매도·순매수 대금 필드는 거래량 필드와 별도다. 공개 예제의 열 이름만으로는
  종목·거래 범위별 실제 값, 배율과 반올림, 당일 정규장 집계와 최종 집계의 대응을 재현할 수 없었다.
  이미 연결된 시장 합계나 다른 KIS TR의 금액 단위를 옮겨 쓰지 않는다.
  [한국투자 공식 요청 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/investor_trade_by_stock_daily/investor_trade_by_stock_daily.py) ·
  [한국투자 응답 필드 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/investor_trade_by_stock_daily/chk_investor_trade_by_stock_daily.py)
- KRX 개별종목 거래실적은 거래량과 거래대금 각각의 매도·매수·순매수 열 및 외국인·기타외국인 분류를
  보여 준다. 정규장 마감분은 15:45경, 시간외를 포함한 최종분은 20:00경 제공한다고 안내한다(한국 시각).
  표에 표시된 투자자 구분과 집계 범위를 확인한 경우에만 분자·분모를 맞출 수 있다. 시각은 공개 화면의
  예정 시각이므로 종목 응답의 `published_at`을 이 시각으로 채우지 않는다. KRX의 다른 웹 통계에서는
  ‘정규시장’ 열에 일부 시간외 거래를 합산하므로 열 이름만으로 두 화면을 결합할 수 없다.
  [KRX 개별종목 투자자별 거래실적](https://data.krx.co.kr/contents/MDC/MDI/outerLoader/index.cmd?screenId=MDCSTAT023) ·
  [KRX 시장동향 거래 범위](https://data.krx.co.kr/contents/MDC/EASY/main/MDCEASY002.jsp)
- 토스 시세 문서는 토큰이 필요한 호가·체결·캔들 계약을 공개한다. 기존 토스 시장 단위 수급을 사용해도
  두 종목의 같은 날 외국인 매수·매도 대금으로 좁힐 수 없다. 실시간 체결의 방향 또는 체결량을 외국인 매매로
  환산하지 않는다. [토스증권 시세](https://developers.tossinvest.com/docs/market-data) ·
  [토스증권 실시간 체결](https://developers.tossinvest.com/docs/trade)

KRX 외국인 보유 화면은 외국인 보유주식수, 종목의 상장주식수, 외국인 한도수량을 따로 제공한다. 같은
종목·종류주·보유 기준일에서 지분율은 보유주식수 ÷ 상장주식수, 한도소진율은 보유주식수 ÷ 한도수량이다.
화면은 장 개시 시점의 외국인 보유수량에 전일 확정치를 사용하므로 거래일이나 화면 조회일을 그대로
보유 기준일에 붙일 수 없다. 실제 정규장 거래일의 주식 수와 보유수량은 같은 기준일의 공식 행으로 확인해야 한다.
[KRX 외국인보유 상위](https://data.krx.co.kr/contents/MDC/EASY/ranking/MDCEASY021.jsp)

| 공개 발행사 표본 | 확인한 기준과 범위 | 현황판에서 사용할 수 없는 근거 |
| --- | --- | --- |
| 삼성전자 `005930.KS` | 2026-09-30 IR 상장현황의 발행 보통주 5,846,278,608주와 우선주 802,371,203주는 별도 종류다. 같은 분기말 IR 주주구성에서 외국인은 보통주 45%, 우선주 72%로 표시된다. | 분기 IR 값은 종류주를 구분하는 근거다. 다른 거래일의 상장주식수, 일별 외국인 보유 수량 또는 KRX 지분율을 제공하지 않는다. [삼성전자 상장현황](https://www.samsung.com/sec/ir/stock-information/listing-info/) · [삼성전자 주주구성](https://www.samsung.com/sec/ir/stock-information/ownership-structure/) |
| SK하이닉스 `000660.KS` | 2026-09-30 IR 주주구성에서 상장주식 730,492,365주는 모두 보통주다. 같은 페이지의 BlackRock 보유는 2026-02-10의 개별 신고(36,407,157주, 5.1%)다. | 개별 주주의 보유량을 같은 페이지의 다른 날짜 상장주식수로 나눠 외국인 전체 일별 지분율을 만들 수 없다. [SK하이닉스 주주구성](https://www.skhynix.com/ir/UI-FR-IR04/) |

공식 문서와 공개 발행사 표본을 읽기 전용으로 대조했다. 저장소의 KRX fixture는 지수·ETF·파생 등
기존 수집 서비스용이며, 두 종목의 같은 거래일에 대한 승인된 KIS 종목 응답, 토스 종목 외국인 대금,
KRX 주권 거래대금과 외국인 보유 행을 한 쌍으로 확인할 수 없었다. 발행사 IR 값은 이력을 채우는 데 사용하지
않는다. 원천 연결에는 서비스 이용 승인과 표시 가능 범위를 먼저 확인한 종목별 원본 또는 승인된 전용 fixture가
필요하다. [KRX OPEN API 이용약관](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO002.jsp) ·
[KRX 데이터 수신과 이용](https://openapi.krx.co.kr/contents/OPP/DATA/OPPDATA003.jsp)

## 지표

- 외국인 참여율(%)은 `100 × (외국인 매수 거래대금 + 외국인 매도 거래대금) / (종목 거래대금 × 2)`다.
  종목 거래대금은 한쪽 기준의 체결 대금이다. 매수와 매도 양쪽을 더한 금액의 분모는 이 값의 두 배다.
- 매수·매도 우위는 `(외국인 매수 거래대금 − 외국인 매도 거래대금) / (외국인 매수 거래대금 + 외국인 매도 거래대금)`이다.
  범위는 −1~1이다. 백분율로 표시하려면 화면에서 100을 곱한다.
- 종목·거래일·시장·거래소·단위가 같은 확정 값끼리 계산한다. KRX와 NXT 또는 통합 금액의 혼합,
  한쪽 금액 결측, 순매수만 있는 경우에는 비율을 제공하지 않는다. 양쪽 모두 0이고 종목 거래대금이
  양수이면 참여율은 0이고 우위는 null이다. 외국인 지분율은 별도 기준일과 원천을 유지한다.
- 정규화 입력의 `trade_scope`에는 공급자가 확인한 정규장(`regular`) 또는 시간외 포함 전체
  거래(`all_sessions`) 범위를 지정한다. 참여율에는 양쪽의 동일한 범위가 필요하다. 매수·매도 우위에는
  외국인 매수·매도가 같은 확인된 범위에 속하면 된다. 거래 범위가 다르면 참여율은 `scope_mismatch`,
  비교에 필요한 범위가 없으면 `no_data`다. 단위가 확인되지 않은 외국인 대금은 원으로 가정하지 않는다.
- 정규화 지분율은 분모가 같은 종류주·기준일의 상장주식수임을 `denominator=listed_shares`로
  확인한 경우에만 표시한다. 분모가 확인되지 않거나 한도수량을 쓰면 해당 날짜의 값은 `null`이다.
  이전에 확인된 지분율이 있으면 원래 기준일로 `latest_ownership`에 표시한다.

## 조회 계약 (#917)

`GET /api/market/flow-board/kr?market=kospi&days=22`는 세션 인증이 필요한 읽기 전용
요청이다. `market`은 `kospi` 또는 `kosdaq`, `days`는 1~22이며 기본값은 각각
`kospi`와 22다. 이 범위를 벗어난 입력에는 422를 반환한다. 응답의 캐시 정책은
`private, no-store`다.

| 필드 | 표시 기준 |
| --- | --- |
| `latest_session`, `sessions[]` | 한국 현지 정규장이 종료된 마지막 거래일과 조회에 포함된 오름차순 거래일. 주말·휴장과 진행 중인 세션은 제외한다. 최근 마감과 원천의 공표 가능일은 다를 수 있다. |
| `ranking` | `status`, `as_of`, `source`, `published_at`, `items[]`. 각 시장에서 같은 날의 종목 거래대금 내림차순, 동률은 종목 코드순이다. 최근 마감보다 이전에 발표된 순위는 `stale`이다. |
| `favorites` | 인증 계정이 저장한 선택 시장 관심종목만 기존 표시 순서로 제공한다. `items[].history[]`는 자료가 없는 날짜도 포함한다. 다른 계정의 관심종목은 응답에 들어가지 않는다. 공유 캐시에는 관심종목 목록이나 계정이 아니라 종목별 공개 시세만 종목 단위로 들어간다. |
| `market_summary` | `scope: "market"`, `flow`, `index`의 별도 시점·출처와 상태를 표시한다. 시장 전체 외국인 수급이나 지수는 개별 종목 계산에 사용하지 않는다. |
| `market_summary.index.change_pct` | 지수 종가와 같은 KRX 대표지수 일별 행의 `FLUC_RT`를 퍼센트 단위로 표시한다. 현재 종가는 `CLSPRC_IDX`이며 기준일은 `index.as_of`다. [KOSPI 일별시세](https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES001_S2.cmd?BO_ID=EREKZauXnMmxyIlqzeDN) · [KOSDAQ 일별시세](https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES001_S2.cmd?BO_ID=nimebcamqFNIPNcRrHoO) |
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
- 지수는 한국 정규장이 마감된 거래일 중 저장된 최신 행을 사용한다. 공표가 늦으면 저장된 행의 기준일을
  그대로 표시한다. 원본 `BAS_DD`가 저장 기준일과 일치하고 `FLUC_RT`가 유한한 십진수 문자열일 때만
  전 거래일 대비 값으로 사용한다. 실제 0은 0%이며 `null`이나 이전 응답의 필드 생략은 자료 없음으로 표시한다.
- `status`는 `available`, `partial`, `no_data`, `source_unavailable`, `stale` 등을
  구분한다. 관측·산식 상태는 `scope_mismatch`, `unit_mismatch`, `not_published`,
  `missing_input`, `zero_denominator`, `inconsistent_amounts`도 반환할 수 있다.

## 조회량과 검증 범위

현황판 요청은 DB에 저장된 주권 거래대금·지수, 기존 시장 수급 캐시, 한국투자 종목별 수급(#916)을 읽는다.
거래대금 순위는 시장별 하루 묶음으로 읽으며 이력은 관심종목을 한 번에 조회한다. 캐시가 없거나 원천이
미연결이면 가능한 항목만 표시한다. 공급자 요청은 아래 한국투자 종목별 수급에서만 생긴다.

### 한국투자 종목별 수급 (#916)

- 대상: 요청 계정의 선택 시장 관심종목을 먼저, 그다음 거래대금 순위 상위 50개를 순서대로 부른다.
  순위 스냅샷이 없으면 관심종목만 부른다. 시장 코드는 KOSPI와 KOSDAQ 모두 `J`(KRX)다.
- 호출: 종목 하나에 `FHPTJ04160001`, `FHKST01010100`, `FHKST01010400`을 한 번씩 부른다.
  하나가 실패하면 그 계열만 빠진다. 이 기능의 한국투자 요청은 한 번에 두 개까지이고, 앞 요청이 시작된 뒤
  0.1초가 지나야 다음 요청을 보낸다(초당 10건 이하). collector 와 같은 앱 키의 초당 한도에 여유를 남기기 위해서다.
- 금액 단위: 응답은 금액 단위를 밝히지 않는다. 금액을 자기 거래량으로 나눈 평균가가 그날 저가·고가의
  ±10% 안에 드는 배율을 1, 1,000, 1,000,000 중에서 찾는다. ±10%는 시간외 단일가가 움직일 수 있는 폭이다.
  거래대금은 `acml_tr_pbmn`/`acml_vol`, 외국인 매수는 `frgn_shnu_tr_pbmn`/`frgn_shnu_vol`,
  매도는 `frgn_seln_tr_pbmn`/`frgn_seln_vol`로 따로 본다. 맞는 배율이 정확히 하나일 때만 원으로 바꾼다.
  금액은 자기 단위로 반올림돼 있어 ±0.5단위 안의 값을 모두 후보로 본다(작은 거래가 백만원 단위로 "1"이 되는 경우).
  금액과 거래량이 모두 0이면 0이다. 한쪽만 0이거나 맞는 배율이 없으면 그 값은 null이다. 거래대금은
  `no_data`, 외국인 매수·매도 중 한쪽만 남으면 `partial`이고 비율은 내지 않는다.
  서버 로그에는 종목 코드·날짜·필드 이름만 남긴다.
- 확정: 거래일 D의 행은 D의 정규장 마감 4시간 40분 뒤(보통 20:10, 수능일처럼 늦게 끝나는 날은 그만큼 늦게)
  이후에 받은 경우에만 확정이다. 그 전에 받은 행은
  `not_published`다. 휴장일 행은 버린다. 거래 범위는 시간외를 포함한 KRX 전체(`all_sessions`)로 표시한다.
- 참여율 분모: 같은 날 한국투자 외국인 대금이 있으면 표시 거래대금과 참여율 분모 모두 같은 응답의
  거래대금을 쓴다. KRX 스냅샷 거래대금은 거래 범위가 확인되지 않아 참여율 분모로 쓰지 않는다.
  순위 순서와 한국투자 값이 없는 날의 거래대금 표시에만 쓴다.
- 지분율: 외국인 보유 수량 ÷ 상장 주식 수 × 100이다. 보유 수량이 상장 주식 수 이하이고 상장 주식 수가
  양수일 때만 쓴다. 이 값과 HTS 소진율의 차이가 0.02%p 이하이면 외국인 한도가 상장 주식 수와 같다고 본다.
  이때만 일자별 소진율을 그날의 지분율로 쓴다. 한도가 있는 종목은 이력이 `no_data`이고 최신 지분율만 표시한다.
- 지분율 기준일: 세션 S 동안 보이는 보유 수량은 직전 거래일 종가 기준 확정치다. 일자별 행은 S의 직전
  거래일을 기준일로 붙인다. 현재가 값은 조회 시점에 시작한 최근 세션의 직전 거래일이다. 개장 전이면 최근
  세션은 전 거래일이다. 두 경우 모두 `OWNERSHIP_LAG_SESSIONS = 1` 하나로 정한다. 기준일이 같으면 현재가 값을
  쓴다. 이 지연은 배포 뒤 KRX 화면과 대조해 확인한다(#1018).
- 캐시: 종목과 최근 마감 거래일마다 메모리(최대 256개)와 봉인된 공유 캐시 `kr_stock_flow`에 둔다.
  최근 거래일 행이 확정이면 6시간, 아니면 10분 동안 쓴다. 호출이 하나라도 실패하면 1분 뒤 다시 부르고
  그 응답은 공유하지 않는다. 다음 장 개시 시각은 넘기지 않는다(현재가 지분율의 기준일이 바뀐다).
  같은 종목을 동시에 요청하면 한 번만 부른다. 다시 부르다 실패하면 같은 거래일의 지난 값을 쓴다.
  만료된 값은 기다리지 않고 바로 보여 주고, 새로 받는 일은 뒤에서 한다. 새 거래일 값이 아직 없으면
  메모리에 남은 직전 거래일 값으로 이력을 보여 준다. 한꺼번에 대기하는 조회는 120개까지이고, 넘으면 캐시에 있는 것만 보여 준다.
- 대기: 요청은 최대 2초 기다리고 끝난 종목만 보여 준다. 남은 조회는 계속 돌아 다음 요청에 쓰인다.
  아직 조회 중인 종목만 있으면 그 계열은 `no_data`다(원천 미연결이 아니다).
  한국투자 키가 없거나 가짜 실행이면 `foreign_flow`와 `ownership`은 `source_unavailable`이다.

### KRX 주권 스냅샷

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
지분율을 합성하여 날짜·단위와 응답 상태를 검증했다. 한국투자 응답은 원·백만원 단위, 단위 불일치,
0과 결측, 20:10 전후, 한도 유무를 합성 응답으로 시험했다. 실측 대조 종목은 삼성전자(005930)와
SK하이닉스(000660)이며 배포 뒤에 확인한다(#1018). 그 전까지 값을 막는 기준은 단위 점검과 확정 규칙이다.
