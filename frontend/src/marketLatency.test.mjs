import assert from "node:assert/strict";
import { test } from "node:test";

// 모드는 모듈을 읽을 때 정해진다 — poll 모드로 읽는다
globalThis.localStorage = { getItem: (k) => (k === "desk.marketStream" ? "poll" : null) };
const m = await import("./marketLatency.ts");

const T = Date.parse("2026-11-23T01:00:00Z");
const iso = (t) => new Date(t).toISOString();
const q = (price) => [{ key: "005930.KS", id: price }];

test("스트림은 바뀐 키만, ready 뒤에만, observed_at(ms)부터 잰다", () => {
  m.recordStream("quote", q(100), q(101), iso(T), null, false, T + 30);        // 재접속 스냅숏: 짝짓기용으로만
  m.recordStream("quote", q(101), q(101), iso(T + 500), null, true, T + 540);  // 같은 값: 바뀜 아님
  m.recordStream("quote", q(101), q(102), iso(T + 1000), null, true, T + 1040);
  const r = m.latencyReport();
  assert.equal(r.mode, "poll");
  assert.deepEqual(r.channels.quote.sse.observed, { n: 1, p50: 40, p95: 40, max: 40 });
});

test("폴링은 스트림이 먼저 받은 같은 값과 짝지어 그 관측 시각부터 잰다 — 폴링 대기가 들어간다", () => {
  m.resetLatency();
  const key = (price) => [{ key: "000660.KS", id: price }];
  m.recordRest("quote", key(200), T, T + 50);                                   // 기준선
  m.recordStream("quote", key(200), key(201), iso(T + 1000), null, true, T + 1040);
  m.recordRest("quote", key(200), T + 2000, T + 2050);                          // 같은 값: 바뀜 아님
  m.recordRest("quote", key(201), T + 15000, T + 15050);                        // 14초 뒤에야 화면에
  m.recordStream("quote", key(201), key(202), iso(T + 16000), null, true, T + 16040);
  m.recordRest("quote", key(202), T + 15900, T + 16100);                        // 요청 뒤에 스트림에 나타남: self
  m.recordRest("quote", key(999), T + 30000, T + 30050);                        // 스트림에 없던 값: unmatched
  const rest = m.latencyReport().channels.quote.rest;
  assert.deepEqual(rest.observed, { n: 1, p50: 14050, p95: 14050, max: 14050 });
  assert.equal(rest.self, 1);
  assert.equal(rest.unmatched, 1);
});

test("같은 가격이 다시 나오면 요청 전 마지막 도착 값일 때만 짝짓는다", () => {
  m.resetLatency();
  const key = (price) => [{ key: "035420.KS", id: price }];
  m.recordRest("quote", key(71100), T, T + 50);                                       // 기준선
  m.recordStream("quote", key(71100), key(71000), iso(T + 1000), null, true, T + 1040);
  m.recordStream("quote", key(71000), key(71100), iso(T + 2000), null, true, T + 2040);
  m.recordRest("quote", key(71000), T + 14700, T + 14750);   // 요청 전 마지막 도착은 71,100 — 예전 71,000 과 짝짓지 않는다
  let rest = m.latencyReport().channels.quote.rest;
  assert.equal(rest.observed, null);
  assert.equal(rest.unmatched, 1);
  m.recordStream("quote", key(71100), key(71000), iso(T + 14600), null, true, T + 15200);   // 1초 병합으로 늦게 온 같은 틱
  m.recordRest("quote", key(71100), T + 29700, T + 29750);   // 요청 전 마지막 도착(15.2초의 71,000)과 다르다
  rest = m.latencyReport().channels.quote.rest;
  assert.equal(rest.unmatched, 2);
  assert.equal(rest.observed, null);
});

test("원천 시각이 있으면 원천 → 화면도 낸다", () => {
  m.resetLatency();
  const f = (id) => [{ key: "KOSPI:1m", id }];
  m.recordStream("flow", f("a"), f("b"), iso(T + 60000), iso(T + 30000), true, T + 60100);
  const sse = m.latencyReport().channels.flow.sse;
  assert.equal(sse.observed.p50, 100);
  assert.equal(sse.source.p50, 30100);
});

test("시계 차이는 서버 − 브라우저로 더해 보정한다", async () => {
  m.resetLatency();
  const serverAhead = 2000;
  globalThis.fetch = async () => ({ json: async () => ({ now: new Date(Date.now() + serverAhead).toISOString() }) });
  const got = await m.measureSkew(3);
  assert.ok(Math.abs(got.skew_ms - serverAhead) <= 20);
  const f = (id) => [{ key: "view", id }];
  m.recordStream("futures", f("a"), f("b"), iso(T), null, true, T + 100 - serverAhead);   // 브라우저 시계가 2초 늦다
  const sse = m.latencyReport().channels.futures.sse;
  assert.ok(Math.abs(sse.observed.p50 - 100) <= 20);
  assert.ok(Math.abs(sse.observed_raw.p50 - (100 - serverAhead)) <= 1);
});
