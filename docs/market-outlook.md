# 미국 시장 관점 조회 (#903, #904)

거시 브리핑은 기존 뉴스 테마 계약을 유지한다. 별도
`GET /api/macro/market-outlook?market=us`는 같은 세션 쿠키를 사용하는 읽기 경로다.
세션이 없으면 401, `us` 이외의 시장은 422를 반환한다. 시장을 생략하면 `us`다.
원천 일부가 실패해도 나머지가 응답할 수 있도록 200 안에서 원천 및 지표 상태를 제공한다.

| 필드 | 의미 |
| --- | --- |
| `market`, `as_of` | `us`와 응답을 평가한 UTC 시각. 원천의 관측·발행 시각은 아래 각 항목에 있다. |
| `views[]` | `id`, `expert`, `organization`, `source`, `source_url`, `date_source_url`, `spoken_on`, `published_on`, `stance`, `horizon`, `summary`, `basis`, `change`, `positions`, `age_days`, `status`. |
| `filings[]` | Duquesne의 최신 등록 분기. `fund_id`, `name`, `cik`, `quarter`, `period_of_report`, `filed_at`, `form`, `accession`, `source_url`, `publication_lag_days`, `holdings_status`, `previous_quarter`, `holdings`. |
| `regime` | `state`, `supporting`와 `opposing`(지표 ID 배열), `next_checks`(한국어 확인 조건), `evidence[]`. |
| `sources` | `views`와 `filings` 섹션 각각의 `status`와 `reason`. SEC는 백그라운드 확인 시각 `checked_at`도 제공한다. |

## 출처와 기준일

- 관점은 원문 링크를 확인한 뒤 저장소에서 한국어로 요약한 기록이다. 뉴스 검색 결과를 자동으로
  인터뷰 발언으로 바꾸거나 원문 영상·문서를 재배포하지 않는다. 날짜는 원천의 현지 날짜다.
  발언일과 공개일은 각각 확인된 값만 표시한다. 둘 다 확인할 수 없으면 `unverified`이고,
  더 이른 날짜 기준 90일을 넘으면 `stale`이다. 확인된 비교 대상이 없으면 `change: null`이다.
  Duquesne 발표 일정은 2026-11-17이고 영상 공개일과 구리 포지션의 시점은 미확인이다.
  벤 스나이더의 기관 공개 요약은 2026-11-19로, 별도의 발언일을 넣지 않는다.
  두 관점은 방향과 전망 기간이 다르므로 배열에 함께 남긴다.
  [TomorrowX 일정](https://tomorrowxsummit.com/) · [공식 영상](https://www.youtube.com/watch?v=6r3D-CMJkuI) ·
  [Goldman Sachs 공개 글](https://www.goldmansachs.com/insights/articles/us-stocks-predicted-to-extend-historic-rally-as-gains-moderate)
- 13F는 기존 SEC 백그라운드 갱신의 캐시를 재사용한다. Duquesne의 CIK는 `0001536411`이다.
  기본 단일 프로세스(`DESK_ROLE=all`)에서 해당 캐시를 사용한다. 분리된 API 역할에
  갱신 스냅샷이 없으면 기존 기관 공시 조회와 같이 결측으로 표시한다.
  2026년 3분기 예시는 2026-09-30 기준, 2026-11-16 제출이다. 분기말과 제출 사이 날짜 차이는
  `publication_lag_days`이다. 새로운 분기나 수정 공시가 캐시에서 발견되면 그 접수 번호 기준으로
  바뀐다. 이전 분기의 수정도 보유 변화 계산의 캐시 키에 반영한다. 일치하는 보유 계산이 아직
  없으면 `holdings_status: missing`, `holdings: []`이며, 비교할 직전 분기가 없으면
  `previous_quarter: null`이다. 원천 최신 확인이 실패하거나 48시간을 넘은 캐시에는 `stale`을 붙인다.
  보유는 공식 보고 대상 증권과 공시된 옵션 행 및 전 분기 비교로 한정되며 전체 포트폴리오,
  실시간 거래, 인터뷰 포지션 또는 미보고 숏을 여기서 알 수 없다.
  [Duquesne 13F EDGAR 제출 정보](https://www.sec.gov/Archives/edgar/data/1536411/000153641126000011/0001536411-26-000011-index.html) ·
  [SEC 13F 범위 FAQ](https://www.sec.gov/rules-regulations/staff-guidance/frequently-asked-questions-about-form-13f)

## 장세 판정

| `state` | 조건 |
| --- | --- |
| `downtrend` | 60거래일 종가 고점 대비 5% 이상 낙폭, 20·50일선 하락 구조, 5거래일 반등 기준 미달. 변동성·시장 폭·신용 중 신선한 악화 근거가 하나 이상 필요하며 같은 보조 지표 집합에서 신선한 개선 근거가 있으면 유보한다. |
| `rebound_candidate` | 낙폭이 남은 하락·중립 구조에서 5거래일 종가 +2% 이상, 별도 시장 폭·신용·변동성 중 개선이 있으며 악화 증거와 충돌하지 않는다. |
| `recovery_watch` | 최근 20거래일에 위 낙폭 조건을 충족했던 뒤 종가 > 20일선 > 50일선과 20일선 상승이 관측된다. 신선한 시장 폭 또는 신용 개선이 필요하고 변동성 등 보조 지표의 악화가 있으면 유보한다. |
| `undetermined` | 자료·과거 봉이 부족하거나 독립 지표에서 방향을 확인할 수 없거나 위 조건에 맞지 않는다. 확인 가능한 개선·악화 근거를 함께 표시한다. |

`evidence`는 지표별 `id`, `value`, `unit`, `direction`, `details`,
`source`, `observed_at`(원천 관측 시각), `fetched_at`(데스크 조회 시각),
`age_days`, `status`, `reason`을 담는다. `index_trend`, `index_drawdown`,
`index_rebound`는 S&P 500 일봉을 사용한다. `volatility`는 Cboe VIX의 5거래일 변화이며,
−2pt 이하는 개선, +3pt 이상은 악화로 본다. 지수와 VIX는 기존 가격 조회의 공급자 표기와
마감 일봉을 사용한다. `breadth`와 `credit_spread`는 승인된 실측 공급자가 연결되기 전
`disabled/source_unconfigured`로 받는다. 지수의 상대 수익률을 시장 폭이나 회사채
신용 스프레드의 실측치로 대입하지 않는다.

신선한 `available` 지표만 판정에 쓴다. 세션 마감에 뒤처진 `stale`, 관측이 없거나 표본이
짧은 `missing`, 원천 오류·시간 초과 `error`, 미설정 `disabled`도 이유와 함께 표시한다.

- `as_of`는 조회가 끝난 후 고정한 공통 UTC 시각이다. 지수와 VIX는 출처가 있고 조회 시각이
  `as_of` 이내이며 해당 일봉의 마감 기준 시각이 조회 시각 이내인 경우에 사용한다.
  조회 시각이 없거나 확인되지 않거나 미래이면 `missing/invalid_observation`이다.
- S&P 500은 16:00 ET, VIX는 16:15 ET를 일봉에 적용하는 보수적 마감 기준으로 쓰며
  단축장에도 같은 시각을 적용한다. 지수의 완료 거래일이 VIX보다 앞서면 직전 거래일 VIX를
  `stale/latest_session_missing`으로 표시하고 판정에서 제외한다. 이 시각들은 원천의 최초
  공표 시각을 확인한 값이 아니다. [NYSE 거래 시간 및 휴장일](https://www.nyse.com/trade/hours-calendars) · [Cboe VIX 방법론](https://cdn.cboe.com/resources/indices/governance/Volatility_Index_Methodology_Cboe_Volatility_Index.pdf)
- 시장 폭·신용 스프레드는 미국 개장일의 관측이며 `observed_at ≤ fetched_at ≤ as_of`와
  관측 거래일의 보수적 지수 마감 이후 조회를 모두 확인한다. 해당 조건을 확인할 수 없으면
  `missing/invalid_observation`이다. 발표 지연을 고려해 지수의 최근 완료 거래일과 바로
  전 개장일의 값까지 허용하고 더 오래된 값에는 `stale/publication_lag`를 표시한다.

발언·13F와 장세 판정은 서로 다른 데이터 흐름이며 시장 상태는 지표 기반의 관찰 구분이다.
