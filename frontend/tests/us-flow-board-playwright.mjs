// node tests/us-flow-board-playwright.mjs <vite-origin> <playwright/index.mjs>
import assert from "node:assert/strict";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { flowFixtures } from "./kr-flow-board-data.mjs";
import { fundingFixture } from "./funding-rate-fixture.mjs";

const [base, playwrightPath] = process.argv.slice(2);
assert.ok(base && playwrightPath, "supply a Vite origin and Playwright index.mjs");
const { chromium } = await import(pathToFileURL(resolve(playwrightPath)));
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL });

const front = "2026-12-18", next = "2026-12-24", removed = "2026-12-31";
const tradeDate = "2026-11-23", snapshot = "2026-11-23T21:00:00Z";
const emptyTotals = {
  calls: null, puts: null, put_call_ratio: null, status: "missing", as_of: null, delay_sec: null, trade_date: null,
};
const emptyTop = { calls: [], puts: [] };
function unavailable(ticker, status, reason, expirations = []) {
  return {
    ticker, status, reason, source: null, expiration: null, expirations, expirations_truncated: false, fetched_at: snapshot,
    spot: { as_of: null, price: null, delay_sec: null }, volume: emptyTotals, oi: emptyTotals, strikes: [],
    top: { volume: emptyTop, oi: emptyTop },
    range: { min: null, max: null, total_strikes: 0, matching_strikes: 0, limit: 80, truncated: false },
    excluded_contracts: 0,
  };
}
function options(ticker, expiration = front, calls = 26) {
  const strikes = [
    { strike: 100, call_volume: calls, put_volume: 0, call_oi: null, put_oi: 0 },
    { strike: 110, call_volume: 0, put_volume: 0, call_oi: null, put_oi: 12 },
  ];
  return {
    ...unavailable(ticker, "ok", null, [front, next, removed]), expiration, source: "합성 옵션 원천",
    spot: { price: 103, as_of: snapshot, delay_sec: 30 },
    volume: { calls, puts: 0, put_call_ratio: 0, status: "ok", as_of: snapshot, delay_sec: 900, trade_date: tradeDate },
    oi: { calls: null, puts: 12, put_call_ratio: null, status: "partial", as_of: null, delay_sec: null, trade_date: tradeDate },
    strikes, top: { volume: { calls: [{ strike: 100, count: calls }], puts: [] }, oi: { calls: [], puts: [{ strike: 110, count: 12 }] } },
    range: { min: 100, max: 110, total_strikes: 2, matching_strikes: 2, limit: 80, truncated: false },
  };
}

try {
  for (const width of [390, 1440]) {
    const page = await browser.newPage({ viewport: { width, height: 900 }, hasTouch: width === 390, isMobile: width === 390 });
    await page.addInitScript(() => {
      if (!localStorage.getItem("desk.marketFlow.v1")) localStorage.setItem("desk.marketFlow.v1", JSON.stringify({ market: "FUNDING" }));
    });
    let requests = 0;
    let funding = 0;
    let holdNext = null;
    let qqqFailed = false;
    await page.route("**/api/**", async (route) => {
      const { pathname, searchParams } = new URL(route.request().url());
      if (pathname === "/api/auth/me") return route.fulfill({ json: { username: "fixture", otp_enabled: false } });
      if (pathname === "/api/runs") return route.fulfill({ json: { items: [] } });
      if (pathname === "/api/market/flow-board/kr") return route.fulfill({ json: flowFixtures.kospi });
      if (pathname === "/api/positioning/funding") {
        funding++;
        return route.fulfill({ json: fundingFixture(searchParams.get("symbol"), Number(searchParams.get("days")) || 30) });
      }
      if (pathname === "/api/positioning/options") {
        requests++;
        const ticker = searchParams.get("ticker"), expiration = searchParams.get("expiration");
        if (ticker === "AMD") return route.fulfill({ json: unavailable(ticker, "unavailable", "no_provider") });
        if (ticker === "AAPL") return route.fulfill({ json: unavailable(ticker, "no_options", "not_listed") });
        if (ticker === "QQQ" && !qqqFailed) {
          qqqFailed = true;
          return route.fulfill({ json: unavailable(ticker, "unavailable", "catalog_unavailable") });
        }
        if (expiration === removed) {
          return route.fulfill({ json: unavailable(ticker, "invalid_expiration", "expiration_not_listed", [front, next]) });
        }
        if (expiration === next && holdNext) {
          const held = holdNext;
          holdNext = null;
          held.seen();
          await held.ready;
          await route.fulfill({ json: options(ticker, expiration, 77) }).catch(() => {});
          held.done();
          return;
        }
        return route.fulfill({ json: options(ticker, expiration ?? front, expiration === next ? 77 : 26) });
      }
      return route.fulfill({ status: 503, json: { detail: "synthetic unavailable" } });
    });
    const dashboard = "#/?flow=short&shortTicker=005930.KS";
    await page.goto(`${base}/${dashboard}`);
    const flow = page.locator("#market-flow");
    const oldOption = flow.getByRole("link", { name: "미장 옵션", exact: true });
    await oldOption.waitFor();
    await flow.getByRole("button", { name: "펀딩비", pressed: true }).waitFor();
    await flow.locator(".funding-panel .funding-meta").waitFor();
    assert.ok(funding > 0);
    assert.equal(requests, 0);
    await oldOption.click();
    await page.waitForURL((url) => url.hash === "#/flow-board/us");
    const panel = page.getByRole("region", { name: "미장 옵션 만기별 분포" });
    const selector = panel.getByRole("combobox", { name: "종목" });
    const expiration = panel.getByRole("combobox", { name: "만기" });
    const summary = panel.locator(".us-opt-summary");
    const chart = panel.getByRole("img", { name: "행사가별 콜·풋 분포" });
    await page.getByRole("heading", { name: "미장 수급 현황판", exact: true }).waitFor();
    await chart.waitFor();
    assert.equal(requests, 1);
    assert.equal(await selector.inputValue(), "NVDA");
    assert.equal(await expiration.inputValue(), front);
    assert.match(await summary.innerText(), /풋\/콜 비율\s+0\.00/);
    assert.match(await panel.locator(".us-opt-timing").innerText(), /합성 옵션 원천[\s\S]*2026-11-23[\s\S]*15분/);
    await panel.getByRole("button", { name: "미결제약정 (OI)", exact: true }).click();
    assert.match(await summary.innerText(), /일부 수치 누락/);
    await chart.locator("g title").filter({ hasText: "콜 — · 풋 0" }).first().waitFor({ state: "attached" });
    await panel.getByRole("button", { name: "당일 거래량", exact: true }).click();
    await expiration.selectOption(next);
    await summary.getByText("77", { exact: true }).waitFor();
    await expiration.selectOption(removed);
    await panel.getByText("선택한 만기를 조회할 수 없습니다.", { exact: false }).waitFor();
    assert.equal(await summary.count(), 0);
    await expiration.selectOption(front);
    await summary.getByText("26", { exact: true }).waitFor();
    let saw, release, finished;
    const seen = new Promise((resolve) => { saw = resolve; });
    const ready = new Promise((resolve) => { release = resolve; });
    const done = new Promise((resolve) => { finished = resolve; });
    holdNext = { seen: saw, ready, done: finished };
    await expiration.selectOption(next);
    await seen;
    await selector.selectOption("AMD");
    await panel.getByText("데이터 원천이 구성되지 않았습니다.", { exact: true }).waitFor();
    release();
    await done;
    assert.equal(await summary.count(), 0);
    assert.equal(await selector.inputValue(), "AMD");
    await selector.selectOption("AAPL");
    await panel.getByText("이 종목에 상장된 옵션 만기가 없습니다.", { exact: true }).waitFor();
    await selector.selectOption("QQQ");
    await panel.getByText("데이터 원천에서 옵션을 불러오지 못했습니다.", { exact: true }).waitFor();
    await panel.getByRole("button", { name: "다시 시도", exact: true }).click();
    await summary.getByText("26", { exact: true }).waitFor();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));

    const regions = page.getByRole("navigation", { name: "수급 현황판 지역" });
    const beforeKorea = requests;
    await regions.getByRole("link", { name: "국장", exact: true }).click();
    await page.getByRole("heading", { name: "국장 수급 현황판", exact: true }).waitFor();
    assert.equal(await regions.getByRole("link", { name: "국장", exact: true }).getAttribute("aria-current"), "page");
    assert.equal(requests, beforeKorea);
    await regions.getByRole("link", { name: "미장", exact: true }).click();
    await panel.waitFor();
    assert.equal(await regions.getByRole("link", { name: "미장", exact: true }).getAttribute("aria-current"), "page");
    await chart.waitFor();
    await page.goto(`${base}/#/live`);
    await page.getByRole("navigation", { name: "시장 하위 메뉴" })
      .getByRole("link", { name: "수급 현황판", exact: true }).click();
    await page.getByRole("heading", { name: "국장 수급 현황판", exact: true }).waitFor();

    await page.evaluate(() => localStorage.setItem("desk.marketFlow.v1", JSON.stringify({ market: "US_OPTIONS" })));
    const beforeLegacy = requests;
    await page.goto(`${base}/${dashboard}`);
    await oldOption.waitFor();
    await flow.getByRole("button", { name: "코스피", pressed: true }).waitFor();
    assert.equal(await panel.count(), 0);
    assert.equal(requests, beforeLegacy);
    await page.goto(`${base}/#/flow-board/kr`);
    await page.getByRole("heading", { name: "국장 수급 현황판", exact: true }).waitFor();
    await page.goto(`${base}/#/?ticker=NVDA`);
    const analysisTicker = page.getByRole("form", { name: "새 분석" }).getByLabel("분석할 종목");
    await analysisTicker.waitFor();
    assert.equal(await analysisTicker.inputValue(), "NVDA");
    assert.equal(requests, beforeLegacy);
    await page.close();
    console.log(`${width}px · menu, one option surface, series switching, fallback states and legacy entry points passed`);
  }
} finally {
  await browser.close();
}
