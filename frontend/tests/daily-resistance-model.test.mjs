import assert from "node:assert/strict";
import test from "node:test";
import {
  dailyResistanceFocus, dailyResistancePresentation, dailyResistanceProduct, findDailyResistanceCandle,
  formatDailyDistance, formatDailyPrice, formatDailyRatio, isDailyResistanceExample,
} from "../src/dailyResistance.ts";

const at = "2026-11-24T05:00:00Z";
const confirmed = {
  ticker: "NQ=F",
  timeframe: "1d",
  status: "confirmed",
  reason: null,
  instrument: { kind: "continuous_future", unit: "index_points" },
  observed_at: "2026-11-24T22:31:00Z",
  fetched_at: "2026-11-24T22:30:00Z",
  source: "yahoo",
  session: {
    market: "cme", trade_date: "2026-11-24", opens_at: "2026-11-23T23:00:00Z",
    closes_at: "2026-11-24T22:00:00Z", confirm_after: "2026-11-24T22:10:00Z",
  },
  bar: { at, open: 215, high: 223, low: 211, close: 216 },
  previous_close: { at: "2026-11-23T05:00:00Z", price: 214 },
  resistance: {
    kind: "channel_upper", price: 220, basis_at: "2026-11-23T05:00:00Z", projected_at: "2026-11-24T00:00:00-05:00",
    ref: { kind: "channel", edge: "upper", id: "1d-channel-fixture" },
  },
  metrics: { range: 12, body_ratio: 1 / 12, upper_wick_ratio: 7 / 12, close_position: 5 / 12,
    body_atr: 0.09, close_distance_pct: -1.82 },
  reaction: {
    kind: "wick_rejection", near: true, touched: true, wick_rejection: true, strong_bullish_close: false, close_breakout: false,
  },
  volume: null,
};

test("확정 일봉에서만 대표 반응과 확정 종가를 표시한다", () => {
  const presentation = dailyResistancePresentation(confirmed);
  assert.equal(presentation.state, "확정 일봉");
  assert.equal(presentation.closeLabel, "확정 종가");
  assert.equal(presentation.reaction, "윗꼬리 밀림");
  for (const [kind, label] of [
    ["close_breakout", "종가 돌파"],
    ["strong_bullish_close", "강한 양봉 마감"],
    ["resistance_touch", "저항 접촉"],
    ["resistance_near", "저항 근접"],
    ["none", "뚜렷한 저항 반응 없음"],
  ]) {
    assert.equal(dailyResistancePresentation({ ...confirmed, reaction: { ...confirmed.reaction, kind } }).reaction, label);
  }
  for (const [reason, state] of [["session_open", "장중 진행"], ["awaiting_confirmation", "마감 후 확인 대기"]]) {
    const live = dailyResistancePresentation({ ...confirmed, status: "in_progress", reason });
    assert.equal(live.state, state);
    assert.equal(live.closeLabel, "진행 봉 종가");
    assert.equal(live.reaction, null);
    assert.match(live.next, /확인 가능 시각/);
  }
});

test("결측과 미지원 상품은 판정과 가격 단위를 채우지 않는다", () => {
  const unavailable = { ...confirmed, status: "unavailable", reason: "previous_bar_missing", reaction: null };
  const view = dailyResistancePresentation(unavailable);
  assert.equal(view.state, "판정 자료 부족");
  assert.equal(view.closeLabel, "관측 봉 종가");
  assert.match(view.detail, /직전 거래일/);
  assert.match(view.next, /확정 일봉/);

  const perpetual = { ...unavailable, ticker: "SOXLUSDT.P", reason: "product_unsupported", instrument: null };
  const cfd = { ...perpetual, ticker: "NAS100.CFD" };
  assert.equal(dailyResistancePresentation(perpetual).state, "지원하지 않는 상품");
  assert.deepEqual(dailyResistanceProduct(perpetual), { name: "SOXL 연동 USDT 무기한 계약", unit: null });
  assert.deepEqual(dailyResistanceProduct(cfd), { name: "NAS100 참고 CFD", unit: null });
  assert.deepEqual(dailyResistanceProduct(confirmed), { name: "나스닥100 연속 선물", unit: "포인트" });
  assert.deepEqual(dailyResistanceProduct({ ticker: "SOXL", instrument: { kind: "etf", unit: "USD" } }),
    { name: "반도체 레버리지 ETF", unit: "USD" });
  assert.equal(isDailyResistanceExample(" nas100.cfd "), true);
  assert.equal(isDailyResistanceExample("NVDA"), false);
  assert.equal(dailyResistanceFocus(perpetual), null);
});

test("몸통·윗꼬리 비율만 백분율로 변환하고 저항 거리는 부호와 영을 보존한다", () => {
  assert.equal(formatDailyRatio(0.4), "40%");
  assert.equal(formatDailyRatio(0), "0%");
  assert.equal(formatDailyRatio(null), "미제공");
  assert.equal(formatDailyRatio(1.1), "미제공");
  assert.equal(formatDailyDistance(1.82), "+1.82%");
  assert.equal(formatDailyDistance(-1.82), "-1.82%");
  assert.equal(formatDailyDistance(-0.001), "-0.001%");
  assert.equal(formatDailyDistance(0), "0%");
  assert.equal(formatDailyDistance(undefined), "미제공");
  assert.equal(formatDailyPrice(1234.5, "USD"), "1,234.5 USD");
  assert.equal(formatDailyPrice(null, "USD"), "미제공");
  assert.equal(formatDailyPrice(1234.5, null), "미제공");
});

test("관찰 봉과 같은 절대 시각에 투영된 유효 저항만 강조한다", () => {
  assert.deepEqual(dailyResistanceFocus(confirmed), {
    barAt: at, high: 223, low: 211, resistance: { price: 220, projectedAt: confirmed.resistance.projected_at },
  });
  const mismatch = { ...confirmed.resistance, projected_at: "2026-11-24T00:00:00Z" };
  assert.equal(dailyResistanceFocus({ ...confirmed, resistance: mismatch }).resistance, null);
  assert.equal(dailyResistanceFocus({ ...confirmed, bar: { ...confirmed.bar, at: "잘못된 시각" } }), null);
  assert.equal(dailyResistanceFocus({ ...confirmed, bar: { ...confirmed.bar, high: 200 } }), null);
});

test("차트에 같은 절대 시각의 봉이 있을 때만 해당 봉을 찾는다", () => {
  const sameDay = { at: "2026-11-24T04:00:00Z" };
  const exact = { at: "2026-11-24T00:00:00-05:00" };
  const invalid = { at: "잘못된 시각" };
  const anchorOf = row => row.at;
  assert.equal(findDailyResistanceCandle([sameDay, exact, invalid], at, anchorOf), exact);
  assert.equal(findDailyResistanceCandle([sameDay, invalid], at, anchorOf), undefined);
  assert.equal(findDailyResistanceCandle([invalid, exact], invalid.at, anchorOf), undefined);
});
