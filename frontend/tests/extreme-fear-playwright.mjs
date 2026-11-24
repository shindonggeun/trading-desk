// Run against an isolated Vite server with a local Playwright installation:
// node tests/extreme-fear-playwright.mjs http://127.0.0.1:5173 /path/to/playwright/index.mjs
import assert from "node:assert/strict";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const [base, playwrightPath] = process.argv.slice(2);
assert.ok(base && playwrightPath, "supply a Vite origin and Playwright index.mjs");
const { chromium } = await import(pathToFileURL(resolve(playwrightPath)));
const day = "2026-11-23";
const asOf = "2026-11-23T21:16:00Z";
const condition = (source, operator, threshold, status, value = null) => ({
  source, operator, threshold, status, value, available_at: value === null ? null : asOf,
  reason: value === null ? "point_in_time_input_unavailable" : null,
});
const metrics = {
  n: 2, wins: 1, losses: 1, flat: 0, win_rate: 0.5, win_rate_ci95: [0.095, 0.905],
  median_return_pct: 0, median_loss_pct: -8.21, loss_quartiles_pct: [-8.21, -8.21, -8.21],
  worst_return_pct: -8.21, worst_mae_pct: -14.27, not_entered: 0, immature: 0, missing: 0, overlap_skipped: 0,
};
const waiting = {
  ...metrics, n: 0, wins: 0, losses: 0, win_rate: null, win_rate_ci95: null, median_return_pct: null,
  median_loss_pct: null, loss_quartiles_pct: null, worst_return_pct: null, worst_mae_pct: null, immature: 2,
};
const strategy = {
  signal_count: 2, prelisting_sessions: 0, unverified_sessions: 0, episodes_suppressed: 1,
  horizons: { 5: metrics, 20: metrics, 60: waiting, 252: waiting },
  ex_post_252: { n: 0, median_max_return_pct: null },
};
const outcome = { status: "complete", exit_date: "2026-11-18", return_pct: -8.21, mae_pct: -14.27 };
const incomplete = { status: "immature", exit_date: null, return_pct: null, mae_pct: null };
const unavailable = {
  symbol: "SOXL", status: "unverified", as_of: asOf, dataset: null, signal_history: [], strategies: {},
  reason: "approved_point_in_time_report_unavailable",
  observations: {
    cnn: { value: 8, as_of: asOf, source: "CNN Fear & Greed", status: "unverified",
      reason: "observation_without_confirmed_session_close" },
    vix: { value: 32, as_of: "2026-11-23T05:00:00Z", source: "Yahoo ^VIX", status: "unverified",
      reason: "observation_without_confirmed_session_close" },
  },
  current: { date: day, matched_count: 0, status: "unverified", conditions: {
    rsi: condition("Wilder RSI(14)", "<=", 30, "unverified"),
    cnn: condition("CNN Fear & Greed", "<=", 10, "missing"),
    vix: condition("Cboe VIX", ">=", 30, "delayed"),
  } },
};
const available = {
  ...unavailable, symbol: "KORU", status: "available", reason: undefined, cost_bps: 10, observations: {},
  dataset: {
    kind: "approved", sha256: "0".repeat(64), price_adjustment: "causal_forward_total_return",
    start: "2026-01-02", end: day,
    sources: Object.fromEntries(["calendar", "prices", "cnn", "vix"].map((name) => [name, { name, version: "fixture" }])),
  },
  current: { date: day, matched_count: 3, status: "matched", conditions: {
    rsi: condition("Wilder RSI(14)", "<=", 30, "matched", 30),
    cnn: condition("CNN Fear & Greed", "<=", 10, "matched", 10),
    vix: condition("Cboe VIX", ">=", 30, "matched", 31.2),
  } },
  signal_history: [{
    signal_date: "2026-11-10", available_at: "2026-11-10T21:16:00Z", entry_date: "2026-11-12",
    outcomes: { 5: outcome, 20: incomplete, 60: incomplete, 252: incomplete },
    ex_post_252: { status: "immature", max_return_pct: null },
  }],
  strategies: { triple: strategy, rsi_only: strategy, buy_hold: strategy, soxl_to_koru: strategy },
};
const candles = {
  ticker: "KORU", interval: "1d", source: "synthetic", fetched_at: asOf, bars:
    [10, 12, 13, 16, 17, 18, 19, 20, 23].map((date, index) => ({
      at: `2026-11-${date}T14:30:00Z`, open: 30 + index, high: 32 + index,
      low: 29 + index, close: 31 + index, volume: 3_000 + index,
    })),
};

const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL });
try {
  for (const width of [390, 1440]) {
    const page = await browser.newPage({ viewport: { width, height: 900 }, reducedMotion: "reduce" });
    const requests = [];
    const chartRequests = [];
    let failKoru = false;
    let edge = false;
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === "/api/research/extreme-fear") {
        const symbol = url.searchParams.get("symbol");
        requests.push(symbol);
        if (symbol === "KORU" && failKoru) {
          failKoru = false;
          return route.fulfill({ status: 503, json: { detail: "synthetic unavailable" } });
        }
        let response = symbol === "KORU" ? available : unavailable;
        if (symbol === "KORU" && edge) {
          response = { ...available, current: { ...available.current, status: "not_matched", matched_count: 2,
            conditions: { ...available.current.conditions,
              cnn: condition("CNN Fear & Greed", "<=", 10, "not_matched", 10.001) } } };
        }
        return route.fulfill({ json: response });
      }
      if (url.pathname === "/api/auth/me") return route.fulfill({ json: { username: "fixture", otp_enabled: false } });
      if (url.pathname === "/api/backtest/reports") return route.fulfill({ json: [] });
      if (url.pathname === "/api/runs") return route.fulfill({ json: { items: [] } });
      if (url.pathname === "/api/candles") {
        chartRequests.push([url.searchParams.get("ticker"), url.searchParams.get("interval")]);
        return route.fulfill({ json: candles });
      }
      return route.fulfill({ status: 503, json: { detail: "synthetic unavailable" } });
    });
    await page.goto(`${base}/#/backtest`);
    await page.getByRole("heading", { name: "극단 공포 조건과 백테스트" }).waitFor();
    const region = page.getByRole("region", { name: "극단 공포 조건과 백테스트" });
    await region.getByText("CNN Fear & Greed", { exact: false }).last().waitFor();
    let content = await region.innerText();
    assert.match(content, /미검증/);
    assert.match(content, /결측/);
    assert.match(content, /지연/);
    assert.match(content, /관측/);
    assert.match(content, /일봉 기준일 2026-11-23 · 공표 시각 미확인/);
    assert.doesNotMatch(content, /50%/);
    failKoru = true;
    await region.getByRole("button", { name: "KORU", exact: true }).focus();
    await page.keyboard.press("Enter");
    await region.getByRole("button", { name: "다시 시도" }).click();
    await region.getByText(/사후 최고/).first().waitFor();
    content = await region.innerText();
    assert.match(content, /3개 중 3개 충족/);
    assert.match(content, /8\.21%/);
    assert.match(content, /14\.27%/);
    assert.match(content, /RSI 단독/);
    assert.match(content, /단순 보유/);
    assert.match(content, /표본/);
    await region.getByRole("button", { name: "252거래일", exact: true }).click();
    content = await region.innerText();
    assert.match(content, /보유기간 미경과/);
    assert.match(content, /완결 표본 부족/);
    assert.equal(requests[0], "SOXL");
    assert.deepEqual(requests.filter((symbol) => symbol === "KORU"), ["KORU", "KORU"]);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    edge = true;
    await region.getByRole("button", { name: "SOXL", exact: true }).click();
    await region.getByText("확정 판정 없음").waitFor();
    await region.getByRole("button", { name: "KORU", exact: true }).click();
    await region.getByText("10.001점", { exact: true }).waitFor();
    await region.getByText("3개 중 2개 충족", { exact: true }).waitFor();
    const signal = region.locator('a[href="#/chart/KORU?interval=1d&signalDate=2026-11-10"]');
    await signal.click();
    await page.getByText("백테스트 신호일", { exact: false }).waitFor();
    await page.locator(".price-chart-box canvas").first().waitFor({ state: "visible" });
    assert.ok(chartRequests.some(([symbol, interval]) => symbol === "KORU" && interval === "1d"));
    assert.match(page.url(), /#\/chart\/KORU\?interval=1d&signalDate=2026-11-10$/);
    await page.close();
    console.log(`${width}px · synthetic unavailable/deferred/loss, retry, chart link and layout passed`);
  }
} finally {
  await browser.close();
}
