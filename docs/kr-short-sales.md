# 국내 개별종목 공매도 데이터

## 공표 기준과 공급 경계

- 거래 비중은 전체 공매도 거래대금 ÷ 전체 거래대금 × 100으로 계산한다. KRX 개별종목 화면은 정규장 자료를 15:40 이후, 전체 매매 자료를 20:10 이후 제공한다. 2025-03-04 이후 화면의 KRX·NXT 합산 범위를 유지하려면 분자와 분모가 같은 통계 행에서 와야 한다. [KRX 개별종목 거래](https://data.krx.co.kr/contents/MDC/STAT/srt/MDCSTAT301.jsp) · [KRX 비중 정의](https://data.krx.co.kr/contents/MDC/STAT/srt/MDCSTAT304.jsp)
- 순보유잔고 비중은 보고대상 순보유잔고 수량을 상장주식수로 나눈 공표치다. T+2 보고와 정정 가능성이 있으므로 거래와 다른 기준일을 보존한다. [KRX 개별종목 순보유잔고](https://data.krx.co.kr/contents/MDC/STAT/srt/MDCSTAT305.jsp) · [KRX 잔고 비중 정의](https://data.krx.co.kr/contents/MDC/STAT/srt/MDCSTAT308.jsp)
- OPEN API의 공개 종목 일별매매 서비스는 이 두 통계의 공표 자료로 확인되지 않았다. KRX는 키 발급과 서비스별 활용 신청·승인을 안내하며, 데이터 분배는 별도 상품 경로다. 도입 시 두 통계의 수신·사용 범위와 승인된 명세를 확인한다. [OPEN API 서비스 목록](https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd) · [이용방법](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO003.jsp) · [데이터 분배상품](https://openapi.krx.co.kr/contents/OPP/DATA/OPPDATA002.jsp)

`desk.kr_short_sales.KrxShortSales`는 승인된 공급자가 제공하는 공표 행을 입력받는다. 현재 기본 구성은 공급자 미설정으로 응답하며, 공표 화면의 내부 요청을 호출하거나 다른 원천의 거래대금으로 분모를 대체하지 않는다.

## 응답과 캐시

- `market`은 `KOSPI` 또는 `KOSDAQ`이다. 상위 `status`는 거래 상태를 나타내며, `trade`와 `balance`는 각각 상태·`latest`·`stale`을 제공한다. 점은 `as_of`, `ratio_pct`, `status`를 담는다.
- `trade.history`는 오래된 순서대로 최근 20개 거래 세션을 제공한다. 휴장일은 제외하고, 거래일 전체 공표 전에는 당일 점을 `not_published`로 표시한다. 빈 공표 행과 분모 0은 각각 `no_data`와 `zero_denominator`이며, 공매도 거래대금 0과 유효한 총 거래대금은 `0%`다.
- 공급자 미설정은 `source_unavailable`, 공급자가 명시한 미지원 종목은 `unsupported`, 자료 조회 오류는 `source_error`다. 마지막 정상값을 표시할 때 그 값의 원래 기준일을 유지한다. `stale`은 오류·결측·분모 0에서 과거 정상값을 쓰는 경우를 구분한다.
- 캐시는 종목과 최근 전체 공표 기준 거래일에 맞춰 갱신한다. 같은 공표 기준일의 동시 조회는 공유하며, 일시적 오류·미공표는 더 빠르게 재시도한다. 잔고 기준일은 공급자의 거래일별 공표 행을 그대로 사용한다.

## 표본과 검증

삼성전자 `005930.KS`, SK하이닉스 `000660.KS`, 에코프로비엠 `247540.KQ`를 합성 시험의 종목 코드로 사용한다. 실제 종목별 수치는 승인된 공급자의 날짜와 거래 범위를 확정한 뒤, 같은 날짜의 KRX 거래·순보유잔고 통계와 대조한다. 합성 시험 수치는 운영 시세로 저장하거나 제공하지 않는다. [KOSPI 종목 코드](https://kind.krx.co.kr/external/dst/valueupReference/11216/%28%EB%B3%B4%EB%8F%84%EC%9E%90%EB%A3%8C%29%20%ED%95%9C%EA%B5%AD%EA%B1%B0%EB%9E%98%EC%86%8C%2C%20%E3%80%8C%EC%BD%94%EB%A6%AC%EC%95%84%20%EB%B0%B8%EB%A5%98%EC%97%85%20%EC%A7%80%EC%88%98%E3%80%8D%20%EB%B0%9C%ED%91%9C.pdf) · [KOSDAQ 종목 코드](https://kind.krx.co.kr/external/2026/08/04/000867/20260804001737/00636.htm)
