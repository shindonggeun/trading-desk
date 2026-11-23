// #908 chart-read 응답 형식을 따르는 화면 검증용 합성 자료.
export const ticker = "NQ=F";
export const at = "2026-11-23T04:00:00+00:00";

const reason = { reason: null, reasonCode: null };
const point = (value, state, distancePct) => ({
  value, state, distancePct, method: "연구 산식", anchorWeeks: [], ...reason,
});
const check = (state, windowWeeks, decisionWeek) => ({
  state, windowWeeks, decisionWeek, anchorWeek: "2026-10-26", source: "yahoo",
  threshold: 25360, invalidationLevel: 24200,
  condition: "후속 확정 주봉 종가로 확인", invalidation: "관찰 주 저가 기준 이탈", ...reason,
});
const root = { observedAt: at, usage: "research_only" };
const base = {
  ...root,
  status: "available",
  assumptions: {
    series: "NQ=F", referenceSeries: "NQ1!",
    rollover: "unknown", originalSettings: "unknown", originalAnchors: "unknown",
  },
  week: {
    start: "2026-11-16", source: "yahoo", candle: "closed",
    closedAt: "2026-11-20T22:00:00+00:00", fetchedAt: at,
  },
  current: {
    price: 25000, source: "yahoo", asOf: "2026-11-23T03:55:00+00:00",
    fetchedAt: at, timeframe: "5m", kind: "chart_bar_close", ...reason,
  },
  evidence: {
    bollinger: point(24600, "near", -1.6),
    rsi: point(29, "oversold", null),
    channel: point(24700, "near", -1.2),
    fib: { ...point(24500, "near", -2), anchorWeeks: ["2026-04-27", "2026-06-01", "2026-07-06"] },
  },
  overlap: {
    state: "overlap", low: 24500, high: 24700,
    basis: ["bollinger", "rsi", "channel", "fib"], ...reason,
  },
  rebound: check("confirmed", 2, "2026-11-09"),
  recovery: check("watching", 12, null),
};

const missingPoint = {
  ...point(null, "missing", null),
  reason: "비교할 근거 값이 없습니다.",
  reasonCode: "insufficient_history",
};

export const cases = {
  rebound: base,
  oversold: {
    ...base,
    evidence: {
      ...base.evidence,
      bollinger: point(24600, "normal", -1.6),
      channel: point(24100, "normal", -3.6),
      fib: point(23900, "normal", -4.4),
    },
    overlap: { state: "none", low: null, high: null, basis: [], ...reason },
    rebound: check("watching", 2, null),
  },
  broken: {
    ...base,
    rebound: check("invalidated", 2, "2026-11-02"),
    evidence: { ...base.evidence, channel: point(24700, "below", -1.2) },
  },
  recovered: {
    ...base,
    rebound: check("not_confirmed", 2, "2026-11-09"),
    recovery: { ...check("confirmed", 12, "2026-11-16"), threshold: 25850 },
  },
  missing: {
    ...base,
    current: {
      ...base.current, price: null, timeframe: null, asOf: null, source: null, fetchedAt: null,
      reasonCode: "chart_price_unavailable", reason: "차트 봉 종가 참조를 받지 못했습니다.",
    },
    evidence: { bollinger: missingPoint, rsi: missingPoint, channel: missingPoint, fib: missingPoint },
    overlap: {
      state: "missing", low: null, high: null, basis: [],
      reasonCode: "insufficient_overlap_evidence", reason: "겹침을 판단할 근거가 부족합니다.",
    },
    rebound: {
      ...check("missing", 2, null),
      anchorWeek: null, condition: null, invalidation: null, threshold: null, invalidationLevel: null,
      reasonCode: "no_observation_week", reason: "관찰 후보 주를 찾지 못했습니다.",
    },
  },
  unavailable: {
    ...root, status: "unavailable",
    reasonCode: "unconfirmed_weekly_close", reason: "원천의 주봉 종결 확인을 기다립니다.",
  },
  legacy: undefined,
};
