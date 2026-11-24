// #912 API와 동일한 형식으로 프론트 옵션을 분리해 검증한다.
const UNDERLYINGS = {
  BTCUSDT: ["비트코인", "crypto", "BTC"],
  ETHUSDT: ["이더리움", "crypto", "ETH"],
  XRPUSDT: ["리플", "crypto", "XRP"],
  SOLUSDT: ["솔라나", "crypto", "SOL"],
  SAMSUNGUSDT: ["삼성전자", "stock", "005930"],
  SKHYNIXUSDT: ["SK하이닉스", "stock", "000660"],
  NVDAUSDT: ["엔비디아", "stock", "NVDA"],
  AMDUSDT: ["AMD", "stock", "AMD"],
  MUUSDT: ["마이크론", "stock", "MU"],
  SOXXUSDT: ["SOXX", "etf", "SOXX"],
};

const TODAY = "2026-11-24";
const NOW = Date.parse(TODAY);
const DAY = 86_400_000;
const date = (ago) => new Date(NOW - ago * DAY).toISOString().slice(0, 10);

export function fundingFixture(symbol, days) {
  const [name, kind, ticker] = UNDERLYINGS[symbol];
  const state = {
    SAMSUNGUSDT: ["stale", "rate_limited"],
    SKHYNIXUSDT: ["inactive", "not_trading"],
    NVDAUSDT: ["source_error", "source_unavailable"],
    AMDUSDT: ["unsupported", "unsupported_contract"],
    MUUSDT: ["no_data", "no_settlements"],
  }[symbol] ?? ["available", null];
  const [status, reason] = state;
  const hasHistory = status === "available" || status === "stale";
  const onlyTodayNegative = symbol === "XRPUSDT";
  const newListing = symbol === "SOLUSDT";
  const price = symbol === "ETHUSDT" ? 3600 : symbol === "BTCUSDT" ? 90000 : 200;
  const delayed = status === "stale";
  const currentDate = delayed ? date(1) : TODAY;
  const end = delayed ? 2 : 1;
  const daily = [];
  const settlements = [];

  for (let n = days - 1; n >= 0 && hasHistory; n--) {
    const day = date(n + end);
    const rate = newListing && n > 1 ? null
      : onlyTodayNegative ? 0
      : n === 0 ? -0.0006
      : n === 1 ? -0.0003
      : n === 2 ? 0
      : n === 3 ? null
      : 0.0001;
    const valid = rate !== null;
    daily.push({ date: day, total_rate: rate, settlement_count: valid ? 3 : 0,
      status: valid ? "complete" : "missing", average_7d: null });
    if (valid) {
      for (const hour of [0, 8, 16]) {
        const hourText = String(hour).padStart(2, "0");
        settlements.push({
          settled_at: day + "T" + hourText + ":00:00+00:00",
          rate: rate / 3,
          rate_type: "regular",
          mark_price: n === 2 ? null : price + days - n,
        });
      }
    }
  }
  for (let i = 6; i < daily.length; i++) {
    const window = daily.slice(i - 6, i + 1);
    if (window.every(row => row.status === "complete")) {
      daily[i].average_7d = window.reduce((sum, row) => sum + row.total_rate, 0) / 7;
    }
  }
  if (hasHistory) {
    settlements.push({ settled_at: currentDate + "T00:00:00+00:00", rate: -0.0001, mark_price: price,
      rate_type: newListing ? "special" : "regular" });
  }

  return {
    symbol, days, underlying: { name, kind, ticker },
    contract_status: status === "inactive" ? "SETTLING" : status === "unsupported" ? null : "TRADING",
    source: "binance",
    fetched_at: status === "source_error" ? null : currentDate + "T00:02:00+00:00",
    status, reason, funding_interval_hours: hasHistory ? 8 : null,
    latest_settlement: settlements.at(-1) ?? null, settlements, daily,
    current_day: { date: currentDate, total_rate: hasHistory ? -0.0001 : null,
      settlement_count: hasHistory ? 1 : 0, status: "partial" },
    negative_streak_days: hasHistory && !onlyTodayNegative ? 2 : 0,
    negative_streak_truncated: false,
  };
}
