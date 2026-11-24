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
    ticker, price: 125, price_source: "live", facts: { name: ticker }, recent: { days: [] },
    indicators: { rsi: { value: 45, zone: "neutral" } },
    short: { available: true, percent_of_float: 0 },
    levels: { resistances: [{ step: 1, price: 999, distance_pct: 123, source: "volume" }], supports: [] },
  };
}

function board(ticker) {
  const price = ticker === "NVDA" ? 100 : 125;
  const noQuote = ticker === "AAPL" || ticker === "005930.KS";
  return { items: [{ ticker, state: ticker === "SOXL" || ticker === "005930.KS" ? "estimate" : noQuote ? "unavailable" : "live",
    price: ticker === "SOXL" ? 900.01 : price, close: ticker === "005930.KS" ? 128 : noQuote ? null : 124,
    close_date: ticker === "005930.KS" ? "2026-11-24" : noQuote ? null : "2026-11-23", change_pct: 0,
    quote_source: ticker === "NVDA" ? "yahoo" : "toss", fetched_at: found }] };
}

function chartRead(ticker) {
  const level = ticker === "SOXX" ? 132.125 : ticker === "SOXL" ? 151 : 103;
  return { ticker, generated_at: found, conflicts: [], summary: "", note: "",
    timeframes: ticker === "AAPL" || ticker === "005930.KS" ? [] : [{
      timeframe: ticker === "NVDA" ? "1h" : "1d", price: ticker === "NVDA" ? 100 : 125, as_of: found,
      levels: { above: [{ price: level, name: "합성 저항", distance_pct: ticker === "SOXX" ? 5.7 : 3 }],
        below: [{ price: 98, name: "합성 지지", distance_pct: -21.6 }] },
    }] };
}

function candles(ticker, interval) {
  return { ticker, interval, source: "yahoo", fetched_at: found, bars: Array.from({ length: 30 }, (_, i) => ({
    at: new Date(Date.UTC(2026, 9, ticker === "NQ=F" && interval === "1w" ? 5 + i * 7 : 1 + i, 5)).toISOString(),
    open: 119 + i / 4, high: 127 + i / 20, low: 114 + i / 4, close: 121 + i / 4, volume: 10_000,
  })) };
}

function weeklyOversold() {
  const week = { start: "2026-10-12", closed_at: "2026-10-16T20:00:00Z", confirmed_at: "2026-10-18T23:00:00Z",
    fetched_at: "2026-10-19T00:00:00Z", source: "yahoo" };
  const point = { status: "matched", week, evidence: {}, matched_conditions: [], min_hits: 2 };
  return {
    current: { ...point, observed_at: found }, markers: [{ ...point, close: 124 }],
    settings: { enabled: false, min_hits: 2, rsi_limit: 30, price_conditions: [] },
    study: { status: "unavailable", coverage: { scanned_weeks: 2, requested_weeks: 52, unverified_weeks: 1, matched_weeks: 1, edge_sample_count: 0 } },
    channels: [], deliveries: [],
  };
}

function gate() {
  let unlock;
  const ready = new Promise((resolve) => { unlock = resolve; });
  return { ready, unlock };
}

try {
  for (const width of [390, 1440]) {
    const page = await browser.newPage({ viewport: { width, height: 844 }, colorScheme: width === 390 ? "light" : "dark" });
    const first = gate();
    const profileGate = gate();
    const chartGate = gate();
    const obsolete = gate();
    let aaplReads = 0;
    let soxxReads = 0;
    let soxlReads = 0;
    let fallbackReads = 0;
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url());
      const ticker = url.searchParams.get("ticker");
      if (url.pathname === "/api/stock/profile") {
        if (ticker === "SOXX") await profileGate.ready;
        return route.fulfill({ json: profile(ticker) });
      }
      if (url.pathname === "/api/estimates") return route.fulfill({ json: board(url.searchParams.get("tickers")) });
      if (url.pathname === "/api/chart-read") {
        if (ticker === "SOXX") await chartGate.ready;
        return route.fulfill({ json: chartRead(ticker) });
      }
      if (url.pathname === "/api/levels" && ticker === "AAPL" && ++fallbackReads > 1) {
        return route.fulfill({ json: { ticker, reference_price: 125, reference: "close", as_of: "2026-11-23",
          computed_at: found, atr: null, levels: [
            { kind: "resistance", price: 130, timeframe: "1d", basis: "합성 구간", strength: 1, zone: [129, 131] },
          ] } });
      }
      if (url.pathname === "/api/candles") {
        const result = candles(ticker, url.searchParams.get("interval"));
        if (ticker === "AAPL" && url.searchParams.get("count") === "3") {
          result.bars = result.bars.slice(-3);
          result.session_elapsed = 0.5;
        } else if (ticker === "005930.KS" && url.searchParams.get("count") === "3") {
          result.bars = result.bars.slice(-3).map((bar, i) => ({ ...bar, at: `2026-11-${21 + i}T15:00:00+00:00` }));
          result.session_elapsed = null;
        }
        return route.fulfill({ json: result });
      }
      if (url.pathname === "/api/nq/weekly-oversold") return route.fulfill({ json: weeklyOversold() });
      if (url.pathname === "/api/favorites") return route.fulfill({ json: [] });
      if (url.pathname !== "/api/stock-social") return route.fulfill({ status: 503, json: { detail: "fixture unavailable" } });
      assert.equal(url.searchParams.get("days"), "7");
      if (ticker === "SOXX") {
        soxxReads += 1;
        await first.ready;
      }
      if (ticker === "SOXL" && ++soxlReads === 1) {
        await obsolete.ready;
      }
      if (ticker === "AAPL" && ++aaplReads === 1) return route.fulfill({ status: 503, json: { detail: "synthetic" } });
      const status = { SOXX: "available", SOXL: "stale", NVDA: "insufficient", AAPL: "unavailable", "005930.KS": "unavailable" }[ticker];
      await route.fulfill({ json: response(ticker, status) }).catch((error) => {
        if (ticker !== "SOXL") throw error;
      });
    });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.context().route("https://news.example/**", (route) => route.fulfill({ body: "합성 원문" }));
    await page.goto(new URL("/tests/stock-social.html", origin).href);
    await page.getByText(/실시간 시세/).waitFor();
    await page.getByText("지지·저항을 불러오는 중…", { exact: true }).waitFor();
    chartGate.unlock();
    const resistance = page.getByRole("table", { name: "가까운 지지·저항 가격" });
    await resistance.waitFor();
    const nearest = page.getByRole("link", { name: "저항 1 일봉 132.125 차트에서 강조" });
    assert.ok(await nearest.isVisible(), "price remains usable during score and profile loading");
    assert.ok(await page.getByText("반도체 ETF", { exact: true }).isVisible(), "known listing name remains visible");
    assert.match(await resistance.innerText(), /\+5\.70?%/);
    if (width === 390) {
      const rect = await nearest.boundingBox();
      assert.ok(rect && rect.y + rect.height < 844, "nearest resistance is above the fold");
    }
    assert.ok(await page.getByText("실시간 시세", { exact: false }).isVisible());
    assert.equal(await page.getByText("최근 10거래일", { exact: true }).isVisible(), false);
    profileGate.unlock();
    first.unlock();

    await page.getByRole("region", { name: "심리 지수" }).getByText("중립", { exact: true }).first().waitFor();
    const evidence = page.getByText(/뉴스·공시 근거/).first();
    const method = page.getByText(/점수 산식과 출처/).first();
    const main = page.locator(".sp-card");
    assert.equal(soxxReads, 1, "profile arrival does not restart the score");
    assert.ok((await resistance.boundingBox()).y < (await evidence.boundingBox()).y);
    assert.match(await main.innerText(), /커뮤니티/);
    assert.match(await main.innerText(), /7일/);
    const analysis = page.getByText("최근 거래일·지표·공매도", { exact: true });
    await analysis.click();
    await page.getByText(/최근 30개 일봉 추이/).waitFor();
    await page.getByText("최근 10거래일", { exact: true }).waitFor();
    assert.ok(await page.getByText("RSI(14)", { exact: true }).isVisible());
    assert.match(await analysis.locator("xpath=..").innerText(), /공매도 잔고/);
    await analysis.click();
    assert.equal(await page.getByRole("link", { name: "종목 분석 열기" }).getAttribute("href"), "#/?ticker=SOXX");

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
    assert.ok(await page.getByText(/시세 · 지연 가능/).isVisible());
    assert.match(await page.getByRole("link", { name: /저항 1 1시간봉/ }).getAttribute("href"), /iv=60m&p=103/);
    await page.getByRole("button", { name: "SOXL", exact: true }).click();
    await page.getByText(/지연|오래된/).first().waitFor();
    await evidence.click();
    await page.getByText("SOXL 합성 이전 근거").waitFor();
    assert.match(await page.getByRole("region", { name: "SOXL 가격과 지지·저항" }).innerText(), /최근 일봉 종가/);
    assert.equal(await page.getByText("900.01", { exact: true }).count(), 0);

    await page.getByRole("button", { name: "AAPL", exact: true }).click();
    await page.getByRole("button", { name: "다시 시도", exact: true }).click();
    await page.getByText(/미연결|자료 없음|미가용/).first().waitFor();
    assert.equal(aaplReads, 2);
    const closedQuote = page.getByRole("region", { name: "AAPL 가격과 지지·저항" });
    await closedQuote.getByText("128", { exact: true }).waitFor();
    await closedQuote.getByText("2026-10-29", { exact: true }).waitFor();
    await closedQuote.getByText("+0.20%", { exact: true }).waitFor();
    await page.getByText("지지·저항 미제공", { exact: true }).waitFor();
    await page.getByRole("button", { name: "지지·저항 다시 조회", exact: true }).click();
    await page.getByText("129~131", { exact: true }).waitFor();
    assert.match(await page.getByRole("link", { name: /저항 1 일봉 130/ }).getAttribute("href"), /t=AAPL&iv=1d&p=130/);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));

    await page.getByRole("button", { name: "005930.KS", exact: true }).click();
    const krxQuote = page.getByRole("region", { name: "005930.KS 가격과 지지·저항" });
    await krxQuote.getByText("2026-11-24", { exact: true }).waitFor();
    await krxQuote.getByText("+0.20%", { exact: true }).waitFor();
    await krxQuote.getByText("128.25", { exact: true }).waitFor();

    await page.getByRole("button", { name: "SOXX", exact: true }).click();
    await nearest.click();
    await page.waitForURL((url) => url.hash === "#/charts?t=SOXX&iv=1d&p=132.125");
    await page.getByText(/종목 요약에서 선택한 가격선/).waitFor();
    await page.locator("canvas").first().waitFor();
    // The chosen resistance is above every candle. Check the actual horizontal highlight is still visible.
    await page.waitForFunction(() => {
      const hex = getComputedStyle(document.documentElement).getPropertyValue("--warn").trim();
      const rgb = [1, 3, 5].map((index) => Number.parseInt(hex.slice(index, index + 2), 16));
      return [...document.querySelectorAll("canvas")].some((canvas) => {
        if (canvas.width < 100 || canvas.height < 40) return false;
        const ctx = canvas.getContext("2d", { willReadFrequently: true });
        if (!ctx) return false;
        const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height);
        for (let y = 4; y < canvas.height - 4; y += 1) {
          let matched = 0;
          for (let x = 8; x < canvas.width - 8; x += 8) {
            const i = (y * canvas.width + x) * 4;
            if (data[i + 3] > 240 && rgb.every((value, channel) => Math.abs(data[i + channel] - value) < 3)) matched += 1;
          }
          if (matched * 8 > canvas.width * 0.55) return true;
        }
        return false;
      });
    }, null, { timeout: 8000 });
    const candlePeriod = page.getByRole("group", { name: "봉 주기", exact: true });
    assert.equal(await candlePeriod.getByRole("button", { name: "일", exact: true }).getAttribute("aria-pressed"), "true");
    await candlePeriod.getByRole("button", { name: "주", exact: true }).click();
    await page.waitForURL((url) => url.hash === "#/charts?t=SOXX&iv=1w");
    await page.getByText(/종목 요약에서 선택한 가격선/).waitFor({ state: "hidden" });
    await page.evaluate(() => { window.location.hash = "#/charts?t=SOXX&iv=1w&p=132.125&p=135"; });
    await page.waitForURL((url) => url.hash.endsWith("&p=132.125&p=135"));
    await page.getByText(/종목 요약에서 선택한 가격선/).waitFor({ state: "hidden" });
    await page.evaluate(() => { window.location.hash = "#/charts?t=NQ%3DF&iv=1w"; });
    await page.getByRole("heading", { name: "NQ=F", exact: false }).first().waitFor();
    const readChart = page.getByRole("button", { name: "차트 읽기", exact: true });
    if (await readChart.getAttribute("aria-pressed") === "false") await readChart.click();
    const nqMarker = page.getByRole("button", { name: "2026-10-12 주봉 과매도 겹침" });
    await nqMarker.click();
    await page.getByRole("heading", { name: "2026-10-12 주 관찰 근거" }).waitFor();
    await page.getByRole("checkbox", { name: "차트에 과매도 마커 표시" }).uncheck();
    await nqMarker.waitFor({ state: "hidden" });
    assert.deepEqual(errors, []);
    await page.close();
    console.log(`${width}px stock social regression passed`);
  }
} finally {
  await browser.close();
}
