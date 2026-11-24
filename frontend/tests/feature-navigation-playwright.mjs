// Isolated Vite: node tests/feature-navigation-playwright.mjs <origin> <playwright/index.mjs>
import assert from "node:assert/strict";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const [base, playwrightPath] = process.argv.slice(2);
assert.ok(base && playwrightPath, "supply a Vite origin and Playwright index.mjs");
const { chromium } = await import(pathToFileURL(resolve(playwrightPath)));
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL });

async function urlIs(page, hash) {
  await page.waitForURL((url) => url.hash === hash);
}

async function activeIs(menu, label) {
  const link = menu.locator('a[aria-current="page"]').and(menu.getByRole("link", { name: label, exact: true }));
  await link.waitFor({ state: "attached" });
}

async function isExpanded(button, expanded) {
  const name = String(expanded);
  await button.waitFor();
  assert.equal(await button.getAttribute("aria-expanded"), name);
}

async function verifyHelp(page, mobile) {
  const button = page.getByRole("button", { name: "화면 도움말", exact: true });
  await isExpanded(button, false);
  if (mobile) {
    await button.tap();
    await isExpanded(button, true);
    await button.tap();
    await isExpanded(button, false);
    return;
  }
  await button.hover();
  await isExpanded(button, true);
  await page.locator(".brand").hover();
  await isExpanded(button, false);
  await page.getByRole("navigation", { name: "메뉴", exact: true }).getByRole("link").first().focus();
  for (let index = 0; index < 45; index++) {
    if (await button.evaluate((element) => document.activeElement === element)) break;
    await page.keyboard.press("Tab");
  }
  assert.ok(await button.evaluate((element) => document.activeElement === element));
  await isExpanded(button, true);
  await page.keyboard.press("Escape");
  await isExpanded(button, false);
  assert.ok(await button.evaluate((element) => document.activeElement === element));
  await page.keyboard.press("Enter");
  await isExpanded(button, true);
  await page.locator(".backtest h2").first().click();
  await isExpanded(button, false);
}

try {
  for (const width of [390, 1440]) {
    const mobile = width === 390;
    const page = await browser.newPage({ viewport: { width, height: 900 }, hasTouch: mobile, isMobile: mobile });
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url()), path = url.pathname;
      if (path === "/api/auth/me") return route.fulfill({ json: { username: "fixture", otp_enabled: false } });
      if (path === "/api/backtest/reports") return route.fulfill({ json: [] });
      if (path === "/api/runs") return route.fulfill({ json: { items: [] } });
      if (path === "/api/candles") {
        return route.fulfill({ json: {
          ticker: url.searchParams.get("ticker"), interval: url.searchParams.get("interval"), source: "synthetic",
          fetched_at: "2026-11-20T21:00:00Z",
          bars: [2, 9, 16].map((day, index) => ({
            at: `2026-11-${String(day).padStart(2, "0")}T14:30:00Z`,
            open: 30 + index, high: 33 + index, low: 29 + index, close: 32 + index, volume: 1000,
          })),
        } });
      }
      return route.fulfill({ status: 503, json: { detail: "synthetic unavailable" } });
    });
    await page.goto(`${base}/#/`);
    const nav = page.getByRole("navigation", { name: "메뉴", exact: true });
    const group = (name) => nav.getByRole("link", { name, exact: true });
    const submenu = (name) => page.getByRole("navigation", { name: `${name} 하위 메뉴`, exact: true });

    await group("시장").click();
    await urlIs(page, "#/live");
    await activeIs(submenu("시장"), "실시간 시세");
    await submenu("시장").getByRole("link", { name: "수급·포지셔닝", exact: true }).click();
    const flowTab = page.locator("#market-flow").getByRole("button", { name: "오늘 장중", pressed: true });
    await flowTab.waitFor();
    await urlIs(page, "#/?flow=intraday&view=flow");
    await page.reload();
    await flowTab.waitFor();
    await urlIs(page, "#/?flow=intraday&view=flow");
    await activeIs(submenu("시장"), "수급·포지셔닝");
    assert.equal(await submenu("시장").getByRole("link", { name: /현황판|시장 모니터/ }).count(), 0);
    await page.goto(`${base}/#/market`);
    await urlIs(page, "#/");
    await flowTab.waitFor();
    await activeIs(nav, "대시보드");

    await group("분석").click();
    await urlIs(page, "#/history");
    await submenu("분석").getByRole("link", { name: "백테스트", exact: true }).click();
    await urlIs(page, "#/backtest");
    await page.locator(".backtest h2").first().waitFor();
    await verifyHelp(page, mobile);
    await page.goBack();
    await urlIs(page, "#/history");
    await page.goForward();
    await urlIs(page, "#/backtest");
    await page.reload();
    await activeIs(submenu("분석"), "백테스트");

    await group("차트").click();
    await urlIs(page, "#/charts");
    await Promise.all([
      page.waitForResponse((response) => {
        const { pathname, searchParams } = new URL(response.url());
        return pathname === "/api/candles" && searchParams.get("ticker") === "NQ=F" && searchParams.get("interval") === "1w";
      }),
      page.goto(`${base}/#/charts?t=NQ%3DF&iv=1w`),
    ]);
    await urlIs(page, "#/charts?t=NQ%3DF&iv=1w");
    await activeIs(nav, "차트");
    await page.goto(`${base}/#/chart/SOXL?interval=1d&read=1`);
    await urlIs(page, "#/chart/SOXL?interval=1d&read=1");
    await activeIs(nav, "차트");
    await page.locator("main.chart-view").waitFor({ state: "attached" });
    assert.equal(await page.getByRole("button", { name: "화면 도움말", exact: true }).count(), 0);
    await page.goto(`${base}/#/charts`);

    await group("운영").click();
    await urlIs(page, "#/review");
    await activeIs(submenu("운영"), "매매 복기");
    await submenu("운영").getByRole("link", { name: "설정", exact: true }).click();
    await urlIs(page, "#/settings");
    await page.goto(`${base}/#/calendar`);
    await activeIs(submenu("시장"), "시장 일정");
    await page.goto(`${base}/#/reports/fixture-report`);
    await activeIs(submenu("분석"), "리포트");
    await page.goto(`${base}/#/briefs/morning?date=2026-11-20`);
    await activeIs(submenu("분석"), "아침 시황");
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await page.close();
    console.log(`${width}px · groups, legacy links, history navigation and help passed`);
  }
} finally {
  await browser.close();
}
