// Isolated Vite: node tests/line-alert-chart-playwright.mjs <origin> <playwright/index.mjs>
import assert from "node:assert/strict";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const [base, playwrightPath] = process.argv.slice(2);
assert.ok(base && playwrightPath, "supply a Vite origin and Playwright index.mjs");
const { chromium } = await import(pathToFileURL(resolve(playwrightPath)));
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL });
const at = "2026-11-20T14:30:00Z";
const candle = (stamp) => ({ at: stamp, open: 101, high: 103, low: 100, close: 102, volume: 300 });
const exactHref = (frame, id, ticker = "SOXL") => "#/charts?" + new URLSearchParams({ t: ticker, iv: frame,
  signal_tf: frame, signal_id: String(id) });

try {
  for (const width of [390, 1440]) {
    const page = await browser.newPage({ viewport: { width, height: 900 }, hasTouch: width === 390 });
    const frameButton = (name, pressed) =>
      page.getByRole("group", { name: "봉 주기", exact: true }).getByRole("button", { name, exact: true, pressed });
    let account = "owner";
    let twoHourSupply = true;
    let releaseReply;
    let beginReply;
    const replyStarted = new Promise(resolve => { beginReply = resolve; });
    const heldReply = new Promise(resolve => { releaseReply = resolve; });
    const requests = [];
    await page.addInitScript(() => {
      window.chartLabels = new Set();
      const fillText = CanvasRenderingContext2D.prototype.fillText;
      CanvasRenderingContext2D.prototype.fillText = function (label, ...args) {
        window.chartLabels.add(label);
        return fillText.call(this, label, ...args);
      };
    });
    await page.route("**/api/**", async route => {
      const { pathname, searchParams } = new URL(route.request().url());
      if (pathname === "/api/auth/me") return route.fulfill({ json: { username: account, otp_enabled: false } });
      if (pathname === "/api/notify/line-watch" && searchParams.has("signal_id")) {
        const id = searchParams.get("signal_id");
        const signal = account === "owner" && id !== "4" ? {
          ticker: "SOXL", timeframe: id === "1" ? "10m" : "120m", at, line_price: 185,
        } : null;
        if (id === "5" && account === "owner") {
          beginReply();
          await heldReply;
        }
        return route.fulfill({ json: { signal } }).catch(() => {});
      }
      if (pathname === "/api/notify/line-watch") {
        return route.fulfill({ json: {
          enabled: true, can_edit: true, tickers: [], timeframes: [], events: {}, rules: null,
          allowed: { timeframes: [], events: [], max_tickers: 10, support: {} },
          limits: { cooldown_min: 30, cooldown_bars: 2, daily_max: 10, per_ticker_max: 3 },
          recent: [{ signal_id: 1, ticker: "SOXL", timeframe: "10m", line_price: 185, price: 102, at,
            trade_date: "2026-11-20", rule: "touch_resistance", event: "touch", line_id: "fixture",
            text: "SOXL 10분 저항 닿음" }],
        } });
      }
      if (pathname === "/api/candles") {
        const ticker = searchParams.get("ticker");
        const frame = searchParams.get("interval");
        const exact = searchParams.get("line_alert") === "true";
        const count = Number(searchParams.get("count"));
        requests.push({ ticker, frame, exact, count, live: false });
        let bars = [candle(at)];
        if (exact && frame === "10m") {
          bars = Array.from({ length: 330 }, (_, index) =>
            candle(new Date(Date.parse(at) + index * 600_000).toISOString())).slice(-count);
        } else if (exact && frame === "120m" && !twoHourSupply) bars = [];
        return route.fulfill({ json: { ticker, interval: frame, source: "mock", fetched_at: at, bars } });
      }
      if (pathname === "/api/candles/live") {
        requests.push({ frame: searchParams.get("interval"), live: true });
      }
      return route.fulfill({ status: 503, json: { detail: "synthetic unavailable" } });
    });

    await page.goto(base + "/#/settings");
    const inApp = page.getByRole("link", { name: /SOXL 10분 저항 닿음/ });
    await inApp.waitFor();
    const inAppHref = await inApp.getAttribute("href");
    assert.match(inAppHref, /iv=10m.*signal_id=1/);
    // Open the saved link without the click handler's per-tab memory entry.
    await page.goto(base + inAppHref);
    await frameButton("10분", true).waitFor();
    await page.getByRole("img", { name: /SOXL 10분봉 가격 차트.*마지막 종가/ }).waitFor();
    await page.getByText(/알림 당시 선 가격 185/).waitFor();
    await page.waitForFunction(() => window.chartLabels.has("▶"));
    assert.ok(requests.some(r => r.frame === "10m" && r.exact && r.count >= 330));
    assert.equal(await page.getByText(/알림 시각의 10분봉 공급/).count(), 0);
    assert.equal(requests.some(r => r.frame === "10m" && r.live), false);
    assert.equal(new URL(page.url()).hash.includes("185"), false);
    await page.reload();
    await page.getByText(/알림 당시 선 가격 185/).waitFor();

    await page.goto(base + exactHref("120m", 3));
    await page.getByRole("img", { name: /SOXL 2시간봉 가격 차트.*마지막 종가/ }).waitFor();
    await page.getByText(/알림 당시 선 가격 185/).waitFor();
    assert.equal(requests.some(r => r.frame === "120m" && r.live), false);
    twoHourSupply = false;
    await page.goto(base + exactHref("120m", 2));
    await frameButton("2시간", true).waitFor();
    await page.getByText(/2시간봉 공급을 확인할 수 없습니다/).waitFor();
    await page.getByRole("link", { name: "1시간봉으로 보기", exact: true }).click();
    await frameButton("1시간", true).waitFor();
    await page.getByText(/2시간봉 선 알림 · 1시간봉 대체 차트 · 알림 당시 선 가격 185/).waitFor();
    await page.getByRole("img", { name: /SOXL 1시간봉 가격 차트.*마지막 종가/ }).waitFor();
    assert.ok(requests.some(r => r.frame === "120m" && r.exact));
    assert.ok(requests.some(r => r.frame === "60m" && !r.exact && !r.live));
    await page.getByRole("link", { name: "2시간봉으로 보기", exact: true }).click();
    await frameButton("2시간", true).waitFor();
    await frameButton("일").click();
    await page.waitForURL(url => url.hash === "#/charts?t=SOXL&iv=1d");
    await page.getByRole("img", { name: /SOXL 일봉 가격 차트/ }).waitFor();
    assert.equal(await frameButton("2시간").count(), 0);
    assert.equal(await page.getByText(/알림 당시 선 가격 185/).count(), 0);

    await page.goto(base + "/#/charts?t=SOXL&iv=5m&signal_tf=10m");
    await page.getByText(/10분봉 선 알림 · 5분봉 대체 차트 · 알림 당시 선 가격을 확인할 수 없습니다/).waitFor();
    await page.goto(base + exactHref("120m", 4));
    await page.getByText(/알림 당시 선 가격을 확인할 수 없습니다/).waitFor();
    await page.goto(base + exactHref("10m", 1, "KORU"));
    await page.getByText(/알림 당시 선 가격을 확인할 수 없습니다/).waitFor();
    await page.goto(base + exactHref("120m", 5));
    await replyStarted;
    account = "visitor";
    await page.evaluate(() => window.dispatchEvent(new StorageEvent("storage", {
      key: "desk-session-change", newValue: "account-switch",
    })));
    await page.getByText(/알림 당시 선 가격을 확인할 수 없습니다/).waitFor();
    releaseReply();
    assert.equal(await page.getByText(/알림 당시 선 가격 185/).count(), 0);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await page.close();
    console.log(width + "px · exact frames, price line, fallback, legacy links and account switch passed");
  }
} finally {
  await browser.close();
}
