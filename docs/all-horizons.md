# 분석 한 번에 네 기간 (#696)

분석은 여전히 **모델을 한 번** 부른다. 기간은 스캘핑 · 단타 · 짧은 스윙(2~3일) · 스윙이다.

- 요청한 기간이 run 의 계획(진입 · 손절 · 목표 · 트리거)이다.
- 같은 결정에서 PM 이 **네 기간 줄**을 고정 영어 라벨로 함께 쓴다(`desk/plan.py` 의 `split_horizon_lines` 가 읽는다).
- 그 옆에서 모델 없이 네 기간의 결정적 조각(지도 · 지지 · 저항 · 차트 읽기)을 만든다(`desk/all_horizons.py`).
- 투자 성향(공격형 · 균형형)은 고르지 않는다. 위험의 성격은 종목 분류(레버리지 · 인버스 · 채권 · 경기방어 · 일반)가 말한다.

단계:

- 1단계(PR1): 결정적 지도 · 종목 분류 · 장세 적합도
- 2단계(PR2): 모델의 네 기간 줄 · 성향 제거 · 기간별 채점

## run 결과에 더해지는 필드

```jsonc
"plan": {
  // … 지금까지의 필드(entry_price · stop_loss · price_target · horizon · trade_map …)는 그대로
  "horizon_lines": {             // PM 이 쓴 네 기간 줄(2단계). 모델이 안 쓰면 키가 없다
    "scalp": {"rating": "Buy", "bias": "long" /* long · short · flat · null */, "entry_price": 183.1,
              "stop_loss": 182.2, "price_target": 184.9, "reward_risk": 2.0, "why": "5분봉 지지 반등",
              "ticker": "NVDA",      // 가격이 걸린 종목: 레버리지 run 은 기초자산(에이전트가 분석한 것)
              "valid_until": "…",    // 그 기간의 진입 창(desk.horizons.valid_until). 지난 날짜 · 대본이면 null
              "entry_vs_live": {"kind": "stop", "gap_pct": 5.0, "stop_passed": false, …},   // 지금가 대비(계획과 같은 desk.levels.entry_vs_live). 시세 없으면 키 없음
              "levels_rejected": {"price_target": 103.0}   /* 서로 맞지 않아 버린 값이 있을 때만 */},
    "intraday": { … }, "short_swing": { … }, "swing": { … }
  },
  "instrument_class": {
    "id": "leverage",            // leverage · inverse · bond · defensive · general
    "label": "레버리지", "risk": "공격", "basis": "QQQ 의 하루 3배", "multiple": 3.0 /* 실수 */, "underlying": "QQQ"
  },
  "horizons": {                  // 오늘 분석에만. 지난 날짜 · 시세 없음이면 null (horizons_note 가 이유)
    "scalp": {
      "horizon": "scalp", "label": "스캘핑", "holding": "…", "flat_by_close": true,
      "source": "model",          // "model": 모델이 말한 기간(계획 또는 줄) / "map": 모델 의견 없음
      "model": {                  // source=map 이면 null. 키는 늘 같다(all_horizons.MODEL_KEYS)
        "origin": "line",          // "plan": run 의 기간(계획 그대로) / "line": PM 의 기간 줄
        "ticker": "NVDA",          // 가격이 걸린 종목(레버리지 run 의 줄은 기초자산)
        "opinion": null, "rating": "Buy", "bias": "long", "entry_price": 183.1, "entry_type": null,
        "stop_loss": 182.2, "price_target": 184.9, "reward_risk": 2.0,
        "long_trigger": null, "short_trigger": null, "valid_until": "…", "why": "5분봉 지지 반등"
      },                          // origin=plan 이면 opinion · entry_type · 트리거도 채워지고 why 는 그 기간 줄의 이유
      "underlying_line": null,    // 레버리지 run 의 줄(기초자산 가격, model 과 같은 키). 상품 칸(map · levels · chart)과
                                  //   가격이 달라 model 에 넣지 않는다 — 그 칸은 source "map". 화면은 "기초자산 QQQ 기준"으로 따로 보인다
      "map": {                    // desk.entry_now 의 고른 쪽(그 기간의 라이브 플레이북)
        "side": "long", "side_ko": "롱", "setup": "…", "kind": "limit", "kind_ko": "지정가",
        "zone": [lo, hi], "first": 101.2, "avg_entry": 100.9, "stop": 99.5, "stop_pct": -1.4,
        "targets": [{"role": "t1", "price": 103.0, "rr": 1.8}],
        "from_now_pct": -0.4, "expected_r": null, "ev_r": null, "p_t1": null, "n": 0, "thin": true,
        "why": ["…"], "stand_aside": null /* 또는 이유 문자열 */, "regime": "…", "price_now": 101.6, "atr": 0.8
      },                          // 고른 쪽이 없으면 side 등은 없고 stand_aside 에 이유
      "levels": {"support": [{"price", "strength", "basis"} ×≤2], "resistance": [… ×≤2]},
      "atr": 0.8,
      "chart": {"timeframe": "5m", "summary": "…",
                "trend": {"state": "up" /* up · down · sideways */, "ema20": 114.4, "ema50": 114.0, "basis": "…"},
                "above": [{"price": 125.5, "name": "최근 고점", "distance_pct": 4.1,
                           "refs": [{"kind": "swing", …}]   // refs 항목은 kind 마다 필드가 다르다(swing · fib …)
                          } /* ×≤2 */],
                "below": [ /* above 와 같은 꼴 ×≤2 */ ],
                "patterns": [kind…]},
      "errors": null              // {"levels"|"entry"|"chart": "TimeoutError"} — 그 조각만 빠짐
    },
    "intraday": { … }, "short_swing": { … }, "swing": { … }
  },
  "horizon_fit": {               // horizons 와 같이 null 일 수 있다
    "regime": "range",           // desk.regime_live(#694): trend_up · trend_down · range · turning · unknown
    "regime_ko": "박스권",
    "best": ["scalp", "intraday"],
    "fit": {"scalp": "good", "intraday": "good", "short_swing": "ok", "swing": "poor"},   // unknown 이면 전부 "unknown"
    "sentence": "지금은 박스권(폭 7.8%, 상단 4번 · 하단 3번 닿음) — 상 · 하단을 오가는 장이라 스캘핑 · 단타 쪽이 맞아요. …",
    "basis": ["…"], "summary": "1시간봉 … · 일봉 …",
    "frames": {"1d": {"regime", "adx", "box", "as_of"}, "60m": {…}},
    "atr_pct": 3.1,              // 스윙(일봉) ATR ÷ 지금가 %
    "record": null,              // 기간별 과거 R(#693 측정이 생기면)
    "errors": null
  },
  "horizons_note": null,         // horizons 가 null 인 이유
  "horizons_fake": true          // DESK_FAKE_RUNS 대본 값일 때만(화면 개발용)
}
```

- `horizons` 의 키 순서는 믿지 않는다(PostgreSQL JSONB 는 순서를 바꾼다). 화면은 scalp → intraday → short_swing → swing 순서로 그린다.
- 옛 run 에는 이 필드들이 없다. 화면은 없으면 지금처럼 기간 하나만 보인다. 1단계 run 의 `model` 에는 `origin` · `ticker` · `reward_risk` · `why` 가 없다.
- 만들다 실패하면 `instrument_class` 는 있고 `horizons` · `horizon_fit` 은 `null`, 이유는 `horizons_note` 에 있다. 이때도 `horizon_lines` 는 남는다.
- **목록(`GET /api/runs`)의 plan 에는 `horizons` · `horizon_fit` 이 없다**(run 하나에 약 10 KB). `GET /api/runs/{id}` 에만 있다.
- 대본 값(`horizons_fake`)은 실제 값과 같은 중첩 모양이다(테스트로 고정). 대본 결정도 네 기간 줄을 쓴다.

### 채점 (`outcome`, `GET /api/stats/plans`)

- run 의 기간이 아닌 **방향 있는 줄**(bias long · short)을 그 기간의 봉으로 따로 채점한다. run 자신의 계획과 같은 규칙(`outcome.judge`)이다.
  - 결과는 `outcome.horizons = {기간: {status, r, interval, ticker, …}}` 에 있다.
  - Hold 계획의 run 도 줄이 있으면 채점한다.
  - 줄의 진입 방식(now · limit · stop)과 이미 지난 손절 · 목표는 runner 가 지금가로 판정해 둔 `entry_vs_live` 를 따른다(돌파 진입을 지정가로 채우지 않는다).
  - 끝난 줄은 다음 sweep 에서 다시 받지 않는다. 기다리는 줄이 있으면 run 이 sweep 에 남는다.
  - 그동안 이미 끝난 run 자신의 결과 · 플레이북 결과는 다시 채점하지 않고 그대로 둔다(스캘핑 1분봉은 이틀이면 계획 시점까지 닿지 않는다). 기록 기한이 지난 뒤에도 줄은 채점된다.
- 통계에 `by_horizon_line`(기간별 줄의 target · stop · hit_rate …)이 더해졌다. `by_horizon` 은 지금처럼 run 자신의 계획만 센다.
- 줄에는 승률(win probability)이 없어 calibration 버킷에는 들어가지 않는다.
- 플레이북 보정(`desk.calibration`)과 백테스트는 이미 기간별 칸이다(`horizon|side|setup|regime|band`). 모델 줄은 과거 모델 출력이 없어 백테스트하지 않는다.

## API 변화 (2단계)

- `POST /api/runs` 의 `profile` 은 받아도 **무시**한다(옛 화면 호환, 40자까지). run `options` 에도 남지 않는다.
- `GET /api/config` 의 `profiles` 는 `[]`, `default_profile` 은 `null` 이다(옛 화면이 깨지지 않게 키만 남김). 성향 선택 UI 는 FE #697 이 걷는다.
- `DESK_INVESTOR_PROFILE` 은 더 이상 읽지 않는다(compose · `.env.example` 에서 뺐다).

## 규칙

- **모델의 네 기간 줄**(지시문 `all_horizons.mandate`, PM 이 레벨 줄과 Entry 줄 다음에 쓴다)

  ```text
  Scalp Plan: <Buy|Overweight|Hold|Underweight|Sell> / Entry: <number> / Stop: <number> / Target: <number> / Why: <one short clause>
  Intraday Plan: …
  Short Swing Plan: …
  Swing Plan: …
  ```

  - PM 만 쓴다(트레이더 · 리스크 토론은 쓰지 않는다고 지시). 라벨과 등급 단어는 출력 언어와 상관없이 영어다.
    한국어 등급 단어(매수 · 비중확대 · 보유 · 관망 · 비중축소 · 매도)도 읽는다.
  - 번역 단계(#567)도 라벨을 지키고, `why` 는 문장으로 보고 비교에서 뺀다.
  - 레버리지 분석가의 글에 줄이 있어도 버린다(줄은 PM 의 기초자산 줄이다).
  - 줄은 결정 글에서 먼저 떼어 낸다. 그래서 줄의 Entry · Stop · Target 이 run 자신의 레벨로 읽히지 않는다.
  - 진입 · 손절 · 목표가 서로 맞지 않으면 run 계획과 같은 규칙(`drop_contradiction`)으로 하나를 버린다.
  - run 자신의 기간 줄은 계획 레벨을 되풀이한다. 화면 · 채점은 계획을 쓰고, 그 줄의 `why` 만 붙인다.
  - 레버리지 run 은 에이전트가 기초자산을 분석하므로 줄 가격도 기초자산이다(`ticker`).
- **기간별 참고 지도를 지시문에**: 오늘 분석이고 레버리지가 아니면, 네 기간의 지지 · 저항 · ATR · 플레이북 고른 쪽 · 추세 · 장세를 몇 줄로 지시문에 넣는다(`all_horizons.prompt_note`).
  - 다른 메모와 같은 대기 예산(`NOTES_WAIT_SEC`) 안에서만 기다린다. 늦으면 그 메모 없이 시작한다. 그래서 분석 시작이 늦어지지 않는다.
  - run 에 붙는 `horizons` 는 분석이 끝난 뒤 다시 만든다(그때의 값, 캐시 덕에 대개 0.1초 안).
- **종목 분류 지시**: 레버리지 · 인버스 · 채권 · 경기방어면 그 성격을 한 줄로 지시문에 넣는다(`all_horizons.class_note`). 일반은 넣지 않는다.
- **기간마다 결정적 지도**
  - `map`: `desk.entry_now`(그 기간의 라이브 플레이북 · 과거 체결률 · 기대 R)가 고른 쪽이다.
  - `levels`: 그 기간 봉의 `desk.sr` 가까운 지지 · 저항이다. 요청한 기간은 분석에 쓴 `sr` 를 그대로 쓴다.
  - `chart`: `desk.chart_read` 한 시간봉이다. 스캘핑 5분 · 단타 15분 · 짧은 스윙 1시간 · 스윙 일봉.
  - 조각마다 20초 제한이 있다. 실패한 조각은 그 기간의 `errors` 에만 남는다.
- **종목 분류**
  - 레버리지 · 인버스: `desk.related.LEVERAGED` 의 배수를 본다. 음수면 인버스다.
  - 채권 · 국채 ETF, 경기방어(필수소비 · 유틸리티 · 헬스케어 · 통신): `desk/all_horizons.py` 의 목록을 본다.
  - 나머지는 일반이다.
- **기간 적합도**
  - 장세는 `regime_live.view`(일봉이 정하고 1시간봉이 보탠다)로 판정한다.
  - 박스권이면 스캘핑 · 단타, 추세면 짧은 스윙 · 스윙, 전환 중이면 단타 · 짧은 스윙이다.
  - 판단 보류면 기간을 고르지 않는다.
  - 레버리지 · 인버스(변동성 끌림), 채권 · 방어(느린 움직임), 일봉 ATR 5% 이상(넓은 손절)이면 한 줄씩 덧붙인다.
- **시간**: 모델 없이 계산하는 조각들이라 마무리 요약(모델 한 번)과 나란히 돈다.
  - 로컬 측정(Yahoo, 네 기간): 처음 1~7초, 캐시가 데워진 뒤 0.05초 안팎이다.
  - Yahoo 가 처음 느리면 조각마다 20초 제한에 걸리고, 그 조각만 빠진다.
- **DESK_FAKE_RUNS**: 대본 실행은 오프라인이라 같은 모양의 예시 값을 붙이고 `horizons_fake: true` 로 표시한다. 대본 결정에도 네 기간 줄이 있다.
