import assert from "node:assert/strict";
import { test } from "node:test";
import {
  byStamp, conditionState, evidenceMeta, evidencePriceLabels, evidencePractice, formatTrendDistance, formatTrendPrice, trendFocus, trendPresentation, trendProduct, trendSummaryLine,
} from "./trendFollow.ts";

// #977 계약을 따른 합성 응답 — 가격은 화면 처리 확인용이지 실제 시세가 아니다.
const base = (over = {}) => ({
  ticker: "SOXX",
  timeframe: "1d",
  status: "confirmed",
  reason: null,
  instrument: { kind: "etf", unit: "USD", symbol: "SOXX", provider: "yahoo", session: "us_regular", rollover: null, note: null },
  observed_at: "2026-11-25T01:00:00Z",
  as_of: "2026-11-24T21:00:00Z",
  last_bar: { at: "2026-11-24T21:00:00Z", open: 200, high: 210, low: 198, close: 208 },
  trend: { state: "down", basis: "하락 채널 안" },
  stage: { state: "first_rebound", signal: "first_up_bar", confirmed_at: "2026-11-24T21:00:00Z", basis: "직전 5봉 하락 뒤 첫 양봉" },
  key_prices: {
    hold: { price: 198, label: "첫 반등 저점", distance_pct: -4.8 },
    break: { price: 215, label: "하락 채널 상단", distance_pct: 3.4, ref: { kind: "channel", edge: "upper", id: "c1" } },
  },
  conditions: [
    { id: "c-close", kind: "confirm", text: "215 위 일봉 종가 마감", price: 215, met: false },
    { id: "c-hold", kind: "invalidate", text: "198 아래 일봉 종가 마감", price: 198, met: null },
  ],
  evidence: [
    {
      id: "q", kind: "quarter_level", status: "confirmed", label: "수평 구간 25% 되돌림", detail: "",
      prices: [220, 200], anchors: [{ at: "2026-11-10T21:00:00Z", price: 230 }, { at: "2026-11-20T21:00:00Z", price: 190 }],
      confirmed_at: "2026-11-20T21:00:00Z", meta: { ratio: 0.25, high: 230, low: 190 },
    },
    {
      id: "fib", kind: "fib_extension", status: "pending", label: "기준 파동 확장", detail: "",
      prices: [246.6, 254.7, 287.7], anchors: [{ at: "2026-11-20T21:00:00Z", price: 190 }, { at: "2026-11-24T21:00:00Z", price: 210 }],
      confirmed_at: null, meta: { ratios: "1.414,1.618,2.618", scale: "linear" },
    },
    {
      id: "st", kind: "stochastic", status: "insufficient", label: "바닥권 → 80", detail: "", prices: [], anchors: [],
      confirmed_at: null, meta: { k: 14, smooth: 1, d: 3, floor: 20, ceiling: 80, pullback_max: 6, pullback_allowed: 15, max_bars: 15 },
    },
  ],
  lead: null,
  ...over,
});

test("첫 반등은 추세와 진행 상태를 한 줄로, 신호는 따로 말한다", () => {
  const p = trendPresentation(base());
  assert.equal(p.line, "일봉 하락 추세 · 첫 반등");
  assert.match(p.signal, /첫 양봉만/);
  assert.equal(p.pending, false);
  assert.equal(trendSummaryLine(base()),
    "관찰: 일봉 하락 추세 · 첫 반등 · 지켜야 할 가격 198 USD · 넘어야 할 저항 215 USD · 다음 확인 215 위 일봉 종가 마감");
  // 핵심 가격이 없고 확인 조건이 모두 충족되면 그 부분만 뺀다
  const done = base({ key_prices: { hold: null, break: null }, conditions: [{ id: "c", kind: "confirm", text: "x", price: null, met: true }] });
  assert.equal(trendSummaryLine(done), "관찰: 일봉 하락 추세 · 첫 반등");
});

test("확인 대기 · 꼬리 접촉과 지지 회복을 같은 상태 안에서 구분한다", () => {
  const wick = trendPresentation(base({ stage: { state: "awaiting_confirmation", signal: "wick_touch", confirmed_at: null, basis: "" } }));
  assert.equal(wick.line, "일봉 하락 추세 · 확인 대기");
  assert.match(wick.signal, /꼬리만 닿았/);
  const reclaim = trendPresentation(base({ stage: { state: "awaiting_confirmation", signal: "support_reclaim", confirmed_at: null, basis: "" } }));
  assert.match(reclaim.signal, /다시 올라섰/);
});

test("확정 돌파와 재이탈", () => {
  const up = trendPresentation(base({ trend: { state: "up", basis: "" }, stage: { state: "breakout_confirmed", signal: "close_above", confirmed_at: "2026-11-24T21:00:00Z", basis: "" } }));
  assert.equal(up.line, "일봉 상승 추세 · 돌파 확인");
  assert.match(up.signal, /저항 위에서 마감/);
  const back = trendPresentation(base({ stage: { state: "failed_breakout", signal: "close_back_below", confirmed_at: null, basis: "" } }));
  assert.equal(back.line, "일봉 하락 추세 · 재이탈");
  assert.match(back.signal, /다시 내려왔/);
});

test("돌파 기준(선 + 0.3 ATR) 안에서 마감해 신호가 없으면 판정 근거를 보인다", () => {
  const p = trendPresentation(base({ stage: { state: "awaiting_confirmation", signal: null, confirmed_at: null, basis: "선 위 0.3 ATR 안에서 마감 — 돌파 확인 전" } }));
  assert.equal(p.line, "일봉 하락 추세 · 확인 대기");
  assert.match(p.signal, /돌파 확인 전/);
});

test("세션을 모르는 시장 · 호가 통화 단위", () => {
  assert.match(trendPresentation(base({ status: "unavailable", reason: "unsupported_market", instrument: null, trend: null, stage: null })).reason, /세션을 모르는 시장/);
  assert.equal(trendProduct({ ticker: "BTC-KRW", instrument: { kind: "spot", unit: "KRW", symbol: "BTC-KRW", provider: "yahoo", session: "24h", rollover: null, note: null } }).unit, "원");
  assert.equal(trendProduct({ ticker: "ETH-BTC", instrument: { kind: "spot", unit: "BTC", symbol: "ETH-BTC", provider: "yahoo", session: "24h", rollover: null, note: null } }).unit, "BTC");
});

test("진행 중 봉이면 판정은 앞 확정봉 기준임을 표시한다", () => {
  assert.equal(trendPresentation(base({ status: "in_progress" })).pending, true);
});

test("자료 부족과 상품 불일치는 판정 · 강조 없이 이유만", () => {
  const thin = base({ status: "unavailable", reason: "insufficient_bars", trend: null, stage: null });
  assert.equal(trendPresentation(thin).line, "일봉 추세 판정 없음");
  assert.match(trendPresentation(thin).reason, /일봉이 모자라/);
  assert.equal(trendSummaryLine(thin), "관찰: 일봉 추세 판정 없음");
  assert.equal(trendFocus(thin, "hold"), null);
  const mismatch = base({ status: "unavailable", reason: "product_mismatch" });
  assert.match(trendPresentation(mismatch).reason, /상품이 달라/);
  assert.equal(trendFocus(mismatch, "q"), null);
});

test("ETF · 무기한 계약 · 연속 선물은 라벨과 단위를 따로", () => {
  assert.deepEqual(
    [trendProduct(base()).label, trendProduct(base()).unit],
    ["SOXX · 반도체 ETF", "USD"],
  );
  const perp = trendProduct({ ticker: "SOXLUSDT", instrument: { kind: "perpetual", unit: "USDT", symbol: "SOXLUSDT", provider: "binance", rollover: null, note: null } });
  assert.equal(perp.label, "SOXLUSDT · SOXL 연동 USDT 무기한 계약");
  assert.equal(perp.unit, "USDT");
  assert.equal(perp.source, "Binance");
  const nq = trendProduct({ ticker: "NQ=F", instrument: { kind: "continuous_future", unit: "index_points", symbol: "NQ=F", provider: "yahoo", session: "cme_globex", rollover: "yahoo_front_month", note: "TradingView NQ1! 과 롤오버가 달라요" } });
  assert.equal(nq.unit, "포인트");
  assert.equal(nq.source, "Yahoo Finance · CME Globex · 롤오버 야후 근월물 연결");
  assert.equal(trendProduct({ ticker: "X", instrument: { kind: "spot", unit: "USD", symbol: "X", provider: null, session: "24h", rollover: null, note: null } }).source, "24시간");
  assert.match(nq.note, /NQ1!/);
  const kospi = trendProduct({ ticker: "^KS11", instrument: { kind: "cash_index", unit: "index_points", symbol: "^KS11", provider: "toss", session: "krx_regular", rollover: null, note: null } });
  assert.equal(kospi.label, "^KS11 · 코스피 · 현물 지수");
  assert.equal(kospi.source, "토스증권 · 한국 정규장");
  const spot = trendProduct({ ticker: "BTC-USD", instrument: { kind: "spot", unit: "USD", symbol: "BTC-USD", provider: "yahoo", session: "24h", rollover: null, note: null } });
  assert.equal(spot.label, "BTC-USD · 24시간 시세");
  assert.equal(spot.source, "Yahoo Finance · 24시간");
  // 상품 불일치는 instrument 가 없다 — 다른 상품 라벨을 지어내지 않는다
  assert.deepEqual(trendProduct({ ticker: "SOXLUSDT.P", instrument: null }), { label: "SOXLUSDT.P · SOXL 연동 USDT 무기한 계약", unit: null, source: null, note: null });
});

test("조건은 충족 · 아직 · 확인 전을 구분한다", () => {
  assert.equal(conditionState({ kind: "confirm", met: true }).label, "충족");
  assert.equal(conditionState({ kind: "invalidate", met: true }).label, "발생");
  assert.equal(conditionState({ kind: "confirm", met: false }).tone, "open");
  assert.equal(conditionState({ kind: "confirm", met: null }).label, "확인 전");
});

test("근거의 설정값을 화면에 남긴다", () => {
  const [q, fib, st] = base().evidence;
  assert.match(evidenceMeta(q), /비율 0\.25 · 구간 190~230/);
  assert.match(evidenceMeta(fib), /확장 1\.414 · 1\.618 · 2\.618 · 선형/);
  assert.equal(evidenceMeta(st), "K 14 · 평활 1 · D 3 · 바닥 20 → 80 · 15봉 안에 도달해야 확인 · 되밀림 6 (허용 15)");
  assert.match(evidenceMeta({ ...st, meta: { ...st.meta, bars_to_80: 7 } }), /7봉 만에 도달/);
  assert.equal(evidenceMeta({ kind: "overlap", meta: { distance_pct: 0.8, tolerance_pct: 1.5, falling: "yes" } }), "저항과 매물대 거리 +0.8% · 허용 1.5% · 하락 저항");
  assert.equal(evidenceMeta({ kind: "overlap", meta: { distance_pct: 0.8, falling: "no" } }), "저항과 매물대 거리 +0.8%");
  assert.equal(formatTrendDistance(0.01), "0.0%");
  assert.equal(formatTrendPrice(5006.0298, "포인트"), "5,006.03 포인트");
  assert.equal(formatTrendPrice(0.123456, "USDT"), "0.1235 USDT");
  assert.equal(formatTrendDistance(3.44), "+3.4%");
});

test("강조 대상 — 확정봉 · 핵심 가격 · 근거 가격과 앵커", () => {
  const o = base();
  const stage = trendFocus(o, "stage");
  assert.equal(stage.barAt, "2026-11-24T21:00:00Z");
  assert.deepEqual(stage.prices.map((p) => p.price), [198, 215]);
  assert.deepEqual(trendFocus(o, "break").prices, [{ price: 215, label: "넘을" }]);
  const q = trendFocus(o, "q");
  assert.deepEqual(q.prices, [{ price: 220, label: "위 25% 선" }, { price: 200, label: "아래 25% 선" }]);
  assert.deepEqual(q.anchors.map((a) => a.n), [1, 2]);
  assert.deepEqual(trendFocus(o, "fib").prices.map((p) => p.label), ["확장 1.414", "확장 1.618", "확장 2.618"]);
  assert.equal(trendFocus(o, "st"), null);
  assert.equal(trendFocus(o, "missing"), null);
});

test("근거 가격마다 #977 순서대로 이름을 붙인다", () => {
  assert.deepEqual(evidencePriceLabels({ kind: "channel_quarter", prices: [10, 9, 8, 7] }), ["채널 상단", "상단 25%", "하단 25%", "채널 하단"]);
  assert.deepEqual(evidencePriceLabels({ kind: "overlap", prices: [10, 10.1] }), ["저항", "매물 구역"]);
  assert.deepEqual(evidencePriceLabels({ kind: "overlap", status: "insufficient", prices: [10] }), ["저항"]);
  // 모르는 개수면 근거 이름으로 — 틀린 이름을 붙이지 않는다
  assert.deepEqual(evidencePriceLabels({ kind: "channel_quarter", prices: [10, 9] }), ["채널 폭 0.25 보조선", "채널 폭 0.25 보조선"]);
  assert.deepEqual(evidencePriceLabels({ kind: "fib_extension", prices: [1, 2], meta: { ratios: "1.414,1.618,2.618" } }), ["피보나치 확장", "피보나치 확장"]);
  // 일부 앵커만 유효해도 번호는 원래 순번을 지킨다
  const o = base();
  o.evidence[0].anchors[0] = { at: "bad", price: 230 };
  assert.deepEqual(trendFocus(o, "q").anchors.map((a) => a.n), [2]);
});

test("앵커가 차트의 같은 시각 봉에 모두 있을 때만 작도 연습으로 잇는다", () => {
  const candle = (stamp, time) => ({ time, stamp, open: 1, high: 1, low: 1, close: 1 });
  const candles = [candle("2026-11-10T21:00:00Z", "2026-11-10"), candle("2026-11-20T21:00:00Z", "2026-11-20"), candle("2026-11-24T21:00:00Z", "2026-11-24")];
  const find = byStamp(candles, (c) => c.stamp);
  const [q, fib, st] = base().evidence;
  const practice = evidencePractice(q, find);
  assert.equal(practice.kind, "measure");
  assert.deepEqual(practice.steps.map((s) => [s.anchor.time, s.anchor.price]), [["2026-11-10", 230], ["2026-11-20", 190]]);
  assert.match(practice.steps[0].instruction, /230 지점을 선택하세요/);
  assert.equal(evidencePractice(fib, find).kind, "measure");
  assert.equal(evidencePractice(st, find), null);
  // 채널 앵커가 둘이면 기준선(두 점 추세선) 연습, 다이버전스는 마지막 두 피벗
  const ch = { id: "ch", kind: "channel_quarter", status: "confirmed", label: "", detail: "", prices: [], confirmed_at: null,
    anchors: [{ at: "2026-11-10T21:00:00Z", price: 230 }, { at: "2026-11-24T21:00:00Z", price: 212 }] };
  assert.equal(evidencePractice(ch, find).kind, "trend");
  const dv = { ...ch, id: "dv", kind: "divergence", anchors: [{ at: "2026-11-10T21:00:00Z", price: 1 }, { at: "2026-11-20T21:00:00Z", price: 2 }, { at: "2026-11-24T21:00:00Z", price: 3 }] };
  assert.deepEqual(evidencePractice(dv, find).steps.map((s) => s.anchor.price), [2, 3]);
  // 자료 부족 근거는 앵커가 있어도 연습으로 잇지 않는다
  assert.equal(evidencePractice({ ...q, status: "insufficient" }, find), null);
  // 앵커 하나만 맞으면(부분 일치) 잇지 않는다
  assert.equal(evidencePractice(q, byStamp([candles[0]], (c) => c.stamp)), null);
  // 시간봉 차트처럼 같은 시각 봉이 없으면 날짜만 맞춰 잇지 않는다
  assert.equal(evidencePractice(q, byStamp([candle("2026-11-10T14:30:00Z", 1), candle("2026-11-20T14:30:00Z", 2)], (c) => c.stamp)), null);
});
