# KRX OPEN API 일별 이력 (#601)

한국거래소 [KRX OPEN API](https://openapi.krx.co.kr)는 **전 영업일 장 마감 값(EOD)** 만 준다. 실시간 대체가 아니라 이력 · 검증 · 백필용이다.
투자자별 매매 · 프로그램매매 · 공매도는 이 무료 API 목록에 없어 지금처럼 한투(KIS) · 토스를 쓴다.
약관상 비상업 · 재배포 금지 — 1인용 내부 표시에만 쓴다.

코드: `backend/desk/krx.py`(수집 · 저장), `desk/cli.py krx`(백필 · 현황), Alembic 0020 `krx_daily` · `krx_days`.

## 확인한 것과 출처

포털의 'API 소개'는 로그인한 JS 페이지라 이 저장소 작업 중에는 직접 읽지 못했다. 아래는 공개 클라이언트 두 곳의
명세(둘이 서로 맞는다)에서 확인했다 — [seokhoonj/krx-openapi](https://github.com/seokhoonj/krx-openapi)
(`src/krx_openapi/_endpoint.py` · `catalog.py` · `errors.py`), [seungdols/krxon](https://github.com/seungdols/krxon) (`spec/endpoints.yaml`).
**키를 넣은 첫 배포 뒤 서버 로그 · `krx status` 로 실제 응답을 확인**하고, 다르면 이 문서와 `APIS` 를 고친다.

| 항목 | 값 |
|------|----|
| 요청 | `GET https://data-dbg.krx.co.kr/svc/apis/{분류}/{API ID}.json?basDd=YYYYMMDD` (https — http 는 리다이렉트되며 헤더가 빠진다) |
| 인증 | 헤더 `AUTH_KEY: <인증키>` (URL 에 넣지 않는다) |
| 응답 | HTTP 200 `{"OutBlock_1": [ {...}, ... ]}` — 모든 값이 문자열, 숫자에 콤마가 들 수 있다 |
| 휴장일 | `OutBlock_1` 이 빈 배열 |
| 오류 | HTTP 200 `{"respCode": "401", "respMsg": ...}` — 키가 틀리거나 **그 서비스 이용 신청이 승인되지 않음**(둘이 같은 코드). 잘못된 URL · http 는 HTTP 403 |
| 하루 한도 | 인증키당 **10,000건**, 자정 초기화. 넘으면 HTTP 429 |
| 반영 시각 | D 일 자료가 **익영업일 08:00 KST** 쯤 올라온다 → 이 앱은 08:30 부터 읽는다 |
| 이용 신청 | 서비스(API)마다 따로 신청 · 승인 |
| 제공 범위 | 아래 서비스 모두 2010-01-04 부터 |

## 쓰는 서비스

`code` 는 하루 안에서 행을 가르는 필드다(`krx_daily.code`).

| API ID | 분류 | 이름 | code | 필드 |
|--------|------|------|------|------|
| `kospi_dd_trd` | idx | KOSPI 시리즈 일별시세정보 | `IDX_NM` | BAS_DD, IDX_CLSS, IDX_NM, CLSPRC_IDX, CMPPREVDD_IDX, FLUC_RT, OPNPRC_IDX, HGPRC_IDX, LWPRC_IDX, ACC_TRDVOL, ACC_TRDVAL, MKTCAP |
| `kosdaq_dd_trd` | idx | KOSDAQ 시리즈 일별시세정보 | `IDX_NM` | (KOSPI 시리즈와 같다) |
| `drvprod_dd_trd` | idx | 파생상품지수 시세정보 (VKOSPI 등) | `IDX_NM` | BAS_DD, IDX_CLSS, IDX_NM, CLSPRC_IDX, CMPPREVDD_IDX, FLUC_RT, OPNPRC_IDX, HGPRC_IDX, LWPRC_IDX |
| `fut_bydd_trd` | drv | 선물 일별매매정보(주식선물 외) | `ISU_CD` | BAS_DD, PROD_NM, MKT_NM, ISU_CD, ISU_NM, TDD_CLSPRC, CMPPREVDD_PRC, TDD_OPNPRC, TDD_HGPRC, TDD_LWPRC, SPOT_PRC, SETL_PRC, ACC_TRDVOL, ACC_TRDVAL, ACC_OPNINT_QTY(미결제약정) |
| `opt_bydd_trd` | drv | 옵션 일별매매정보(주식옵션 외) | `ISU_CD` | BAS_DD, PROD_NM, RGHT_TP_NM(CALL/PUT), ISU_CD, ISU_NM, TDD_CLSPRC, CMPPREVDD_PRC, TDD_OPNPRC, TDD_HGPRC, TDD_LWPRC, IMP_VOLT(내재변동성), NXTDD_BAS_PRC, ACC_TRDVOL, ACC_TRDVAL, ACC_OPNINT_QTY |
| `etf_bydd_trd` | etp | ETF 일별매매정보 | `ISU_CD` | BAS_DD, ISU_CD, ISU_NM, TDD_CLSPRC, CMPPREVDD_PRC, FLUC_RT, NAV, TDD_OPNPRC, TDD_HGPRC, TDD_LWPRC, ACC_TRDVOL, ACC_TRDVAL, MKTCAP, INVSTASST_NETASST_TOTAMT(순자산총액), LIST_SHRS, IDX_IND_NM, OBJ_STKPRC_IDX, CMPPREVDD_IDX, FLUC_RT_IDX |
| `kts_bydd_trd` | bon | 국채전문유통시장 일별매매정보 | `ISU_CD` | BAS_DD, MKT_NM, ISU_CD, ISU_NM, BND_EXP_TP_NM, GOVBND_ISU_TP_NM, CLSPRC, CMPPREVDD_PRC, CLSPRC_YD, OPNPRC, OPNPRC_YD, HGPRC, HGPRC_YD, LWPRC, LWPRC_YD, ACC_TRDVOL, ACC_TRDVAL |

숫자는 `krx.num()` 으로 읽는다(`"1,234.5"` → 1234.5, `""` · `"-"` → None). 저장은 KRX 가 보낸 문자열 그대로(`payload`).

## 수집

- **스케줄**: 키가 있으면 백엔드가 15분마다 서비스별로 최근 2주 중 빠진 영업일(KRX 휴장일 · 주말은 묻지 않는다)을 받는다.
  D 일은 익영업일 08:30 KST 전에는 묻지 않는다. 그러니 평일 08:30 ~ 08:45 사이에 전 영업일치가 쌓인다.
- **빈 응답**: 최근 7일 안의 영업일이 비면 아직 안 올라온 것으로 보고 3시간 뒤 다시 묻는다. 7일이 지난 날이 비면 휴장으로 적는다(`krx_days.rows = 0`).
- **거절(401)**: 그 서비스만 6시간 건너뛴다(키 · 이용 신청 확인). **한도(429)**: 그 패스의 나머지를 멈춘다. 한 프로세스는 하루 9,000건까지만 쓴다.
- **멱등**: `krx_days` 에 (서비스, 날) 이 있으면 다시 묻지 않는다. 같은 날을 다시 받으면 그날 행을 지우고 새로 쓴다(겹치지 않는다).
- **백필**: `python -m desk.cli krx backfill --api <ID|all> --from 2015-01-01 [--to YYYY-MM-DD] [--max-calls 3000]` — 서버 컨테이너 안에서([deploy.md §11](deploy.md#11-krx-open-api-일별-이력-601)).
  하루 한도 안에서 나눠 돌리면 남은 날부터 이어 받는다. 옵션(`opt_bydd_trd`)은 하루 수천 행이라 오래 받을수록 DB 가 커진다.
- 키가 없으면(로컬 · 테스트) 아무것도 묻지 않는다. 테스트는 KRX 형식대로 손으로 만든 응답(`backend/tests/fixtures/krx/`, 키 · 헤더 없음)만 쓴다.

## 후속

VKOSPI 이력(`drvprod_dd_trd`) · 선물 미결제(`fut_bydd_trd`) · 방향 판정 이력 · ETF 순자산(`etf_bydd_trd`) 은 #602 ~ #605 에서 이 표를 읽는다.
