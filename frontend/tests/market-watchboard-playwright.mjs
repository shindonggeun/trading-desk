// node tests/market-watchboard-playwright.mjs <vite-origin> <playwright/index.mjs> [--menu]
import assert from "node:assert/strict";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { watchboardFixtures, watchboardJapanBreak } from "./market-watchboard-data.mjs";

const [base, playwrightPath, mode] = process.argv.slice(2);
assert.ok(base && playwrightPath, "supply a Vite origin and Playwright index.mjs");
const { chromium } = await import(pathToFileURL(resolve(playwrightPath)));
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL });
const inMenu = mode === "--menu";

try {
  for (const width of [390, 1440]) {
    const mobile = width === 390;
    const page = await browser.newPage({ viewport: { width, height: 900 }, hasTouch: mobile, isMobile: mobile });
    let failCrypto = true;
    let japanBreak = false;
    const requests = [];
    if (inMenu) {
      await page.route("**/api/**", async (route) => {
        const url = new URL(route.request().url());
        if (url.pathname === "/api/auth/me") return route.fulfill({ json: { username: "fixture", otp_enabled: false } });
        if (url.pathname === "/api/runs") return route.fulfill({ json: { items: [] } });
        if (url.pathname === "/api/market/watchboard") {
          const group = url.searchParams.get("group");
          requests.push([group, url.searchParams.get("days")]);
          if (group === "crypto" && failCrypto) {
            failCrypto = false;
            return route.fulfill({ status: 503, json: { detail: "synthetic unavailable" } });
          }
          return route.fulfill({ json: group === "semiconductors" && japanBreak ? watchboardJapanBreak : watchboardFixtures[group] });
        }
        if (url.pathname === "/api/candles") {
          const ticker = url.searchParams.get("ticker"), interval = url.searchParams.get("interval");
          return route.fulfill({ json: {
            ticker, interval, source: "synthetic", fetched_at: watchboardFixtures.semiconductors.as_of,
            bars: [19, 20, 23].map((day, index) => ({
              at: `2026-11-${day}T01:00:00Z`, open: 100 + index, high: 102 + index,
              low: 99 + index, close: 101 + index, volume: 1000,
            })),
          } });
        }
        return route.fulfill({ status: 503, json: { detail: "synthetic unavailable" } });
      });
      await page.goto(`${base}/#/`);
      await page.getByRole("navigation", { name: "메뉴", exact: true }).getByRole("link", { name: "시장", exact: true }).click();
      await page.getByRole("navigation", { name: "시장 하위 메뉴" }).getByRole("link", { name: "시장 모니터", exact: true }).click();
      await page.waitForURL((url) => url.hash === "#/market-watchboard");
    } else {
      await page.goto(`${base}/tests/market-watchboard.html?failCrypto`);
    }
    const board = page.locator(".market-watchboard");
    await board.getByRole("heading", { name: "시장 모니터", exact: true }).waitFor();
    await board.getByText("TSMC 대만 본주", { exact: true }).waitFor();
    let content = await board.innerText();
    for (const pattern of [/코스피/, /키옥시아/, /TSMC 미국 ADR/, /휴장/, /지연/, /결측|미확인/, /Yahoo/, /TWD/, /2026-11-19/, /0(?:\.0+)?%/]) {
      assert.match(content, pattern);
    }
    const card = (name) => board.locator(".watchboard-instrument").filter({ has: page.getByRole("heading", { name, exact: true }) });
    assert.match(await card("코스피").locator(".watchboard-change strong").innerText(), /^[+-]?0(?:\.0+)?%$/);
    assert.match(await card("키옥시아").innerText(), /15:30/);
    assert.match(await card("TSMC 대만 본주").innerText(), /13:30/);
    assert.match(await card("TSMC 미국 ADR").innerText(), /차트 연결 없음/);
    assert.match(content, /대만 가권지수|\^TWII/);
    const chartLink = board.locator('a[href*="t=2330.TW"][href*="iv=1d"]');
    assert.equal(await chartLink.count(), 1);
    await board.getByRole("button", { name: "코인", exact: true }).click();
    await board.getByRole("button", { name: "다시 시도", exact: true }).click();
    await board.getByText("비트코인", { exact: true }).first().waitFor();
    content = await board.innerText();
    assert.match(content, /이더리움/);
    assert.match(content, /11(?:\.0+)?%/);
    assert.doesNotMatch(content, /키옥시아/);
    await board.getByRole("button", { name: "금광주", exact: true }).click();
    await board.getByText("금광주 ETF", { exact: true }).first().waitFor();
    assert.match(await board.locator(".watchboard-comparison").innerText(), /비교할 수 없습니다/);
    assert.equal(await board.locator(".watchboard-plot").count(), 0);
    assert.equal(await board.locator('a[href*="example.invalid"]').count(), 0);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));

    if (inMenu) {
      await board.getByRole("button", { name: "반도체", exact: true }).click();
      await chartLink.waitFor();
      assert.ok(requests.every(([, days]) => days === "30"));
      assert.ok(requests.some(([group]) => group === "gold_miners"));
      await Promise.all([
        page.waitForResponse((response) => {
          const { pathname, searchParams } = new URL(response.url());
          return pathname === "/api/candles" && searchParams.get("ticker") === "2330.TW" && searchParams.get("interval") === "1d";
        }),
        chartLink.click(),
      ]);
      const hash = new URL(page.url()).hash;
      assert.match(hash, /^#\/charts\?t=2330\.TW&iv=1d$/);
      await page.locator(".price-chart-box canvas").first().waitFor({ state: "visible" });
      await page.goBack();
      await board.getByText("키옥시아", { exact: true }).waitFor();
      japanBreak = true;
      await page.reload();
    } else {
      await page.goto(`${base}/tests/market-watchboard.html?japanBreak`);
    }
    await board.getByText("장중 휴식", { exact: true }).waitFor();
    await page.close();
    console.log(`${width}px · ${inMenu ? "menu" : "independent"} watchboard passed`);
  }
} finally {
  await browser.close();
}
