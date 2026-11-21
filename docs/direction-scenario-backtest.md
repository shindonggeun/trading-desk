# 네 기간 롱·숏 과거 승률 재생성 (#796)

분석 상세는 `plan.horizons[기간].scenarios`에 방향별 과거 성과를 저장한다. 화면은 승률, 표본 부족, 집계 없음을 표시한다. 초기 번들의 `entry_now_swing.json`은 `direction_stats`가 생기기 전에 만들어졌고, 나머지 세 기간 리포트는 아직 생성되지 않았다. [백엔드 #802](https://github.com/shindonggeun/trading-desk/pull/802) · [프론트 #813](https://github.com/shindonggeun/trading-desk/pull/813)

## 재생성 입력

| 기간 | 기본 봉 | 보조 봉 | 기본 봉 준비 / 결과 추적 길이 |
| --- | --- | --- | ---: |
| `scalp` | 1분 | 일봉 | 200 / 400봉 |
| `intraday` | 5분 | 일봉 | 390 / 160봉 |
| `short_swing` | 60분 | 일봉 | 390 / 80봉 |
| `swing` | 일봉 | 주봉 | 200 / 40봉 |

- 기본 바스켓은 `SOXL`, `TQQQ`, `NVDA`, `QQQ`, `005930.KS`, `000660.KS`다. 각 종목의 정규 세션 비수정 OHLCV와 시각이 필요하다. 봉 스냅샷과 종목별 실제 시작·종료일을 보관한다.
- 기본 계획 시작일은 2019-01-01이다. 현재 코드가 사용하는 Yahoo 장중 이력 상한은 1분봉 7일, 5분봉 60일, 60분봉 730일이다. 장중을 2019년부터 평가하려면 별도로 보관된 봉이 필요하다. 조회 가능 기간만 평가하면 실제 시작일을 따로 기록한다.
- 시장 위험 게이트의 지수·시장 바스켓 일봉과 VIX 일봉도 필요하다. CLI의 `notes`에서 누락·건너뜀과 각 종목의 봉 범위를 함께 확인한다. 공식 재생성을 시작하기 전에 필요한 데이터가 모두 있는지 확인한다.

## 로컬 실행

보관 봉은 `<입력 폴더>/<종목>/<프레임>.json`에 저장한다. 예를 들어 `QQQ/5m.json`은
`at`(UTC 또는 오프셋이 있는 ISO 8601 시각), `open`, `high`, `low`, `close`, `volume`의 배열이다.
봉을 시간순으로 저장하고 같은 시각은 한 번만 넣는다. `backend`에서 다음과 같이 입력 범위를 먼저 확인한다.

```powershell
python -B -m desk.cli entry-backtest --check-input D:/market-data/ohlcv --from 2019-01-01 --through 2026-11-20
```

사전검사는 기본 바스켓과 네 기간의 필수 프레임을 확인한다. `--horizon`을 반복해 검사할 기간을 고를 수 있다.
각 종목의 거래소 현지 날짜로 시작·종료 범위를 비교하고, 주봉은 ISO 주차로 비교한다. 누락된 종목과 프레임,
잘못된 봉, 부족한 날짜 범위를 출력한다. 통과하면 종료 코드 0, 부족하면 2다. 이 모드는 로컬 파일만 읽고 종료한다.
요청 날짜 사이의 봉 연속성, 준비·추적 창의 표본 수, 위험 게이트·VIX는 리플레이 전에 별도로 확인한다.

전체 바스켓 리플레이는 검증 슬롯을 받은 뒤 별도 워크트리의 `backend`에서 실행한다. 표준 CLI는 Yahoo에서 시세를 읽고 로컬 JSON을 쓴다. 아래 예시는 2019년 이후 스윙 리포트의 명령이다. 장중 기간은 필요한 이력을 확보한 경우에만 로컬 봉 로더로 재현한다.

```powershell
python -m desk.cli entry-backtest --from 2019-01-01 --horizon swing
```

이 명령의 산출물은 `desk/data/backtest/entry_now_<기간>.json`과 체결률 보정용 `desk/data/entry_reach.json`이다. 작업용 워크트리에서 리포트와 체결률 변경을 각각 검토한다. 고정 입력을 다시 쓸 때는 `entry_now.gather(..., fetch=...)`에 `(ticker, horizon, start) -> (primary, secondary, notes)` 형태의 로컬 봉 로더와 같은 시점의 `vix`·`risk` 입력을 넘긴 뒤 `entry_now.replay`, `entry_now.compare` 순서로 실행한다.

## 결과 검증

1. 파일명의 기간과 JSON의 `horizon`이 같은지 확인한다. JSON의 `costs_bps`는 진입·청산 각각에 적용하는 편도 비용이다. 기본값은 10bp이며, 승리는 비용을 뺀 `r_net > 0`이다.
2. `direction_stats.rules`를 생성 코드의 `desk.backtest_reports.rules_fingerprint()`와 대조한다. 장중 봉 수집 상태와 별개로, 배포 코드의 규칙 해시와 리포트 해시가 같아야 앱이 집계를 읽는다.
3. `cut`은 계획일을 시간순으로 나눈 검증 시작일이다. 집계에는 그날 이후 전체 추적 창을 가진 계획의 체결·청산 거래만 들어간다. `cells`의 키는 `방향|셋업|플레이북 장세`이며 종목·날짜·키가 같으면 한 건만 센다. 다른 셋업이나 장세 표본으로 대체하지 않는다.
4. 셀별로 `cut <= start <= end`, `0 <= wins <= n`을 확인한다. `direction_stats.lookup`은 정확히 맞는 셀이 30건 이상이면 `available`과 `wins / n`, 1~29건이면 `insufficient`와 빈 승률, 파일·해시·키가 맞지 않으면 `unavailable`을 반환한다. 표본은 과거 바스켓 거래이며 같은 기간의 종목 간 움직임이나 거래가 겹칠 수 있다.
5. 이전 분석 결과는 저장 시점의 내용을 담는다. 새 리포트로 표시를 점검할 때는 현재 리포트로 만들어진 응답이나 통제된 시험 데이터를 사용한다.

```powershell
python -m pytest -q -p no:cacheprovider tests/test_direction_stats.py tests/test_entry_now.py tests/test_all_horizons.py tests/test_backtest_reports.py
```

위 테스트는 비용 차감 승리 집계, 기간·방향·장세가 맞는 표본 조회, 표본 부족 및 집계 없음 응답, 이전 분석 호환을 확인한다.

## 2026-11-21 로컬 확인

- `d6756ce`에서 QQQ 1종목의 스윙 데이터를 2025-01-01부터 10봉 간격으로 소규모 리플레이했다. 실제 계획일은 2025-01-03~2026-09-23, 검증 시작일은 2026-03-18이었다. 편도 10bp, 규칙 해시 `71eaf60c1162`, 출력 형식과 조회를 확인했다. 나온 두 셀은 표본 부족이었다.
- 같은 날 QQQ 장중 봉을 2019-01-01부터 요청했을 때 첫 봉은 1분봉 2026-11-16, 5분봉 2026-08-31, 60분봉 2023-12-27이었다. 요청한 기간을 덮는 보관 봉을 확보할 때까지 전체 바스켓 재생성과 번들 반영은 보류한다.
