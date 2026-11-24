import assert from "node:assert/strict";
import test from "node:test";
import { createLineFeedCache } from "../src/lineFeedReuse.ts";
import { chartFallbackHref, chartHref, chartLineAlert, signalDateTime, signalTime } from "../src/lineWatchNavigation.ts";

test("account scopes share only their own feed requests and cached results", async () => {
  const owner = createLineFeedCache();
  const visitor = createLineFeedCache();
  const day = "2026-11-24";
  let finishOwner;
  let ownerSignal;
  const loadOwner = signal => {
    assert.equal(ownerSignal, undefined);
    ownerSignal = signal;
    return new Promise(resolve => { finishOwner = resolve; });
  };
  const first = owner.acquire(day, "same", loadOwner);
  const second = owner.acquire(day, "same", loadOwner);
  first.release();
  assert.equal(ownerSignal.aborted, false);
  const separate = visitor.acquire(day, "same", async () => []);
  assert.deepEqual(await separate.promise, []);
  finishOwner(["owner only"]);
  assert.deepEqual(await second.promise, ["owner only"]);
  assert.deepEqual(await visitor.acquire(day, "same", async () => assert.fail("missed empty cache")).promise, []);
  assert.deepEqual(await owner.acquire(day, "same", async () => assert.fail("missed owner cache")).promise, ["owner only"]);
});

test("last subscriber aborts the request and a late completion cannot seed the cache", async () => {
  const scope = createLineFeedCache();
  let finish;
  let oldSignal;
  const first = scope.acquire("2026-11-24", "state", signal => {
    oldSignal = signal;
    return new Promise(resolve => { finish = resolve; });
  });
  const oldRejected = assert.rejects(first.promise, { name: "AbortError" });
  first.release();
  assert.equal(oldSignal.aborted, true);
  const newRead = scope.acquire("2026-11-24", "state", async () => ["new"]);
  finish(["old"]);
  await oldRejected;
  assert.deepEqual(await newRead.promise, ["new"]);
  assert.deepEqual(await scope.acquire("2026-11-24", "state", async () => assert.fail("cache was replaced")).promise, ["new"]);
});

test("navigation keeps the original timeframe and never puts the private line price in the URL", () => {
  for (const [timeframe, fallback] of [["10m", "5m"], ["120m", "60m"], ["1w", null]]) {
    const href = chartHref({ ticker: "NQ=F", timeframe, line_price: 21430 }, "opaque-selector");
    const query = new URLSearchParams(href.split("?")[1]);
    assert.equal(query.get("t"), "NQ=F");
    assert.equal(query.get("iv"), timeframe);
    assert.equal(query.get("signal_tf"), timeframe);
    assert.equal(query.get("signal_ref"), "opaque-selector");
    assert.equal(href.includes("21430"), false);
    const alert = chartLineAlert(query);
    assert.deepEqual(alert, { timeframe, display: timeframe, fallback,
      reference: "opaque-selector", signalId: null, exact: fallback !== null });
    const alternate = chartFallbackHref("NQ=F", alert);
    if (fallback) {
      const older = chartLineAlert(new URLSearchParams(alternate.split("?")[1]));
      assert.deepEqual(older, { ...alert, display: fallback, exact: false });
    } else assert.equal(alternate, null);
  }
  assert.equal(chartLineAlert(new URLSearchParams("signal_tf=120m&iv=240m")), null);
  assert.equal(chartLineAlert(new URLSearchParams("signal_tf=constructor&iv=constructor")), null);
});

test("external alert selectors stay attached to the exact signal across an explicit fallback", () => {
  const query = new URLSearchParams("t=NQ%3DF&signal_tf=120m&iv=120m&signal_id=41");
  const alert = chartLineAlert(query);
  assert.equal(alert.signalId, 41);
  const alternate = chartLineAlert(new URLSearchParams(chartFallbackHref("NQ=F", alert).split("?")[1]));
  assert.equal(alternate.signalId, 41);
  assert.equal(alternate.display, "60m");
  assert.equal(alternate.exact, false);
  for (const bad of ["0", "-2", "2.5", "2147483648", "x"]) {
    query.set("signal_id", bad);
    assert.equal(chartLineAlert(query).signalId, null);
  }
  assert.equal(chartLineAlert(new URLSearchParams("t=NQ%3DF&iv=60m")), null);
});

test("recorded_at controls signal order and accessible time while legacy intraday rows retain candle-end time", () => {
  const at = "2026-11-24T06:20:00Z";
  const s = { timeframe: "10m", at, recorded_at: "2026-11-24T07:55:00Z" };
  assert.equal(signalTime(s), Date.parse(s.recorded_at));
  assert.equal(signalDateTime(s), "2026-11-24T07:55:00.000Z");
  assert.equal(signalDateTime({ at, timeframe: "10m" }), "2026-11-24T06:30:00.000Z");
  assert.equal(signalDateTime({ at: "invalid", timeframe: "10m" }), undefined);
});
