# 종목별 뉴스·공시 심리 관찰 (#934)

`GET /api/stock-social?ticker=SOXX&days=7`는 기존 세션 인증으로 조회한다.
기본 종목은 `SOXX`이고 기본 기간은 7일이다. 기간은 1~30일이며 잘못된 종목 형식이나
기간은 422를 반환한다. 요청 시 뉴스 원문을 방문하거나 분석·모델 작업을 시작하지 않는다.

## 화면 계약 (#936)

- `ticker`, `days`, `generated_at`, `method`, `coverage`, `news`, `community`,
  `sources[]`, `items[]`가 최상위 필드다. `generated_at`은 응답 산정 기준 UTC 시각이다.
- `method.version`은 `headline-rules-v1`, `baseline`은 50,
  `min_samples`는 3이며 `min_publishers`는 2다. `coverage.requested_days`와
  `news_max_days`를 함께 표시할 수 있다. GDELT 뉴스는 기존 최대 72시간의 제한된 캐시에 있다.
- `news`와 `community`는 `status`, nullable `score`, `sample_count`, `reason`,
  `updated_at`을 담는다. 뉴스는 `publisher_count`도 제공한다. 점수가 없으면 `score: null`이다.
  `available`, `partial`, `insufficient`, `stale`, `unavailable`을 구분한다.
  `partial`은 일부 스냅샷에 문제가 있어도 남은 최신 뉴스가 산정 기준을 충족했다는 뜻이다.
  뉴스가 충분해도 공시와 커뮤니티의 상태는 각 출처에서 따로 확인한다.
- `sources[]`는 `id`, `status`, `reason`, `updated_at`, `prior_http_status`를 제공한다.
  과거 HTTP 코드는 당시 관측이며 요청할 때 다시 확인한 결과가 아니다.
  `updated_at`은 출처 상태의 기준 시각이다. GDELT는 뉴스 신선도 기준 시각을, SEC는 응답 조회
  기준 시각을 반환한다. 개별 공시의 접수 시각과 날짜는 `items[]`에서 확인한다.
- `items[]`는 `kind`(`news` 또는 `filing`), `source`, `publisher`, `title`,
  `summary`, `url`, `at`, `filed_date`, `time_basis`, `relevance`, `tags`,
  `sentiment`, `reason`, `weight`, `contribution`, `stale`로 구성된다.
  `time_basis`가 `gdelt_seen`이면 `at`은 GDELT의 발견 시각이다. `edgar_acceptance`는 SEC
  접수 시각을 뜻하며, 날짜만 있는 `edgar_filing_date`는 `at` 대신 `filed_date`를 사용한다.
  공시나 계산에 반영하지 않은 항목은 `weight`와 `contribution`이 null이다.
- `relevance`가 `direct`인 항목은 종목이나 발행사를 직접 식별한다. `indirect`는
  SOXX·SOXL과 관련된 반도체 산업·기업 자료다. 종목과 테마의 연결이 현재 ETF 편입을 뜻하지 않는다.

## 출처와 이용 범위

| 출처 | 사용 범위와 실제 확인 |
| --- | --- |
| GDELT DOC | 기존 거시 뉴스 캐시에서 제목, 매체, 링크와 발견 시각을 읽는다. 최대 72시간, 주제별 건수 제한이 있다. 개발 환경에서 실행한 공개 DOC 조회는 성공하지 않아 캐시 계약과 합성 응답으로 검증했다. [GDELT DOC 기사 목록](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/amp/) |
| 기존 종목 뉴스, Google News RSS, 뉴스 다이제스트 | 관련 경로와 캐시를 확인했다. Google·Yahoo 데이터는 새 용도에 대한 표시 조건을 확인하지 못해 이번 응답에 포함하지 않는다. 기존 모델 다이제스트와 별도로 제목에 대한 고정 규칙을 적용한다. [Google News 이용 조건](https://www.google.com/intl/en_us/terms_google_news.html) |
| SEC EDGAR | 공개 submissions 메타데이터를 발행사로 연결한다. 등록된 사용자 에이전트와 기존 캐시를 사용하며 요청을 제한한다. SEC에서 공개한 NVIDIA 제출 JSON 접근을 확인했다. 공시 유형과 접수 날짜·원문 링크를 제공한다. [EDGAR API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) · [SEC Fair Access](https://www.sec.gov/about/developer-resources) · [NVIDIA 제출 메타데이터](https://data.sec.gov/submissions/CIK0001045810.json) |
| Open DART / 회사·운용사 IR | 공시 검색은 발급된 DART 키가 필요하다. 허용된 키·발행사별 RSS를 확인한 연결이 없어 별도 상태로 둔다. [Open DART 공시검색](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001) |
| Reddit | 정식 API 이용·표시 승인이 확인된 연결이 없다. #861의 Reddit RSS 404를 과거 관측으로 반환한다. [Reddit Data API 조건](https://redditinc.com/policies/data-api-terms) |
| Stocktwits | 공식 등록 페이지는 신규 애플리케이션 접수를 중단했다고 안내한다. #861의 403을 과거 관측으로 반환한다. [Stocktwits 개발자 페이지](https://api.stocktwits.com/developers) · [Stocktwits 이용 조건](https://stocktwits.com/about/legal/terms/) |

기사 전문과 소셜 게시물은 이 기능의 응답에 들어가지 않는다. 제목에 대한 짧은 분류 근거를 제공하고
원문은 링크로 연다. SEC 공시는 서식·접수 메타데이터에 근거하며 그 자체는 득점 표본에 포함하지 않는다.
SEC의 종목·CIK 연결이 없는 경우 `symbol_unmapped`를 반환한다. ETF 신탁의 제출 자료에서 개별
시리즈나 클래스를 식별하는 별도 조회는 제공하지 않는다.

## 점수 표기

중복을 제거한 관련 헤드라인에 긍정(+1), 부정(-1), 중립(0)을 부여한다. 같은 제목에 긍정과 부정
근거가 모두 있으면 양쪽을 근거에 남기고 중립으로 둔다. 뉴스 점수는 관련도·최근성·주제에 따라
가중 평균을 구한 뒤 `50 + 50 × 가중평균`으로 0~100에 맞춘다. 직접 기사는 1, 간접 기사는
0.5에서 시작한다. 최근성 가중치는 72시간에 걸쳐 1에서 0.25로 낮아지며 주제 태그가 있는
헤드라인에는 1.25를 곱한다. 매체당 최신 표본 최대 2건과 총 가중치 최대 60%를 적용한다.
기존 캐시의 테마 갱신 시각은 장외에도 전진할 수 있어, 뉴스 신선도는 최신 유효 헤드라인 발견
시각과 테마 갱신 시각 중 이른 시각을 기준으로 삼는다. 이 시각이 6시간을 넘긴 테마의
헤드라인은 근거 목록에만 표시한다.

50은 충분한 표본에서 중립이거나 신호가 상쇄된 결과다. 뉴스 표본, 출처의 범위와 산정 버전을 함께
표시한다. 방향은 헤드라인 전체에 나타난 표현에서 구한다. 두 회사가 함께 언급된 제목에서는
개별 회사에 대한 평가로 표시하지 않는다.

예를 들어 SOXX 실적 관련 제목은 `direct`, NVIDIA 실적 제목은 SOXX에서 `indirect`로 분류한다.
서로 다른 매체 두 곳에서 관련 제목 세 건 이상을 확보하면 점수를 표시할 수 있다. 같은 제목이 여러
곳에 실려도 중복 규칙에 걸리면 한 건만 남는다. 과거 커뮤니티 HTTP 코드와 공시 건수는 뉴스 점수에
영향을 주지 않는다.
