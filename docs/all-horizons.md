# 분석 한 번에 네 기간 (#696)

분석은 여전히 **모델을 한 번** 부른다(요청한 기간 하나). 그 옆에서 모델 없이 계산하는 조각을 네 기간 모두 만들어 run 결과 `plan` 에 붙인다.
기간은 스캘핑 · 단타 · 짧은 스윙(2~3일) · 스윙이다. 코드는 `desk/all_horizons.py` 이고, `desk/runner.py` 가 마무리 요약과 나란히 부른다.

- 1단계(이 문서, PR1): 결정적 지도 · 종목 분류 · 장세 적합도
- 2단계(PR2): 모델이 네 기간 줄을 함께 쓰고(`plan.parse_plan`) 성향 선택을 없애며, 채점을 기간별로 한다

## run 결과에 더해지는 필드

```jsonc
"plan": {
  // … 지금까지의 필드(entry_price · stop_loss · price_target · horizon · trade_map …)는 그대로
  "instrument_class": {
    "id": "leverage",            // leverage · inverse · bond · defensive · general
    "label": "레버리지", "risk": "공격", "basis": "QQQ 의 하루 3배", "multiple": 3.0 /* 실수 */, "underlying": "QQQ"
  },
  "horizons": {                  // 오늘 분석에만. 지난 날짜 · 시세 없음이면 null (horizons_note 가 이유)
    "scalp": {
      "horizon": "scalp", "label": "스캘핑", "holding": "…", "flat_by_close": true,
      "source": "map",            // "model": 모델이 말한 기간 / "map": 모델 의견 없음(결정적 지도)
      "model": null,              // source=model 이면 {opinion, rating, bias, entry_price, entry_type,
                                  //   stop_loss, price_target, long_trigger, short_trigger, valid_until}
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

- 옛 run 에는 이 필드들이 없다. 화면은 없으면 지금처럼 기간 하나만 보인다.
- 만들다 실패하면 `instrument_class` 는 있고 `horizons` · `horizon_fit` 은 `null`, 이유는 `horizons_note` 에 있다.
- **목록(`GET /api/runs`)의 plan 에는 `horizons` · `horizon_fit` 이 없다**(run 하나에 약 10 KB). `GET /api/runs/{id}` 에만 있다.
- 대본 값(`horizons_fake`)은 실제 값과 같은 중첩 모양이다(테스트로 고정).

## 규칙

- **기간마다 결정적 지도**
  - `map`: `desk.entry_now`(그 기간의 라이브 플레이북 · 과거 체결률 · 기대 R)가 고른 쪽이다.
  - `levels`: 그 기간 봉의 `desk.sr` 가까운 지지 · 저항이다. 요청한 기간은 분석에 쓴 `sr` 를 그대로 쓴다.
  - `chart`: `desk.chart_read` 한 시간봉이다. 스캘핑 5분 · 단타 15분 · 짧은 스윙 1시간 · 스윙 일봉.
  - 조각마다 20초 제한이 있다. 실패한 조각은 그 기간의 `errors` 에만 남는다.
- **종목 분류**
  - 레버리지 · 인버스: `desk.related.LEVERAGED` 의 배수를 본다. 음수면 인버스다.
  - 채권 · 국채 ETF, 경기방어(필수소비 · 유틸리티 · 헬스케어 · 통신): `desk/all_horizons.py` 의 목록을 본다.
  - 나머지는 일반이다.
  - 성향(공격형 · 균형형)을 고르는 대신 이 분류를 설명에 쓴다. 성향 제거는 2단계다.
- **기간 적합도**
  - 장세는 `regime_live.view`(일봉이 정하고 1시간봉이 보탠다)로 판정한다.
  - 박스권이면 스캘핑 · 단타, 추세면 짧은 스윙 · 스윙, 전환 중이면 단타 · 짧은 스윙이다.
  - 판단 보류면 기간을 고르지 않는다.
  - 레버리지 · 인버스(변동성 끌림), 채권 · 방어(느린 움직임), 일봉 ATR 5% 이상(넓은 손절)이면 한 줄씩 덧붙인다.
- **시간**: 모델 없이 계산하는 조각들이라 마무리 요약(모델 한 번)과 나란히 돈다.
  - 로컬 측정(Yahoo, 네 기간): 처음 1~7초, 캐시가 데워진 뒤 0.05초 안팎이다.
  - Yahoo 가 처음 느리면 조각마다 20초 제한에 걸리고, 그 조각만 빠진다.
- **DESK_FAKE_RUNS**: 대본 실행은 오프라인이라 같은 모양의 예시 값을 붙이고 `horizons_fake: true` 로 표시한다.
