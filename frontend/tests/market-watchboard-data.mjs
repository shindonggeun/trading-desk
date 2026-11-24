const observed = "2026-11-24T04:05:00Z";
const closes = { kr: "06:30", us: "21:00", jp: "06:30", tw: "05:30", cme: "22:00" };

function row(trade_date, close, market) {
  const closed_at = market === "crypto"
    ? new Date(Date.parse(`${trade_date}T00:00:00Z`) + 86_400_000).toISOString()
    : `${trade_date}T${closes[market]}:00Z`;
  return { trade_date, at: `${trade_date}T00:00:00Z`, closed_at, close };
}

function instrument(ticker, name, kind, market, timezone, currency, fields = {}) {
  const closes = market === "crypto" ? [100, 109, 111] : [100, 110, 108];
  const history = ["2026-11-19", "2026-11-20", "2026-11-23"].map((day, index) => row(day, closes[index], market));
  return {
    ticker, name, kind, market, timezone, currency, unit: kind === "index" || kind === "future" ? "points" : "currency",
    relation: kind === "index" || kind === "future" ? "benchmark" : "related",
    chart_href: `/#/charts?t=${encodeURIComponent(ticker)}&iv=1d`,
    market_state: "closed", status: "available", reason: null, source: "yahoo", fetched_at: observed,
    source_delay_min: 20, history, latest: history.at(-1), change_pct: -1.82,
    ...fields,
  };
}

const unavailable = {
  status: "missing", reason: "no_completed_bar", history: [], latest: null, change_pct: null,
};
const points = (days, values) => days.map((trade_date, index) => ({ trade_date, return_pct: values[index] }));
const response = (group, items, comparison) => ({ group, days: 30, as_of: observed, items, comparison });
const baseDate = "2026-11-19";
const days = [baseDate, "2026-11-20", "2026-11-23"];
const kospiHistory = days.map((day, index) => row(day, [100, 110, 110][index], "kr"));
const japanHistory = [row(days[0], 100, "jp"), row(days[2], 97.55, "jp")];
const etherHistory = days.map((day, index) => row(day, [100, 80, 88][index], "crypto"));

export const watchboardFixtures = {
  semiconductors: response("semiconductors", [
    instrument("^KS11", "코스피", "index", "kr", "Asia/Seoul", "KRW", {
      market_state: "open", history: kospiHistory, latest: kospiHistory.at(-1), change_pct: 0,
    }),
    instrument("NQ=F", "나스닥100 연속 선물", "future", "cme", "America/New_York", "USD", { market_state: "open" }),
    instrument("^SOX", "필라델피아 반도체지수", "index", "us", "America/New_York", "USD"),
    instrument("^N225", "닛케이225", "index", "jp", "Asia/Tokyo", "JPY", {
      market_state: "holiday", status: "delayed", reason: "latest_session_missing",
      history: japanHistory, latest: japanHistory.at(-1), change_pct: null,
    }),
    instrument("^TWII", "대만 가권지수", "index", "tw", "Asia/Taipei", "TWD", { market_state: "open", ...unavailable }),
    instrument("285A.T", "키옥시아", "stock", "jp", "Asia/Tokyo", "JPY", { market_state: "holiday" }),
    instrument("2330.TW", "TSMC 대만 본주", "stock", "tw", "Asia/Taipei", "TWD", { market_state: "open" }),
    instrument("TSM", "TSMC 미국 ADR", "adr", "us", "America/New_York", "USD", {
      relation: "reference", chart_href: null, ...unavailable,
    }),
  ], {
    status: "partial", base_date: baseDate, excluded: ["^TWII"],
    series: [{ ticker: "^KS11", points: points(days, [0, 10, 10]) },
      { ticker: "NQ=F", points: points(days, [0, 10, 8]) },
      { ticker: "^SOX", points: points(days, [0, 10, 8]) },
      { ticker: "^N225", points: points([days[0], days[2]], [0, -2.45]) }],
  }),
  crypto: response("crypto", [
    instrument("BTC-USD", "비트코인", "crypto", "crypto", "UTC", "USD", {
      market_state: "open", source_delay_min: 0, change_pct: null,
    }),
    instrument("ETH-USD", "이더리움", "crypto", "crypto", "UTC", "USD", {
      market_state: "open", source_delay_min: 0, history: etherHistory, latest: etherHistory.at(-1), change_pct: null,
    }),
  ], {
    status: "available", base_date: baseDate, excluded: [],
    series: [{ ticker: "BTC-USD", points: points(days, [0, 9, 11]) },
      { ticker: "ETH-USD", points: points(days, [0, -20, -12]) }],
  }),
  gold_miners: response("gold_miners", [
    instrument("GDX", "금광주 ETF", "etf", "us", "America/New_York", "USD"),
    instrument("NEM", "뉴몬트", "stock", "us", "America/New_York", "USD",
      { chart_href: "https://example.invalid/#/charts?t=NEM&iv=1d", ...unavailable }),
  ], { status: "unavailable", base_date: null, excluded: ["GDX", "NEM"], series: [] }),
};

const breakAsOf = "2026-11-24T03:05:00Z";
export const watchboardJapanBreak = {
  ...watchboardFixtures.semiconductors, as_of: breakAsOf,
  items: watchboardFixtures.semiconductors.items.map((item) => ({
    ...item, fetched_at: item.fetched_at === null ? null : breakAsOf,
    market_state: item.market === "jp" ? "break" : item.market_state,
  })),
};
