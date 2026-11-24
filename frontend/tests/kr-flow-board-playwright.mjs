// node tests/kr-flow-board-playwright.mjs <vite-origin> <playwright/index.mjs> [--menu]
import assert from "node:assert/strict";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { flowFixtures } from "./kr-flow-board-data.mjs";

const [base, playwrightPath, mode] = process.argv.slice(2);
assert.ok(base && playwrightPath, "supply a Vite origin and Playwright index.mjs");
const { chromium } = await import(pathToFileURL(resolve(playwrightPath)));
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL });
const menu = mode === "--menu";

try {
  for (const width of [390, 1440]) {
    const mobile = width === 390;
    const page = await browser.newPage({ viewport: { width, height: 900 }, hasTouch: mobile, isMobile: mobile });
    const candleRequests = [];
    if (menu) {
      await page.route("**/api/**", async (route) => {
        const url = new URL(route.request().url());
        if (url.pathname === "/api/auth/me") return route.fulfill({ json: { username: "fixture", otp_enabled: false } });
        if (url.pathname === "/api/runs") return route.fulfill({ json: { items: [] } });
        if (url.pathname === "/api/market/flow-board/kr") {
          assert.equal(url.searchParams.get("days"), "22");
          return route.fulfill({ json: flowFixtures[url.searchParams.get("market")] });
        }
        if (url.pathname === "/api/candles") {
          const ticker = url.searchParams.get("ticker"), interval = url.searchParams.get("interval");
          candleRequests.push([ticker, interval]);
          return route.fulfill({ json: {
            ticker, interval, source: "synthetic", fetched_at: "2026-11-24T06:40:00Z",
            bars: [20, 23, 24].map((day, index) => ({
              at: `2026-11-${day}T00:00:00Z`, open: 100 + index, high: 102 + index,
              low: 99 + index, close: 101 + index, volume: 1000,
            })),
          } });
        }
        return route.fulfill({ status: 503, json: { detail: "synthetic unavailable" } });
      });
      await page.goto(`${base}/#/live`);
      await page.getByRole("navigation", { name: "시장 하위 메뉴" }).getByRole("link", { name: "수급 현황판", exact: true }).click();
      await page.waitForURL((url) => url.hash === "#/flow-board/kr");
    } else {
      await page.goto(`${base}/tests/kr-flow-board.html`);
    }
    const board = page.locator(".kr-flow-board");
    await board.getByRole("heading", { name: "국장 수급 현황판", exact: true }).waitFor();
    const plot = board.locator(".kr-flow-scatter");
    const indexCard = board.locator("article").filter({ has: page.getByRole("heading", { name: "지수 종가", exact: true }) });
    const samsung = plot.getByRole("button", { name: /삼성전자 .*산점도 선택/ });
    const sk = plot.getByRole("button", { name: /SK하이닉스 .*산점도 선택/ });
    await samsung.waitFor();
    assert.match(await indexCard.innerText(), /전 거래일 대비\s+0%/);
    assert.equal(await indexCard.locator("time").first().getAttribute("datetime"), flowFixtures.kospi.market_summary.index.as_of);
    assert.equal(await plot.getByRole("button").count(), 49);
    assert.match(await plot.innerText(), /우위 0%/);
    assert.match(await plot.innerText(), /참여율 중앙값/);
    const picker = board.getByLabel("산점도 종목 선택");
    await picker.selectOption("000660.KS");
    assert.equal(await sk.getAttribute("aria-pressed"), "true");
    if (mobile) {
      await samsung.tap();
    } else {
      await sk.focus();
      await page.keyboard.press("ArrowLeft");
      assert.ok(await samsung.evaluate((dot) => document.activeElement === dot));
      await page.keyboard.press("ArrowRight");
      assert.ok(await sk.evaluate((dot) => document.activeElement === dot));
      assert.equal(await sk.getAttribute("aria-pressed"), "true");
      await page.keyboard.press("ArrowLeft");
    }
    assert.equal(await samsung.getAttribute("aria-pressed"), "true");
    const details = board.locator(".kr-flow-selection");
    const rankDate = flowFixtures.kospi.ranking.as_of;
    const holdingDate = flowFixtures.kospi.ranking.items[0].latest_ownership.as_of;
    const detailText = await details.innerText();
    assert.match(detailText, /47\.5/);
    assert.ok(detailText.includes(holdingDate));
    assert.ok((await board.innerText()).includes(rankDate));
    const favorite = board.locator(".kr-flow-favorite").filter({ hasText: "삼성전자" });
    assert.match(await favorite.innerText(), /0(?:\.0+)?%/);
    await favorite.getByText("삼성전자 거래일별 수치", { exact: true }).click();
    assert.equal(await favorite.locator("tbody tr").count(), 22);
    assert.match(await favorite.innerText(), /정보 없음|자료 없음|산출 불가|분모/);
    const chart = favorite.locator('a[href="#/charts?t=005930.KS&iv=1d"]');
    const analysis = favorite.locator('a[href="#/?ticker=005930.KS"]');
    const short = board.locator('a[href="#/?flow=short&shortTicker=000660.KS"]').last();
    assert.equal(await chart.count(), 1);
    assert.equal(await analysis.count(), 1);
    assert.equal(await short.count(), 1);
    await board.getByRole("button", { name: "코스닥", exact: true }).click();
    await board.getByRole("heading", { name: /에코프로비엠.*247540\.KQ/ }).waitFor();
    assert.match(await indexCard.innerText(), /932\.51 포인트/);
    assert.match(await indexCard.innerText(), /전 거래일 대비\s+자료 없음/);
    assert.equal(await plot.count(), 0);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));

    if (menu) {
      await board.getByRole("button", { name: "코스피", exact: true }).click();
      await chart.click();
      await page.waitForURL((url) => url.hash === "#/charts?t=005930.KS&iv=1d");
      await page.locator(".price-chart-box canvas").first().waitFor({ state: "visible" });
      assert.ok(candleRequests.some(([ticker, interval]) => ticker === "005930.KS" && interval === "1d"));
      await page.goBack();
      await analysis.click();
      await page.waitForURL((url) => url.hash === "#/?ticker=005930.KS");
      await page.goBack();
      await page.evaluate(() => localStorage.setItem("desk.marketFlow.v1", JSON.stringify({ market: "FUNDING" })));
      await short.click();
      await page.waitForURL((url) => url.hash === "#/?flow=short&shortTicker=000660.KS");
      const selected = page.getByRole("group", { name: "국내 공매도 종목 빠른 선택" })
        .getByRole("button", { name: "SK하이닉스", pressed: true });
      await selected.waitFor();
      await page.reload();
      await selected.waitFor();
      await page.locator("#market-flow").getByRole("button", { name: "펀딩비", pressed: true }).waitFor();
      await page.goto(`${base}/#/market`);
      await page.waitForURL((url) => url.hash === "#/");
      await page.locator("#market-flow").getByRole("button", { name: "오늘 장중", pressed: true }).waitFor();
    } else {
      await page.goto(`${base}/tests/kr-flow-board.html?holdKospi`);
      await board.getByRole("button", { name: "코스닥", exact: true }).click();
      await board.getByRole("heading", { name: /에코프로비엠.*247540\.KQ/ }).waitFor();
      await page.evaluate(async () => {
        window.dispatchEvent(new Event("fixture:release-kospi"));
        await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      });
      assert.equal(await board.getByRole("button", { name: "코스닥", exact: true, pressed: true }).count(), 1);
      assert.equal(await board.locator(".kr-flow-favorite").filter({ hasText: "삼성전자" }).count(), 0);
    }
    await page.close();
    console.log(`${width}px · ${menu ? "menu" : "independent"} domestic flow board passed`);
  }
} finally {
  await browser.close();
}
