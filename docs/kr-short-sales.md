# 국내 개별종목 공매도 데이터

## 공표 기준과 공급 경계

- 거래 비중은 전체 공매도 거래대금 ÷ 전체 거래대금 × 100으로 계산한다. KRX 개별종목 화면은 정규장 자료를 15:40 이후, 전체 매매 자료를 20:10 이후 제공한다. 2025-03-04 이후 화면의 KRX·NXT 합산 범위를 유지하려면 분자와 분모가 같은 통계 행에서 와야 한다. [KRX 개별종목 거래](https://data.krx.co.kr/contents/MDC/STAT/srt/MDCSTAT301.jsp) · [KRX 비중 정의](https://data.krx.co.kr/contents/MDC/STAT/srt/MDCSTAT304.jsp)
- 순보유잔고 비중은 보고대상 순보유잔고 수량을 상장주식수로 나눈 공표치다. T+2 보고와 정정 가능성이 있으므로 거래와 다른 기준일을 보존한다. [KRX 개별종목 순보유잔고](https://data.krx.co.kr/contents/MDC/STAT/srt/MDCSTAT305.jsp) · [KRX 잔고 비중 정의](https://data.krx.co.kr/contents/MDC/STAT/srt/MDCSTAT308.jsp)
- OPEN API의 공개 종목 일별매매 서비스는 이 두 통계의 공표 자료로 확인되지 않았다. KRX는 키 발급과 서비스별 활용 신청·승인을 안내하며, 데이터 분배는 별도 상품 경로다. 도입 시 두 통계의 수신·사용 범위와 승인된 명세를 확인한다. [OPEN API 서비스 목록](https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd) · [이용방법](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO003.jsp) · [데이터 분배상품](https://openapi.krx.co.kr/contents/OPP/DATA/OPPDATA002.jsp)

`desk.kr_short_sales.KrxShortSales`는 공급자가 제공하는 공표 행을 입력받는다. #979부터 거래 비중은 `kis_source`(한국투자증권 국내주식 공매도 일별추이)로 연결하고, 순보유잔고는 연결된 공급자가 없어 그 시리즈만 `source_unavailable`로 답한다. KIS 클라이언트가 없으면(키 미설정 · 가짜 실행) 카드 전체가 `source_unavailable`이다. 공표 화면의 내부 요청을 호출하거나 다른 원천의 거래대금으로 분모를 대체하지 않는다.

### 공식 공급 경로 재확인

2026-11-24 공개 명세와 저장소의 공급 계약을 대조했다. 거래대금 비중과 잔고 비중을 함께 읽어
인증 화면에 제공할 수 있는 승인된 경로는 확인되지 않았다.

| 후보 | 확인한 제공 범위와 제약 |
| --- | --- |
| KRX Data Marketplace | 개별종목 거래 화면과 종목별 순보유잔고 화면을 각각 대조 기준으로 쓴다. 거래 비중의 산식은 상위 50 전용 화면에도 있으며, 그 화면은 2026-09-14부터 당일 자료를 22시 이후 제공한다. 순보유잔고는 보고대상 투자자의 합계로, T+2일 18시 이후에 제출된 신규·정정 보고에 따라 바뀔 수 있다. 공개 안내에서 두 통계의 서버 조회 계약과 잔고의 정확한 공개 시각은 확인되지 않았다. [종목별 거래](https://data.krx.co.kr/contents/MDC/STAT/srt/MDCSTAT301.jsp) · [종목별 잔고](https://data.krx.co.kr/contents/MDC/STAT/srt/MDCSTAT305.jsp) · [상위 50 통계](https://data.krx.co.kr/contents/MDC/STAT/srt/MDCSTAT304.jsp) |
| KRX OPEN API | 공개 목록의 유가증권·코스닥 일별매매 서비스에서 두 공매도 통계의 명세를 찾지 못했다. 키 신청과 서비스별 신청은 각 관리자 승인이 필요하다. 약관은 비상업 이용과 화면 출처 표시를 요구하고 제3자 제공을 제한한다. 기존 일반 일별매매 승인으로 두 통계를 사용할 수 있다는 근거는 없다. [서비스 목록](https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd) · [이용방법](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO003.jsp) · [이용약관 제6·10·11조](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO002.jsp) |
| KRX 데이터 분배 | 주식 실시간 상품의 항목에 공매도가 기재돼 있다. 제품 개요로는 KRX·NXT 합산 전체 거래대금, 별도 잔고 비중, 과거 조회 범위의 제공을 확인할 수 없다. 기관 내부 이용과 외부 제공 권한은 계약에서 구분되며 이 기능에 두 통계를 제공하는 계약과 명세를 확인하지 못했다. [분배상품](https://openapi.krx.co.kr/contents/OPP/DATA/OPPDATA002.jsp) · [데이터 라이선스](https://openapi.krx.co.kr/contents/OPP/DATA/OPPDATA004.jsp) |
| 한국투자증권 | 공식 국내주식 공매도 일별추이 견본에 공매도 거래대금과 거래대금 비중 필드가 있다. 개별종목 순보유잔고 비중, KRX·NXT 합산과의 동등성, 이번 사용 목적의 권한은 확인되지 않았다. 별도 상품인 대차거래나 신용잔고를 순보유잔고 대신 사용하지 않는다. [KIS 공식 API 목록](https://apiportal.koreainvestment.com/apiservice-summary) · [응답 필드 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/daily_short_sale/chk_daily_short_sale.py) |
| 토스증권 | 공개 OpenAPI 1.2.28의 국내 일별 공매도는 원화 공매도 거래대금과 시간외를 포함한 소수 비율을 제공하고 일별 확정치는 저녁에 반영한다고 안내한다. 순보유잔고 비중과 전체 거래대금 원필드가 없다. 분모 0도 비율 0을 반환하므로 이 값만으로 공표된 실제 0과 분모 0을 구분할 수 없다. KRX·NXT 합산 동등성과 이 기능의 사용 승인도 확인되지 않았다. [토스증권 OpenAPI 명세](https://openapi.tossinvest.com/openapi-docs/latest/openapi.json) |

KRX 공개 문서에서 표의 항목과 공표 기준을 확인했다. 공개 정적 화면에서 삼성전자·SK하이닉스의
동일 거래일 행을 확보하지 못했고, 대조 가능한 승인된 공급 표본도 없었다. 기본 `source_unavailable`을
유지한다. 공급자 평가에는 수신·캐시·파생 비중 표시의 권한, 종목 매핑,
세션과 시장 범위, 공표 및 정정 기준, 거래대금 두 필드와 독립 잔고 기준일을 확인할 수 있는 명세가 필요하다.

### KIS 연결 (#979)

2026-11-25 사용자 결정으로 거래 비중 원천을 KIS로 정했다. 토스 명세는 공매도 거래대금과 비율만 주고 분모가 0이어도 비율 0을 돌려줘
실제 0%와 분모 0을 구분할 수 없다. KIS는 분자와 분모를 같은 행에 준다. 두 공급자 모두 본인 키로 1인용 인증 화면에만 표시하고 재배포하지 않는다.

| 항목 | 내용 |
| --- | --- |
| 요청 | `GET /uapi/domestic-stock/v1/quotations/daily-short-sale`, tr_id `FHPST04830000`, `FID_COND_MRKT_DIV_CODE=J`(KRX), `FID_INPUT_ISCD`=6자리 코드, `FID_INPUT_DATE_1/2`=20거래일 창의 처음 · 끝 |
| 계산 | `output2` 행마다 `ssts_tr_pbmn`(공매도 거래대금) ÷ `acml_tr_pbmn`(누적 거래대금) × 100, 기준일 `stck_bsop_date` |
| 분모 0 | `acml_tr_pbmn`이 0이면 `zero_denominator`, 공매도 0과 양수 분모는 `0%` |
| 단위 방어 | 양수 비중은 KIS의 `ssts_tr_pbmn_rlim`과 10배 안에서 맞아야 한다. 그 필드가 없거나 0인데 계산값이 0.005% 이상이면 확인할 수 없으므로 그 행을 `no_data`로 두고 경고를 남긴다 |
| 행 수 | 한 번 호출하고 페이지를 넘기지 않는다. KIS가 창보다 적은 행을 주면 오래된 점이 `no_data`가 되고 INFO 로그를 남긴다 |
| 미공표 | 20:10 전에는 당일 점을 `not_published`로 두고 직전 거래일까지만 읽는다(기존 규칙) |
| 장애 | `KisError`는 `source_error`이고 마지막 정상값을 `stale`로 유지한다. 1분 뒤 재시도한다 |
| 토큰 | 선물 현황과 같은 `KisRest`(공용 토큰 `kis_rest`)를 쓴다. 키 · 토큰은 응답 · 로그에 남지 않는다 |

남은 확인: 운영에서 같은 거래일 KRX 개별종목 거래 화면(MDCSTAT301)과 값을 대조한다. 대조 항목은 `acml_tr_pbmn`의 단위(원)와
세션 범위(시간외 포함 여부), NXT 포함 여부다. 20:10 직후 KIS가 당일 행을 아직 갱신하지 않아 공매도 0을 주면 15분 캐시 동안 0%로 보일 수 있으니,
당일 행이 올라오는 시각도 함께 확인한다. 코드 `J`는 KRX 시장만 뜻하므로, 대조 전까지 화면 출처를 "KRX 시장 기준"으로 적는다.

## 응답과 캐시

- 기존 세션으로 인증한 단일 `GET /api/positioning?ticker=005930.KS`에서 국내 6자리 `.KS`·`.KQ` 종목의 응답에 `kr_short`를 추가한다. 국내 거래·잔고는 이 필드에서 읽는다. 미국 단일과 기존 배치의 응답 계약은 유지한다.
- 국내 응답의 상위 `supported`는 정규화된 종목을 어댑터가 지원하는지 뜻한다. 정상 국내 코드에 공급자가 없으면 `reason`과 `kr_short.status`가 `source_unavailable`이며, 공급자가 해당 종목을 명시적으로 지원하지 않으면 `supported: false`와 `reason: "unsupported"`가 반환된다.
- `source`는 연결된 공급자 이름(`kis`)이고 미연결이면 `null`이다. 공급자가 순보유잔고를 주지 않으면 `balance.status`만 `source_unavailable`이며, 카드는 이를 고장이 아닌 "공급자 미제공"으로 표시한다.
- `market`은 `KOSPI` 또는 `KOSDAQ`이다. 상위 `status`는 거래 상태를 나타내며, `trade`와 `balance`는 각각 상태·`latest`·`stale`을 제공한다. 점은 `as_of`, `ratio_pct`, `status`를 담는다.
- `trade.history`는 오래된 순서대로 최근 20개 거래 세션을 제공한다. 휴장일은 제외하고, 거래일 전체 공표 전에는 당일 점을 `not_published`로 표시한다. 빈 공표 행과 분모 0은 각각 `no_data`와 `zero_denominator`이며, 공매도 거래대금 0과 유효한 총 거래대금은 `0%`다.
- 공급자 미설정은 `source_unavailable`, 공급자가 명시한 미지원 종목은 `unsupported`, 자료 조회 오류는 `source_error`다. 마지막 정상값을 표시할 때 그 값의 원래 기준일을 유지한다. `stale`은 오류·결측·분모 0에서 과거 정상값을 쓰는 경우를 구분한다.
- 캐시는 종목과 최근 전체 공표 기준 거래일에 맞춰 갱신한다. 같은 공표 기준일의 동시 조회는 공유하며, 일시적 오류·미공표는 더 빠르게 재시도한다. 잔고 기준일은 공급자의 거래일별 공표 행을 그대로 사용한다.

## 표본과 검증

삼성전자 `005930.KS`, SK하이닉스 `000660.KS`, 에코프로비엠 `247540.KQ`를 합성 시험의 종목 코드로 사용한다. 실제 종목별 수치는 승인된 공급자의 날짜와 거래 범위를 확정한 뒤, 같은 날짜의 KRX 거래·순보유잔고 통계와 대조한다. 합성 시험 수치는 운영 시세로 저장하거나 제공하지 않는다. [KOSPI 종목 코드](https://kind.krx.co.kr/external/dst/valueupReference/11216/%28%EB%B3%B4%EB%8F%84%EC%9E%90%EB%A3%8C%29%20%ED%95%9C%EA%B5%AD%EA%B1%B0%EB%9E%98%EC%86%8C%2C%20%E3%80%8C%EC%BD%94%EB%A6%AC%EC%95%84%20%EB%B0%B8%EB%A5%98%EC%97%85%20%EC%A7%80%EC%88%98%E3%80%8D%20%EB%B0%9C%ED%91%9C.pdf) · [KOSDAQ 종목 코드](https://kind.krx.co.kr/external/2026/08/04/000867/20260804001737/00636.htm)
