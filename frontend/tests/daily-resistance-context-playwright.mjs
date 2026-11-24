// Isolated Vite: node tests/daily-resistance-context-playwright.mjs <origin> <playwright/index.mjs>
import assert from "node:assert/strict";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const [origin, driver] = process.argv.slice(2);
assert.ok(origin && driver, "supply a Vite origin and Playwright index.mjs");
const { chromium } = await import(pathToFileURL(resolve(driver)));
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL });
const fixtureAt = "2026-11-23T05:00:00Z";
const discoveredAt = "2026-11-23T19:45:00Z";
const observedAt = "2026-11-23T21:15:00Z";
const newsReadAt = "2026-11-23T21:25:00Z";

function chartRead(ticker) {
  const result = { ticker, generated_at: observedAt, timeframes: [], conflicts: [], summary: "합성 차트 요약", note: "" };
  if (ticker === "^NDX") return result;
  const perpetual = ticker === "SOXLUSDT.P";
  result.daily_reaction = {
    ticker, timeframe: "1d", status: perpetual ? "unavailable" : "confirmed", reason: perpetual ? "product_unsupported" : null,
    instrument: perpetual ? null : ticker === "NQ=F" ? { kind: "continuous_future", unit: "index_points" } : { kind: "etf", unit: "USD" },
    observed_at: observedAt, fetched_at: observedAt, source: "yahoo", session: null,
    bar: perpetual ? null : { at: fixtureAt, open: 125, high: 133, low: 123, close: 126 },
    previous_close: null, resistance: null, metrics: null, volume: null,
    reaction: perpetual ? null : { kind: "wick_rejection", near: true, touched: true, wick_rejection: true },
  };
  return result;
}

const news = (title, fields = {}) => ({
  title, kind: "news", source: "gdelt", publisher: "합성 매체", at: discoveredAt, time_basis: "gdelt_seen",
  relevance: "direct", stale: false, url: "https://example.test/news", ...fields,
});
const newsResponse = (ticker, items) => ({
  ticker, generated_at: newsReadAt, news: { status: "available" }, items,
});

try {
  for (const width of [390, 1440]) {
    const page = await browser.newPage({ viewport: { width, height: 844 } });
    const errors = [];
    const requested = [];
    let release;
    const waitForInitial = new Promise((resolveGate) => { release = resolveGate; });
    let nqAttempts = 0;
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url());
      const ticker = url.searchParams.get("ticker");
      if (url.pathname === "/api/chart-read") return route.fulfill({ json: chartRead(ticker) });
      if (url.pathname === "/api/candles") {
        const bars = Array.from({ length: 6 }, (_, i) => ({
          at: new Date(Date.UTC(2026, 10, 18 + i, 5)).toISOString(), open: 125,
          high: 133, low: 123, close: 126, volume: 1000,
        }));
        return route.fulfill({ json: { ticker, interval: url.searchParams.get("interval"), source: "yahoo", bars } });
      }
      if (url.pathname === "/api/stock-social") {
        requested.push(ticker);
        if (ticker === "NQ=F" && ++nqAttempts === 1) return route.fulfill({ status: 503, json: {} });
        if (ticker === "NQ=F" && nqAttempts === 2) {
          return route.fulfill({ json: { ...newsResponse(ticker, []), sources: [{ id: "gdelt", status: "unavailable" }] } });
        }
        if (ticker === "NQ=F") return route.fulfill({ json: newsResponse(ticker, []) });
        if (ticker === "SOXL") return route.fulfill({ json: newsResponse("SOXX", [news("SOXX 응답 재사용 금지")]) });
        await waitForInitial;
        return route.fulfill({ json: newsResponse(ticker, [
          news("SOXX 운용사 합성 발표"),
          news("NVDA 부품 업계 합성 기사", { relevance: "indirect" }),
          news("가상 사후 기사", { at: "2026-11-23T21:16:00Z" }),
          news("SOXX 공시 보기", { kind: "filing", time_basis: "edgar_acceptance" }),
        ]) });
      }
      return route.fulfill({ status: 503, json: { detail: "synthetic unavailable" } });
    });

    await page.goto(origin + "/tests/daily-resistance-context.html");
    const price = page.getByRole("region", { name: "일봉 마감과 저항 반응" });
    await price.getByText("윗꼬리 밀림", { exact: true }).waitFor();
    assert.deepEqual(requested, []);
    const disclosure = page.getByText("뉴스·사용자 메모", { exact: true });
    await disclosure.focus();
    await page.keyboard.press("Enter");
    await page.getByText("뉴스를 불러오는 중…", { exact: true }).waitFor();
    assert.ok(await price.getByText("126 USD", { exact: true }).isVisible());
    release();
    const article = page.getByText("SOXX 운용사 합성 발표", { exact: true }).first();
    await article.waitFor();
    assert.equal(await price.getByText("SOXX 운용사 합성 발표", { exact: true }).count(), 0);
    assert.ok(await page.getByText(/합성 매체 · GDELT 발견/).isVisible());
    assert.ok(await page.locator(`time[datetime="${discoveredAt}"]`).isVisible());
    assert.equal(await page.getByText(/일봉 관찰 기준/).locator("time").getAttribute("datetime"), observedAt);
    assert.equal(await page.getByText(/뉴스 조회 시각/).locator("time").getAttribute("datetime"), newsReadAt);
    await page.getByText("이 상품에 연결된 사용자 메모가 제공되지 않았어요.", { exact: true }).waitFor();
    assert.equal(await page.getByText(/NVDA 부품|가상 사후 기사|SOXX 공시 보기/).count(), 0);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    await disclosure.click();
    await article.waitFor({ state: "hidden" });
    await disclosure.click();
    await article.waitFor();

    const switcher = page.getByRole("navigation", { name: "검증 종목" });
    await switcher.getByRole("button", { name: "SOXL", exact: true }).click();
    await article.waitFor({ state: "hidden" });
    await disclosure.click();
    const noNews = page.getByText(/같은 상품의 뉴스를 찾지 못했어요/);
    await noNews.waitFor();
    assert.equal(await page.getByText("SOXX 응답 재사용 금지").count(), 0);

    await switcher.getByRole("button", { name: "NQ=F", exact: true }).click();
    await disclosure.click();
    const retryNews = page.getByRole("button", { name: "뉴스 다시 조회" });
    await retryNews.click();
    await page.getByText("뉴스 원천 자료를 확인할 수 없어요.", { exact: false }).waitFor();
    await retryNews.click();
    await noNews.waitFor();
    assert.deepEqual(requested, ["SOXX", "SOXX", "SOXL", "NQ=F", "NQ=F", "NQ=F"]);

    await switcher.getByRole("button", { name: "SOXLUSDT.P", exact: true }).click();
    await price.getByText("지원하지 않는 상품", { exact: true }).waitFor();
    await disclosure.click();
    await page.getByText("이 상품에 연결된 사용자 메모가 제공되지 않았어요.", { exact: true }).waitFor();
    assert.deepEqual(requested, ["SOXX", "SOXX", "SOXL", "NQ=F", "NQ=F", "NQ=F"]);

    await switcher.getByRole("button", { name: "^NDX", exact: true }).click();
    await page.getByText(/일봉 저항 반응 자료를 받지 못했어요/).waitFor();
    await disclosure.waitFor({ state: "hidden" });
    assert.equal(await page.getByText("일봉 마감과 저항 반응", { exact: true }).count(), 0);
    assert.deepEqual(errors, []);
    console.log(`${width}px daily resistance context regression passed`);
    await page.close();
  }
} finally {
  await browser.close();
}
