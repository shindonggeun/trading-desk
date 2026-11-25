# NQ=F 주봉 과매도 과거 표본 검증 (#914)

2026-11-24에 #675의 기존 Yahoo `NQ=F` 주봉 보관본을 #945 규칙으로 재연산했다.
사용 가능한 798주에서 기본 조건 충족은 0건이었다. 4주·12주 수익률과 최대 추가 하락을
산출할 전환 표본이 없었다.

## 원천과 시점

| 원천 | 가져올 수 있는 근거 | 비교 범위 |
| --- | --- | --- |
| Yahoo Finance `NQ=F` | 기존 #675의 로컬 OHLCV 보관본을 사용했다. `auto_adjust=False`로 읽은 값이며 행의 시각은 뉴욕 월요일을 식별한다. | 사용 가능한 범위에서 같은 보관본의 가격끼리 비교한다. 주별 당시 공급·정정 시각, 만기 연결, 롤오버 보정, `Close`의 정산가 여부는 확인되지 않았다. 이번 검증에서 새 자동 수집은 하지 않았다. [#675 자료·해시](references/nq-weekly-support.md#재현) · [Yahoo 이력](https://finance.yahoo.com/quote/NQ%3DF/history/) · [Yahoo 이용약관](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html) |
| TradingView `NQ1!` | #675의 사용자 제공 캡처를 참고했다. TradingView는 과거 계약 자동 조정 옵션과 일봉 이상에서 정산가·최종 체결가 선택을 제공한다. | 캡처의 설정과 연결 계약이 확인되지 않아 전체 주봉을 같은 시계열로 볼 수 없다. 이번 재연산에서 화면 자료를 수집하지 않았다. [과거 계약 조정](https://www.tradingview.com/support/solutions/43000685266-how-can-i-enable-backadjustment-for-continuous-futures/) · [종가 선택](https://www.tradingview.com/support/solutions/43000685268-can-i-switch-settlement-and-last-prices-as-close-for-futures/) · [TradingView 정책](https://www.tradingview.com/policies/) |
| CME NQ 개별 만기 및 DataMine | 공식 문서로 거래시간, 일일·최종 정산 절차와 롤 일정을 확인했다. CME 연속 가격 상품 설명은 Active·Front 선택, 일별 정산가와 라이선스 경로를 제시한다. | 이번 검증에서 이용 권한이 확인된 만기별 과거 OHLC·롤·공급 시각 사본은 확보하지 못했다. 정산가만으로 주간 최저가를 계산할 수 없다. [정산 절차](https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457222172/Nasdaq-100) · [CME 롤 일정](https://www.cmegroup.com/trading/equity-index/rolldates.html) · [연속 가격 데이터](https://www.cmegroup.com/market-data/cme-group-continuous-price-series.html) · [CME 데이터 이용 조건](https://www.cmegroup.com/trading/market-data-explanation-disclaimer.html) |

- 평시 CME Globex 주 마지막 거래 종료는 금요일 17:00 ET다. #945는 그 뒤 일요일
  18:00 ET에 도달하고 다음 뉴욕 월요일 라벨을 포함한 응답을 받을 때까지 확정을 보류한다.
  휴일의 실제 일정과 정정·공급 시각은 본 역사 자료에서 확인되지 않는다. [CME NQ 거래시간](https://www.cmegroup.com/markets/equities/nasdaq/e-mini-nasdaq-100.timeAndSales.html)
- 대표 만기의 일일 정산은 현재 14:59:30~15:00:00 CT를 기준으로 산정하며 거래가 없을 때
  별도 대체 규칙을 적용한다. CME 신고에 따르면 NQ는 2020-10-26 거래일부터 15:15에서
  15:00 CT로 바뀌었다. 분기 만기의 최종 정산은 해당 만기의 특별개장지수(SOQ) 기준이다.
  거래 종료와 두 정산 기준을 Yahoo 주봉의 종가나 수신 시각으로 대입하지 않았다. [정산 절차](https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457222172/Nasdaq-100) · [2020 정산 변경 신고](https://www.cftc.gov/filings/orgrules/rule092420cmedcm001.pdf)

## 실제 가격 보관본 재연산

보관본의 800행에서 기준일 이후 진행 주를 빼면 #675에 기록된 799행
(2011-08-01~2026-11-16) 및 정규화 SHA-256과 일치한다. 분석 기준은
2026-11-22 05:45 KST다. 2026-11-16 주는 그 기준에 #945의 다음 일요일 경계를 넘지
않아 분석에서 제외했다. 사용할 수 있는 주는 2011-08-01~2026-11-09의 798개다.
보관본에 주별 당시 수신 기록이 없어 이 결과는 현재 규칙으로 만든 사후 재연산이다.
당시에도 같은 봉·조건을 검증할 수 있었는지는 미확인이다.

설정은 기본값인 Wilder RSI(14) ≤ 30, 볼린저·채널·피보나치 지지 중 최소 1개 충족으로
고정했다. 전체 이력은 오프라인에서 처음부터 워밍업했고, 별도 비교는 앱의 최대 120개
원천 행과 104주 표시 범위를 적용했다. 기본 규칙과 차트 캡처의 Buy 1·Buy 2 설정은
서로 확인할 수 없으므로 캡처의 표시나 확률은 집계하지 않았다.

| 범위 | 사용 주 | 평가 주 | 판정 불가 주 | RSI ≤ 30 | 전체 충족 | 유효 전환 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 전체 보관본 | 798 | 798 | 15 | 3 | 0 | 0 |
| 앱 최대 표시 범위 대조 (2024-11-18~2026-11-09) | 119 | 104 | 0 | 0 | 0 | 0 |

유효 전환은 직전 인접 주의 유효 미충족에서 충족으로 바뀐 주다. 후속 수익률은 해당
전환 주 종가 대비 정확히 N주 후 종가의 변화율이다. 최대 추가 하락은 그 종가 대비
다음 N개 주 최저가로 계산하며 하락이 없으면 0이다. 두 실제 가격 범위 모두 4주·12주
각각 전환 0건, 관측 0건, 후속 미확인 0건이다. 수익률 중앙값과 최대 추가 하락은
미산출이다. 52주 고점 대비 사전 15% 급락과 후속 15% 이상 하락을 관측한 전환 역시
0건이며, 이 건수에서 발생률을 추정할 수 없다. 2011-08-01 이전, 2026-11-09 이후,
정산가 기반 만기별 성과, 롤에 따른 차이와 당시 공개 시점은 이 표에서 미검증이다.

### 보관본 재현

`backend/`에서 아래 Python을 기존에 사용할 수 있는 로컬 JSON 파일 경로를 인자로 주어
실행한다. 거래소·Yahoo에 접속하거나 원본 행을 출력할 필요가 없다. #945의 연구 함수가
입력 날짜 검증, RSI·지지 판정과 결과 집계를 담당한다. 확인되지 않은 `fetched_at`은
채우지 않는다.

```python
import hashlib
import json
import sys
from datetime import date, datetime
from pathlib import Path

from desk import nq_weekly_research as nq
from desk import nq_weekly_signal as signal

cutoff = datetime.fromisoformat("2026-11-22T05:45:00+09:00")
rows = [
    row for row in json.loads(Path(sys.argv[1]).read_bytes())
    if date(2011, 8, 1) <= nq._monday(row["at"]) <= date(2026, 11, 16)
]
canonical = (json.dumps(rows, sort_keys=True, separators=(",", ":")) + "\n").encode()
expected = "ee5da88fa9fe189f476530fb233d8e00d6cb714273b844196565085f795a23aa"
if len(rows) != 799 or hashlib.sha256(canonical).hexdigest() != expected:
    raise ValueError("The input does not match the #675 archived snapshot")

for name, source_rows, weeks in (
    ("full_archive", rows, len(rows) - 1),
    ("app_limit", rows[-signal.FETCH_WEEKS:], signal.MAX_HISTORY_WEEKS),
):
    labels = {nq._monday(row["at"]) for row in source_rows}
    eligible = []
    for bar in nq._closed(source_rows, cutoff):
        monday = date.fromisoformat(bar["at"])
        if monday + nq.WEEK in labels and cutoff >= signal._confirmed_at(monday):
            eligible.append(bar)
    bands, rsis = signal._series(eligible)
    _, study = signal._replay(
        signal._Weekly(None, eligible, None, None), bands, rsis, signal.DEFAULT_SETTINGS, weeks,
    )
    rsi_hits = sum(rsi is not None and rsi <= signal.DEFAULT_SETTINGS["rsi_limit"] for rsi in rsis[-weeks:])
    print(name, json.dumps({"rsi_hits": rsi_hits, **study}))
```

## 합성 대조와 운영 인계

#945의 61주 fixture는 상태 전환을 검증하기 위해 볼린저 조건만 켰다. 아래 두 기간에서
유효 전환 2건이 모두 관측됐고, 수익률 음수 2건과 사후 추가 하락 15% 이상 1건이
집계됐다. 기간이 아직 끝나지 않은 별도 테스트는 수익률과 최대 추가 하락을 미확인으로
유지한다. [회귀 코드](../backend/tests/test_nq_weekly_signal.py)

| 합성 기간 | 관측 / 전환 | 수익률 중앙값 | 최대 추가 하락 |
| --- | ---: | ---: | ---: |
| 4주 | 2 / 2 | −3.81% | 53.27% |
| 12주 | 2 / 2 | −11.43% | 53.27% |

`backend/`에서 같은 합성 집계를 다시 출력할 수 있다.

```python
import json
from runpy import run_path

from desk import nq_weekly_signal as signal

fixture = run_path("tests/test_nq_weekly_signal.py")
last = fixture["LAST"]
now = fixture["_scheduled_confirmation"](last)
rows = fixture["_rows"]()[:last + 2]
rows[44]["low"] = 50
view = signal.view(fixture["_source"](rows, now), fixture["_rules"](), now, weeks=104)
print(json.dumps(view["study"]["horizons"]))
```

운영 인계 항목은 승인된 텔레그램의 실제 시험 수신과 첫 실신호 발송 확인이다.
이번 검증에는 운영 채널이나 설정 변경이 포함되지 않는다.
