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

test("관측 시각이 없거나 잘못된 프레임은 스트림 표본과 폴링 짝에서 제외한다", () => {
  m.resetLatency();
  const stamps = (id) => [{ key: "latency-missing-observed", id }];
  m.recordRest("quote", stamps(300), T, T + 50);
  m.recordStream("quote", stamps(300), stamps(301), undefined, iso(T + 100), true, T + 200);
  m.recordStream("quote", stamps(301), stamps(302), "invalid", iso(T + 200), true, T + 300);
  m.recordRest("quote", stamps(301), T + 400, T + 450);
  m.recordRest("quote", stamps(302), T + 500, T + 550);
  const { sse, rest } = m.latencyReport().channels.quote;
  assert.equal(sse.observed, null);
  assert.equal(sse.source, null);
  assert.equal(rest.observed, null);
  assert.equal(rest.unmatched, 2);
  assert.equal(rest.self, 0);
});

test("ready 전 재생과 같은 값의 반복을 제외하고 원천 시각은 있는 표본에서만 잰다", () => {
  m.resetLatency();
  const stamps = (id) => [{ key: "latency-reconnected-flow", id }];
  m.recordStream("flow", stamps("before"), stamps("same"), iso(T), null, false, T + 25);
  m.recordStream("flow", stamps("same"), stamps("same"), iso(T), null, true, T + 30);
  m.recordStream("flow", stamps("same"), stamps("a"), iso(T + 100), null, true, T + 150);
  m.recordStream("flow", stamps("a"), stamps("b"), iso(T + 200), "invalid", true, T + 250);
  m.recordStream("flow", stamps("b"), stamps("c"), iso(T + 300), iso(T + 50), true, T + 350);
  m.recordStream("flow", stamps("c"), stamps("c"), iso(T + 310), iso(T + 60), true, T + 360);
  const r = m.latencyReport();
  assert.deepEqual(r.channels.flow.sse.observed_raw, { n: 3, p50: 50, p95: 50, max: 50 });
  const sourceDelay = 300 + r.skew_ms;
  assert.deepEqual(r.channels.flow.sse.source, { n: 1, p50: sourceDelay, p95: sourceDelay, max: sourceDelay });
});

test("시계 차이는 가장 짧은 유효 왕복의 서버 − 브라우저 값으로 보정한다", async (t) => {
  m.resetLatency();
  let now = T;
  t.mock.method(Date, "now", () => now);
  const replies = [
    { rtt: 1, now: "invalid" },
    { rtt: 200, now: iso(T + 1 + 100 + 3000) },
    { rtt: 20, now: iso(T + 201 + 10 - 2000) },
  ];
  t.mock.method(globalThis, "fetch", async () => {
    const reply = replies.shift();
    now += reply.rtt;
    return { json: async () => ({ now: reply.now }) };
  });
  assert.deepEqual(await m.measureSkew(3), { skew_ms: -2000, rtt_ms: 20 });
  const f = (id) => [{ key: "view", id }];
  m.recordStream("futures", f("a"), f("b"), iso(T), null, true, T + 2100);
  const sse = m.latencyReport().channels.futures.sse;
  assert.equal(sse.observed.p50, 100);
  assert.equal(sse.observed_raw.p50, 2100);
  assert.equal(sse.source, null);
});

test("서버 시계가 앞서면 양의 보정으로 원시 음수 지연을 복원한다", async (t) => {
  m.resetLatency();
  t.mock.method(Date, "now", () => T);
  t.mock.method(globalThis, "fetch", async () => ({ json: async () => ({ now: iso(T + 2000) }) }));
  assert.deepEqual(await m.measureSkew(1), { skew_ms: 2000, rtt_ms: 0 });
  const f = (id) => [{ key: "view", id }];
  m.recordStream("futures", f("a"), f("b"), iso(T), null, true, T - 1900);
  const sse = m.latencyReport().channels.futures.sse;
  assert.equal(sse.observed.p50, 100);
  assert.equal(sse.observed_raw.p50, -1900);
});

test("시각 보정에 쓸 수 없는 응답뿐이면 새 오프셋을 결정하지 않는다", async (t) => {
  t.mock.method(Date, "now", () => T);
  const fetch = t.mock.method(globalThis, "fetch", async () => ({ json: async () => ({ now: iso(T + 500) }) }));
  assert.deepEqual(await m.measureSkew(1), { skew_ms: 500, rtt_ms: 0 });
  fetch.mock.mockImplementation(async () => ({ json: async () => ({}) }));
  assert.equal(await m.measureSkew(2), null);
  assert.equal(m.latencyReport().skew_ms, 500);
});
