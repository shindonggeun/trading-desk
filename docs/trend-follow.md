# 일봉 추세추종 근거 (#977)

`GET /api/chart-read` 는 요청 시간봉에 `1d` 가 있으면 선택 필드 `trend_follow` 를 붙인다.
- 하락 뒤 첫 반등, 지지 회복, 저항 시험, 확정 종가 돌파, 재이탈을 구분한다.
- 같은 차트 조회의 일봉과 1d frame(채널 · 추세선)을 다시 쓰고, 다른 상품의 봉으로 대신하지 않는다.
- 코스피 · SK하이닉스 · 삼성전자만 나머지 두 종목의 일봉을 더 읽는다(선행 돌파 비교).
- 지난 봉의 산술이다. 예측도, 매수 · 매도 추천도 아니다.

계산은 `backend/desk/trend_follow.py`, 화면은 #978 이다.

## 확정봉

- 마지막 일봉은 장 마감에 원천 지연을 더한 시각이 지나야 확정으로 친다.
  - 국장: 15:30 KST + 20분
  - 미장: 정규장 마감 + 15분
  - CME 선물: 17:00 ET + 10분
- 그 전에는 마지막 봉을 빼고 읽고 `status: "in_progress"` 를 낸다. `as_of` 는 판정에 쓴 마지막 확정봉 시각이다.
- 스윙은 양쪽 3봉 피벗(`channels.SWING_K["1d"]`)이다. 오른쪽 3봉이 확정된 다음 봉부터만 쓴다. 판정하는 봉보다 뒤의 봉은 읽지 않는다.

## 진행 상태 `stage.state` / `stage.signal`

| state | signal | 조건 |
| --- | --- | --- |
| `breakout_confirmed` | `close_above` | 최근 20봉 안에 저항선 + 0.3 ATR 위 종가. 직전 봉은 선 아래 |
| `failed_breakout` | `close_back_below` | 돌파 뒤 돌파한 선 − 0.3 ATR 아래 종가 |
| `awaiting_confirmation` | `wick_touch` | 마지막 봉 고가가 가장 가까운 저항 − 0.25 ATR 에 닿고, 종가는 돌파 아래 |
| `first_rebound` | `support_reclaim` | 최근 3봉 안에 지지 − 0.3 ATR 아래 종가를 낸 뒤, 마지막 봉이 그 지지 위로 마감 |
| `first_rebound` | `first_up_bar` | 직전 20봉 최고 종가에서 3 ATR 넘게 하락한 뒤 처음 나온 강한 양봉(몸통 0.6 ATR 이상, 종가가 범위 상단 30%) |
| `awaiting_confirmation` | `null` | 위 반등이 최근 10봉 안에 있었고 아직 돌파 전 |
| `none` | `null` | 해당 없음 |

판정은 위에서부터 먼저 맞는 것을 쓴다.

- 저항으로 보는 선:
  - 1d frame 의 하락 추세선. 두 번째 앵커가 확정된 다음 봉부터 쓴다.
  - 최상위 채널의 상단 · 하단. 로그 직선이다.
  - 아직 종가로 넘지 않은 스윙 고점.
- 지지도 같은 방식이다. 이미 종가로 깨진 스윙 저점은 지지에서 빠진다.
- 추세 `trend.state`(`down` · `sideways` · `up`)는 차트 읽기의 20 · 50봉 EMA 순서(`chart_read.trend_state`)를 확정봉에 적용한 값이다.

## 핵심 가격 · 조건

- `key_prices.hold` 는 지켜야 할 가격이다.
  - 돌파 뒤: 돌파한 선
  - 지지 회복 뒤: 회복한 지지
  - 그 밖: 가장 가까운 지지
- `key_prices.break` 는 넘어야 할 가격이다.
  - 재이탈 뒤: 다시 넘어야 할 선
  - 그 밖: 가장 가까운 저항
- 두 가격 모두 10 ATR 안의 선만 쓴다. `distance_pct` 는 마지막 확정 종가 기준이다.
- `conditions[]`:

  | kind | id | 내용 |
  | --- | --- | --- |
  | `confirm` | `close_above_resistance` | 저항 위 종가 |
  | `confirm` | `hold_after_break` | 돌파 다음 봉도 선 위 |
  | `confirm` | `stochastic_80` | 스토캐스틱 80 도달 |
  | `confirm` | `rsi_divergence` | RSI 다이버전스 |
  | `invalidate` | `close_below_hold` | hold 아래 종가 |
  | `invalidate` | `close_back_below` | 재이탈 |

  `met` 이 `null` 이면 근거가 부족하다는 뜻이다.

## 근거 `evidence[]`

| kind | 정의 | meta |
| --- | --- | --- |
| `quarter_level` | 최근 60봉 확정 스윙 최고점 · 최저점 구간의 25% 되돌림 두 선(선형) | `ratio` · `high` · `low` · `scale=linear` · `tolerance_pct`(0.3 ATR) |
| `channel_quarter` | 1d frame 최상위 채널(두 피벗 + 평행 오프셋)의 폭 25% 보조선(로그). `prices` = 상단 · 상단 25% · 하단 25% · 하단, `anchors` = 기준 쪽 두 피벗 | `ratio` · `scale=log` · `channel_id` · `width_pct` |
| `fib_extension` | 마지막 확정 스윙 고점과 그 앞 스윙 저점의 상승 파동 1.414 · 1.618 · 2.618 확장(선형) | `ratios` · `scale=linear` · `wave_low` · `wave_high` · `log_prices`(로그 눈금 값) |
| `divergence` | 최근 120봉 확정 스윙 저점의 가격 저점 ↓ · RSI14 저점 ↑ 연속 횟수. 붙은 두 피벗(3봉 이내)은 하나로 본다 | `indicator=rsi14` · `pivot_k=3` · `comparisons` |
| `stochastic` | 앱 차트와 같은 (14, 1, 3) %K 의 마지막 바닥(≤20)부터 80 이상까지 걸린 봉 수. 15봉 안에, 되밀림 15 이하이면 `confirmed` | `k` · `smooth` · `d` · `floor` · `ceiling` · `bars_to_80` · `pullback_max` · `pullback_allowed` · `max_bars` |
| `overlap` | 가장 가까운 저항과 최근 120봉 매물대(두꺼운 노드 · POC)의 거리가 1.5% 안이면 겹침 | `distance_pct` · `close_distance_pct` · `tolerance_pct` · `falling` |
| `lead` | 코스피 대비 SK하이닉스 · 삼성전자의 돌파 시점(아래) | `benchmark` · `compared` |

- 근거를 계산할 입력이 없으면 `status: "insufficient"` 와 빈 `prices` 를 낸다.
- 캡처에서 읽은 작도 가격은 코드에 넣지 않는다. 캡처는 #675 의 사례로만 쓴다.

## 선행 돌파 `lead`

- 세 종목에 같은 규칙을 쓴다: 직전 스윙 고점 위 0.3 ATR 넘는 일봉 종가 마감, 최근 20봉.
- 마지막 확정봉의 거래일이 코스피와 같을 때만 비교한다.
- `state`:
  - `led`: 코스피보다 먼저 돌파했거나, 코스피는 아직 돌파 전
  - `same`: 코스피와 같은 날 돌파
  - `lagged`: 코스피보다 늦게 돌파
  - `waiting`: 아직 돌파 전
  - `not_comparable`: 일봉이 없거나 거래일이 어긋남
- 돌파 시점 비교일 뿐 매수 신호가 아니다.

## 상품 구분 `instrument`

| 종목 | kind · unit · session | 비고 |
| --- | --- | --- |
| `SOXX` · `SOXL` | `etf` · USD · `us_regular` | |
| `NQ=F` | `continuous_future` · `index_points` · `cme_globex` | `rollover: "yahoo_front_month"`. TradingView `NQ1!` 와 공급자 · 롤오버 시점이 다르다는 `note` |
| `^KS11` | `cash_index` · `index_points` · `krx_regular` | |
| `000660.KS` · `005930.KS` | `stock` · KRW · `krx_regular` | |
| `SOXLUSDT(.P)` · `NAS100` · `NQ1!` | `unavailable` · `product_mismatch`, `instrument: null` | 다른 상품의 봉으로 대신하지 않음 |

- `provider` 는 일봉 원천(`yahoo` · `toss`)이다.
- 그 밖에 근거가 없을 때의 `reason`:
  - `missing_daily_source`: 일봉 없음
  - `insufficient_bars`: 확정봉 60개 미만
  - `trend_follow_error`: 계산 실패

## 과거 결과 (2026-11-25 실행)

재현 기준:
- 날마다 그날까지의 확정 일봉(최대 300봉)만으로 읽었다.
- 스윙 선만 썼다. 1d frame 추세선은 전체 이력으로 그려지므로 뺐다.
- 그날 새로 `breakout_confirmed` 또는 `first_rebound` 가 된 날을 표본으로 삼았다.
- 결과는 그 뒤 10봉으로 쟀다.
  - 재이탈: 10봉 안에 돌파한 선 − 0.3 ATR 아래 종가가 나온 경우
  - 수익: 10봉 뒤 종가의 수익률
- 원천: 야후 일봉 1,056개(종목당 987일 판정).

| 종목 | 기간 | 돌파 확인 | 10봉 안 재이탈 | 10봉 뒤 상승 | 수익 중앙값 | 첫 양봉 n (상승) | 지지 회복 n (상승) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| SOXX | 2022-09-09 ~ 2026-11-23 | 116 | 52 (45%) | 64 | +1.54% | 6 (3) | 6 (3) |
| SOXL | 2022-09-09 ~ 2026-11-23 | 109 | 58 (53%) | 61 | +4.01% | 4 (2) | 3 (3) |
| NQ=F | 2022-09-16 ~ 2026-11-25 | 127 | 57 (45%) | 81 | +1.05% | 5 (3) | 2 (1) |
| ^KS11 | 2022-07-28 ~ 2026-11-25 | 107 | 47 (44%) | 67 | +1.00% | 3 (2) | 3 (2) |
| 000660.KS | 2022-07-28 ~ 2026-11-25 | 131 | 62 (47%) | 76 | +2.09% | 7 (3) | 2 (1) |
| 005930.KS | 2022-07-28 ~ 2026-11-25 | 121 | 60 (50%) | 71 | +0.69% | 3 (2) | 1 (0) |

실패 사례(돌파 다음 10봉 수익이 가장 나빴던 날):

| 종목 | 돌파일 | 돌파한 선 | 재이탈일 | 10봉 뒤 수익 |
| --- | --- | --- | --- | --- |
| SOXX | 2024-07-22 | 81.21 | 2024-07-24 | −17.5% |
| SOXL | 2024-07-22 | 49.99 | 2024-07-24 | −47.31% |
| NQ=F | 2025-03-24 | 19,959 | 2025-03-28 | −13.8% |
| 코스피 | 2026-09-23 | 6,996 | 2026-09-28 | −14.77% |
| SK하이닉스 | 2026-07-15 | 1,995,000 | 2026-07-16 | −36.5% |
| 삼성전자 | 2026-07-03 | 299,500 | 2026-07-08 | −21.16% |

읽는 법:
- 스윙 고점 하나를 종가로 넘은 돌파는 절반 가까이 10봉 안에 다시 선 아래로 마감했다. 그래서 화면은 `hold_after_break`(다음 봉도 선 위)와 `close_below_hold` 를 함께 보여 준다.
- 첫 반등 표본은 종목당 1~7개라 비율을 말할 수 없다.
- 마지막 거래일(국장 · NQ=F 2026-11-25)은 아직 진행 중인 봉일 수 있다. 그 봉은 10봉 결과 계산에만 들어간다.

재현:

```bash
cd backend
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m desk.trend_follow_replay \
  "SOXX,SOXL,NQ=F,^KS11,000660.KS,005930.KS" --years 3 --out trend_follow_replay.json
```

야후 이력이 바뀌면 숫자도 조금 바뀐다. 규칙을 바꾸면 이 표를 다시 만든다.
