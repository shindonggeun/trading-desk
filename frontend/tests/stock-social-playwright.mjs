// Isolated Vite: node tests/stock-social-playwright.mjs <origin> <playwright/index.mjs>
import assert from "node:assert/strict";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const [origin, driver] = process.argv.slice(2);
assert.ok(origin && driver, "supply a Vite origin and Playwright index.mjs");
const { chromium } = await import(pathToFileURL(resolve(driver)));
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL });
const found = "2026-11-24T02:34:00+00:00";
const link = "https://news.example/soxx";

function item(fields) {
  return {
    kind: "news", source: "gdelt", publisher: "news.example", title: "", summary: "",
    url: link, at: found, filed_date: null, time_basis: "gdelt_seen", relevance: "direct",
    tags: ["earnings"], sentiment: "positive", reason: "positive_phrase", weight: 0.8,
    contribution: 12.5, stale: false, ...fields,
  };
}

function response(ticker, status = "available") {
  const available = status === "available";
  const stale = status === "stale";
  return {
    ticker, days: 7, generated_at: "2026-11-24T03:00:00+00:00",
    method: { version: "headline-rules-v1", baseline: 50, min_samples: 3, min_publishers: 2 },
    coverage: { requested_days: 7, news_max_days: 3 },
    news: {
      status, score: available ? 50 : null, sample_count: available ? 3 : status === "insufficient" ? 1 : 0,
      publisher_count: available ? 2 : status === "insufficient" ? 1 : 0,
      reason: available ? null : stale ? "news_snapshot_stale" : status === "insufficient"
        ? "too_few_unique_headlines" : "news_source_unavailable",
      updated_at: available || stale ? found : null,
    },
    community: { status: "unavailable", score: null, sample_count: 0, reason: "sources_not_authorized", updated_at: null },
    sources: [
      { id: "gdelt", status, reason: null, updated_at: available || stale ? found : null, prior_http_status: null },
      { id: "sec_edgar", status: available ? "available" : "unavailable", reason: available ? null : "not_configured",
        updated_at: available ? found : null, prior_http_status: null },
      { id: "reddit", status: "unavailable", reason: "not_authorized", updated_at: null, prior_http_status: 404 },
    ],
    items: available ? [
      item({ title: "SOXX 합성 긍정 기사", summary: "예상치 상회 문구가 긍정으로 반영됐습니다." }),
      item({ title: "반도체 합성 부정 기사", summary: "전망 하향 문구가 부정으로 반영됐습니다.",
        publisher: "sector.example", url: "https://sector.example/chips",
        relevance: "indirect", tags: ["guidance"], sentiment: "negative", contribution: -12.5 }),
      item({ title: "반영값 0 합성 기사", summary: "상반된 문구가 함께 있습니다.",
        url: "javascript:alert(1)", sentiment: "neutral", reason: "conflicting_phrases", contribution: 0 }),
      item({ kind: "filing", source: "sec_edgar", publisher: "SEC EDGAR", title: "합성 SEC 공시",
        summary: "접수 메타데이터", url: "https://www.sec.example/filing",
        at: null, filed_date: "2026-11-20", time_basis: "edgar_filing_date", tags: ["filing"], sentiment: null,
        reason: "filing_metadata_unscored", weight: null, contribution: null }),
    ] : stale ? [
      item({ title: "SOXL 합성 이전 근거", weight: null, contribution: null, stale: true }),
    ] : [],
  };
}

function profile(ticker) {
  return {
    ticker, price: 125, price_source: "live", facts: { name: ticker }, recent: { days: [] }, indicators: {},
    levels: { resistances: [{ step: 1, price: 132, distance_pct: 5.6, source: "volume" }], supports: [] },
  };
}

function gate() {
  let unlock;
  const ready = new Promise((resolve) => { unlock = resolve; });
  return { ready, unlock };
}

try {
  for (const width of [390, 1440]) {
    const page = await browser.newPage({ viewport: { width, height: 900 }, colorScheme: width === 390 ? "light" : "dark" });
    const first = gate();
    const obsolete = gate();
    let aaplReads = 0;
    let soxxReads = 0;
    let soxlReads = 0;
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url());
      const ticker = url.searchParams.get("ticker");
      if (url.pathname === "/api/stock/profile") return route.fulfill({ json: profile(ticker) });
      assert.equal(url.pathname, "/api/stock-social");
      assert.equal(url.searchParams.get("days"), "7");
      if (ticker === "SOXX") {
        soxxReads += 1;
        await first.ready;
      }
      if (ticker === "SOXL" && ++soxlReads === 1) {
        await obsolete.ready;
      }
      if (ticker === "AAPL" && ++aaplReads === 1) return route.fulfill({ status: 503, json: { detail: "synthetic" } });
      const status = { SOXX: "available", SOXL: "stale", NVDA: "insufficient", AAPL: "unavailable" }[ticker];
      await route.fulfill({ json: response(ticker, status) }).catch((error) => {
        if (ticker !== "SOXL") throw error;
      });
    });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.context().route("https://news.example/**", (route) => route.fulfill({ body: "합성 원문" }));
    await page.goto(new URL("/tests/stock-social.html", origin).href);
    const resistance = page.getByRole("heading", { name: "저항선", exact: true });
    await resistance.waitFor();
    assert.ok(await page.getByRole("link", { name: /132/ }).isVisible(), "price remains usable during score loading");
    first.unlock();

    await page.getByText(/중립|상쇄/).first().waitFor();
    const evidence = page.getByText(/뉴스·공시 근거/).first();
    const method = page.getByText(/점수 산식과 출처/).first();
    const main = page.locator(".sp-card");
    assert.equal(soxxReads, 1, "profile arrival does not restart the score");
    assert.ok((await resistance.boundingBox()).y < (await evidence.boundingBox()).y);
    assert.match(await main.innerText(), /커뮤니티/);
    assert.match(await main.innerText(), /7일/);

    await evidence.focus();
    await page.keyboard.press("Enter");
    await page.getByText("SOXX 합성 긍정 기사").waitFor();
    await page.getByText("반도체 합성 부정 기사").waitFor();
    await page.getByText("합성 SEC 공시").waitFor();
    assert.match(await main.innerText(), /직접/);
    assert.match(await main.innerText(), /간접/);
    assert.match(await main.innerText(), /발견/);
    assert.match(await main.innerText(), /2026[.\-]\s*11[.\-]\s*20/);
    assert.match(await main.innerText(), /12\.5/);
    const original = page.locator(`a[href="${link}"]`);
    assert.equal(await original.getAttribute("target"), "_blank");
    assert.match(await original.getAttribute("rel"), /noreferrer/);
    assert.equal(await page.locator('a[href^="javascript:"]').count(), 0);
    const [article] = await Promise.all([page.waitForEvent("popup"), original.click()]);
    await article.waitForURL(link);
    await article.close();

    await evidence.focus();
    await page.keyboard.press("Enter");
    await page.keyboard.press("Tab");
    assert.equal(await method.evaluate((node) => node === document.activeElement || node.contains(document.activeElement)), true);
    await page.keyboard.press("Space");
    await page.getByText(/headline-rules-v1/).waitFor();
    assert.match(await main.innerText(), /404/);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));

    await Promise.all([
      page.waitForRequest((request) => request.url().includes("/api/stock-social?ticker=SOXL&")),
      page.getByRole("button", { name: "SOXL", exact: true }).click(),
    ]);
    assert.equal(await page.getByText("SOXX 합성 긍정 기사").count(), 0);
    await page.getByRole("button", { name: "NVDA", exact: true }).click();
    await page.getByText(/표본 부족/).first().waitFor();
    obsolete.unlock();
    assert.equal(await page.getByText("SOXL 합성 이전 근거").count(), 0);
    await page.getByRole("button", { name: "SOXL", exact: true }).click();
    await page.getByText(/지연|오래된/).first().waitFor();
    await evidence.click();
    await page.getByText("SOXL 합성 이전 근거").waitFor();

    await page.getByRole("button", { name: "AAPL", exact: true }).click();
    await page.getByRole("button", { name: "다시 시도", exact: true }).click();
    await page.getByText(/미연결|자료 없음|미가용/).first().waitFor();
    assert.equal(aaplReads, 2);
    assert.ok(await resistance.isVisible());
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    assert.deepEqual(errors, []);
    await page.close();
    console.log(`${width}px stock social regression passed`);
  }
} finally {
  await browser.close();
}
