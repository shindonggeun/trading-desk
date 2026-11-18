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

`code` 는 하루 안에서 행을 가르는 필드다(`krx_daily.code`, 여럿이면 `|` 로 잇는다). 선물 · 국채는 `MKT_NM`(정규 · 야간 같은 시장 구분)이
같은 종목 코드를 되풀이할 수 있어 함께 넣었다. code 가 비거나 겹치는 행은 버리고 WARNING 로그와 `dropped` 수로 남긴다 — 실응답에서 보이면 `APIS` 의 code 를 늘린다.

| API ID | 분류 | 이름 | code | 필드 |
|--------|------|------|------|------|
| `kospi_dd_trd` | idx | KOSPI 시리즈 일별시세정보 | `IDX_NM` | BAS_DD, IDX_CLSS, IDX_NM, CLSPRC_IDX, CMPPREVDD_IDX, FLUC_RT, OPNPRC_IDX, HGPRC_IDX, LWPRC_IDX, ACC_TRDVOL, ACC_TRDVAL, MKTCAP |
| `kosdaq_dd_trd` | idx | KOSDAQ 시리즈 일별시세정보 | `IDX_NM` | (KOSPI 시리즈와 같다) |
| `drvprod_dd_trd` | idx | 파생상품지수 시세정보 (VKOSPI 등) | `IDX_NM` | BAS_DD, IDX_CLSS, IDX_NM, CLSPRC_IDX, CMPPREVDD_IDX, FLUC_RT, OPNPRC_IDX, HGPRC_IDX, LWPRC_IDX |
| `fut_bydd_trd` | drv | 선물 일별매매정보(주식선물 외) | `ISU_CD`+`MKT_NM` | BAS_DD, PROD_NM, MKT_NM, ISU_CD, ISU_NM, TDD_CLSPRC, CMPPREVDD_PRC, TDD_OPNPRC, TDD_HGPRC, TDD_LWPRC, SPOT_PRC, SETL_PRC, ACC_TRDVOL, ACC_TRDVAL, ACC_OPNINT_QTY(미결제약정) |
| `opt_bydd_trd` | drv | 옵션 일별매매정보(주식옵션 외) | `ISU_CD` | BAS_DD, PROD_NM, RGHT_TP_NM(CALL/PUT), ISU_CD, ISU_NM, TDD_CLSPRC, CMPPREVDD_PRC, TDD_OPNPRC, TDD_HGPRC, TDD_LWPRC, IMP_VOLT(내재변동성), NXTDD_BAS_PRC, ACC_TRDVOL, ACC_TRDVAL, ACC_OPNINT_QTY |
| `etf_bydd_trd` | etp | ETF 일별매매정보 | `ISU_CD` | BAS_DD, ISU_CD, ISU_NM, TDD_CLSPRC, CMPPREVDD_PRC, FLUC_RT, NAV, TDD_OPNPRC, TDD_HGPRC, TDD_LWPRC, ACC_TRDVOL, ACC_TRDVAL, MKTCAP, INVSTASST_NETASST_TOTAMT(순자산총액), LIST_SHRS, IDX_IND_NM, OBJ_STKPRC_IDX, CMPPREVDD_IDX, FLUC_RT_IDX |
| `kts_bydd_trd` | bon | 국채전문유통시장 일별매매정보 | `ISU_CD`+`MKT_NM` | BAS_DD, MKT_NM, ISU_CD, ISU_NM, BND_EXP_TP_NM, GOVBND_ISU_TP_NM, CLSPRC, CMPPREVDD_PRC, CLSPRC_YD, OPNPRC, OPNPRC_YD, HGPRC, HGPRC_YD, LWPRC, LWPRC_YD, ACC_TRDVOL, ACC_TRDVAL |

**VKOSPI**: `drvprod_dd_trd` 의 `IDX_NM == "코스피 200 변동성지수"` 행, 종가는 `CLSPRC_IDX`(#602 가 읽는다).
이 이름은 공개 저장소 두 곳의 실응답 기반 파서([gomtang-dori/Korea-Gomtang-Index](https://github.com/gomtang-dori/Korea-Gomtang-Index) `src/lib/krx_dvrprod_index.py`,
[SJS9401/market-dashboard](https://github.com/SJS9401/market-dashboard) `kr_vkospi_backfill_local.py`)가 같게 쓴다. 둘 다 공백을 뺀 `코스피200변동성지수` 로도 맞추니
읽는 쪽은 공백을 지우고 비교한다. `IDX_CLSS` 값은 확인하지 못했다(픽스처의 값은 자리표시).

숫자는 `krx.num()` 으로 읽는다(`"1,234.5"` → 1234.5, `""` · `"-"` → None). 저장은 KRX 가 보낸 문자열 그대로(`payload`).

## 수집

- **스케줄**: 키가 있고 `DESK_MACRO_INTERVAL_SEC > 0`(뉴스 흐름과 같은 스위치, 기본 3600)이고 대본 실행(`DESK_FAKE_RUNS`)이 아니면 백엔드가 15분마다 서비스별로 최근 2주 중 빠진 영업일(KRX 휴장일 · 주말은 묻지 않는다)을 받는다.
  D 일은 익영업일 08:30 KST 전에는 묻지 않는다. 그러니 평일 08:30 ~ 08:45 사이에 전 영업일치가 쌓인다.
- **빈 응답**: 최근 7일 안의 영업일이 비면 아직 안 올라온 것으로 보고 3시간 뒤 다시 묻는다. 7일이 지난 날이 비면 휴장으로 적는다(`krx_days.rows = 0`).
- **거절(401 · 403)**: 그 서비스만 6시간 건너뛴다(키 · 이용 신청 확인). **한도(429)**: 그 패스의 나머지를 멈추고 자정(KST)까지 묻지 않는다.
  한 프로세스는 하루 9,000건까지만 쓴다 — 서버 스케줄과 CLI 백필은 **각자 센다**. 키 한도 10,000건은 둘이 나눠 쓰므로 백필은 `--max-calls` 로 넉넉히 남긴다.
  한도를 넘었을 때 정말 HTTP 429 로 오는지는 실호출로 확인하지 못했다(첫 배포 뒤 확인).
- **오류(5xx · JSON 아님 · 그 밖의 respCode)**: 그날만 건너뛰고 다음 날로 간다(3시간 뒤 다시). 3번 잇달아 실패하면 그 서비스의 패스를 멈춘다. 응답 문구(`respMsg`)는 로그에 남기지 않는다.
- **멱등**: `krx_days` 에 (서비스, 날) 이 있으면 다시 묻지 않는다. 같은 날을 다시 받으면 그날 행을 지우고 새로 쓴다(겹치지 않는다).
- **백필**: `python -m desk.cli krx backfill --api <ID|all> --from 2015-01-01 [--to YYYY-MM-DD] [--max-calls 3000] [--refetch-empty]` — 서버 컨테이너 안에서([deploy.md §11](deploy.md#11-krx-open-api-일별-이력-601)).
  하루 한도 안에서 나눠 돌리면 남은 날부터 이어 받는다. 거절 · 오류 · 한도로 멈추면 exit 1.
  `krx status` 는 서비스별 날 수 · 기간 · 행 수와 "달력상 개장일인데 행이 0" 인 날 수를 보인다 — 그런 날은 `--refetch-empty` 로 다시 받는다. 옵션(`opt_bydd_trd`)은 하루 수천 행이라 오래 받을수록 DB 가 커진다.
- 키가 없으면(로컬 · 테스트) 아무것도 묻지 않는다. 테스트는 KRX 형식대로 손으로 만든 응답(`backend/tests/fixtures/krx/`, 키 · 헤더 없음)만 쓴다.

## 후속

VKOSPI 이력(`drvprod_dd_trd`) · 선물 미결제(`fut_bydd_trd`) · 방향 판정 이력 · ETF 순자산(`etf_bydd_trd`) 은 #602 ~ #605 에서 이 표를 읽는다.

### VKOSPI (#602)

- 공포지수(`desk/fear.py`, `GET /api/market/fear`)는 `krx_daily` 의 `drvprod_dd_trd` 에서 `code` 가 공백을 모두 지우고 `코스피200변동성지수` 가 되는 행의 `CLSPRC_IDX` 를 최근 400일 읽는다(1시간 캐시).
- 값의 출처는 아래 순서로 정한다.
  1. 오늘 값은 한투(KIS)를 쓴다.
  2. 한투에 없는 어제 · 지난주 값은 KRX 종가로 채운다.
  3. 한투 키가 없거나 한투가 실패하면 KRX 의 마지막 종가(전 영업일)를 그 날짜로 보인다.
  4. 마지막 KRX 종가가 10일보다 오래되었거나 KRX 행이 없을 때만 KODEX 200 실현 변동성 추정을 쓴다.
- 응답 항목에 `origin`(값 · 어제 · 지난주 각각 `kis` · `krx` · `estimate`)과 `percentile_1y` 를 더한다. `percentile_1y` 는 오늘 값을 넣은 최근 252거래일 안에서 그 값 이하인 비율(0–100)이다. 과거 종가가 200일보다 적으면 `null` 이다. 값이 없는(오류) 항목에도 두 필드는 있다(`{}` · `null`). 툴팁 문구(`source`)도 출처를 밝힌다.
- 수집이 실패해 빠진 날(`krx status` 의 빈 날)이 있으면 어제 · 지난주 값이 한 세션 더 앞의 값이 될 수 있다 — 빠진 날은 백필로 채운다.
- 1년 백분위에는 최근 14개월이면 충분하다. 배포 뒤 서버에서 `krx backfill --api drvprod_dd_trd --from <14개월 전>` 을 한 번 돌린다(약 290건).
  2015년부터의 긴 이력(약 2,700건, 하루 예산 안)은 다른 후속(#603 ~ #605)과 검증에 쓸 때 받는다.

### 선물 미결제 · 베이시스 · 풋/콜 (#603)

`desk/futures_oi.py` 가 `fut_bydd_trd` · `opt_bydd_trd` 행에서 하루 한 줄(`futures_oi_daily`, Alembic 0021)을 만든다. KRX 는 다시 묻지 않는다.

- **고르는 행**
  - 선물: `PROD_NM` 이 공백을 빼고 `코스피200선물` 이고, `MKT_NM` 에 `야간` 이 없는 분기물.
  - 옵션: `PROD_NM` 이 `코스피200옵션` 인 월물(위클리 · 미니는 뺀다).
  - 만기월은 `ISU_NM` 의 YYYYMM 에서 읽는다. 첫 실응답에서 이름 형식을 확인한다.
- **최근월물**: 만기일(둘째 목요일, 휴장이면 앞 영업일)이 그날 이후인 가장 가까운 분기물이다. 만기일 당일도 그 월물이고, 한투 화면과 같다.
- **미결제 증감**
  - `oi_change` 는 같은 월물의 직전 영업일 값과 비교한다. `oi_total` 은 전 분기물 합이다. 그 증감은 이틀 모두 있는 월물로만 센다. 그래서 만기 다음 날 만기 월물 잔량이 목록에서 빠지는 것을 감소로 읽지 않는다.
  - 같은 월물 행이 둘이면(중복) 경고하고 미결제가 큰 쪽을 쓴다. 월이 둘 적힌 스프레드 행은 읽지 않는다.
  - `rolling` 은 만기 전 5영업일과 만기일이다. 직전 영업일 행이 없으면 증감은 비운다.
- **옵션 지표**
  - `pc_oi_ratio` 는 월물 옵션 전 만기의 풋 미결제 ÷ 콜 미결제다.
  - `atm_iv` 는 5영업일 이상 남은 가장 가까운 월물에서, 코스피200 에 가장 가까운 행사가의 콜 · 풋 IV 평균이다.
- **갱신**
  - 스케줄의 KRX 패스가 선물 · 옵션 행을 새로 쌓으면 최근 2주를 다시 만든다.
  - 같은 날의 한투 마감 스냅숏과 대조해, 미결제 차이가 max(1%, 300계약)를 넘거나 베이시스 차이가 0.5pt 를 넘으면 WARNING 로그를 남긴다.
  - 백필 뒤에는 `python -m desk.cli krx derive --from 2015-01-01` 을 돌린다(키가 필요 없다).
- **API**: `GET /api/market/futures/oi-history?days=250`(세션 필요, 1~1000일, 최신이 앞). 비면 `reason` 이 `no_key` 또는 `no_history` 다.
- **방향 판정 특징**: `FuturesOi.features_before(day, at)` 는 `day` 이전 행 가운데 판정 시각 `at` 까지 KRX 에 올라온 것(`krx.ready_at`, D+1 08:30 KST)과 20일 z-score 만 준다.
  - 그래서 07:30 1차 판정은 D-2 행을 본다. 학습과 운영이 같은 값을 본다.
  - 이름 형식이 달라 그날 행을 하나도 읽지 못하면 WARNING 로그를 남긴다.
