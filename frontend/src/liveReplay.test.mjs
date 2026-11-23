import assert from "node:assert/strict";
import { test } from "node:test";
import { pending, remember, replayOver, REPLAY_KEEP_MS } from "./liveReplay.ts";

// 수급 패널처럼: 값 하나, 시각은 freshness.fetched_at — apply 는 메시지 값으로 바꾼다
const flow = (price, at) => ({ price, freshness: { fetched_at: at } });
const msg = (price, at) => ({ kind: "flow", key: "KOSPI:1d", payload: flow(price, at) });
const apply = (_prev, m) => m.payload;
const stamps = (d) => [{ key: "KOSPI:1d", at: d.freshness.fetched_at }];

test("늦게 온 REST 옛 값 위에 그 사이 받은 SSE 새 값을 다시 얹는다", () => {
  const rest = flow(2601, "2026-11-23T02:00:00+00:00");          // 요청 시점의 값
  const got = replayOver(rest, [msg(2688, "2026-11-23T02:01:00+00:00")], apply, stamps);
  assert.equal(got.price, 2688);
});

test("REST 가 더 새 값을 읽어 왔으면 그보다 오래된 메시지는 건너뛴다", () => {
  const rest = flow(2700, "2026-11-23T02:02:00+00:00");
  const got = replayOver(rest, [msg(2688, "2026-11-23T02:01:00+00:00")], apply, stamps);
  assert.equal(got, rest);
});

test("시각이 같으면 메시지가 이기고, 여러 메시지는 순서대로 얹는다", () => {
  const rest = flow(2601, "2026-11-23T02:00:00+00:00");
  const got = replayOver(rest, [msg(2650, "2026-11-23T02:00:00+00:00"), msg(2688, "2026-11-23T02:01:00+00:00")],
    apply, stamps);
  assert.equal(got.price, 2688);
});

test("시각이 없는 채널(알림)은 apply 의 중복 거르기로 그대로 얹는다", () => {
  const day = { date: "2026-11-23", items: [{ id: "a" }] };
  const add = (prev, m) => (prev.items.some((x) => x.id === m.payload.id) ? undefined
    : { ...prev, items: [...prev.items, m.payload] });
  const got = replayOver(day, [{ payload: { id: "a" } }, { payload: { id: "b" } }], add);
  assert.deepEqual(got.items.map((x) => x.id), ["a", "b"]);
});

test("시세판처럼 여러 키: 바뀐 칸만 시각을 본다", () => {
  const board = { items: [{ t: "AAPL", p: 182, at: "2026-11-23T02:00:05+00:00" }, { t: "MSFT", p: 400, at: "2026-11-23T02:00:09+00:00" }] };
  const quote = (t, p, at) => ({ key: t, payload: { price: p, fetched_at: at } });
  const set = (prev, m) => ({ items: prev.items.map((it) => (it.t === m.key ? { ...it, p: m.payload.price, at: m.payload.fetched_at } : it)) });
  const s = (d) => d.items.map((it) => ({ key: it.t, at: it.at }));
  const got = replayOver(board, [quote("AAPL", 183, "2026-11-23T02:00:07+00:00"), quote("MSFT", 399, "2026-11-23T02:00:08+00:00")],
    set, s);
  assert.deepEqual(got.items.map((it) => it.p), [183, 400]);   // MSFT 의 메시지는 REST 값보다 오래됐다
});


test("최신값 채널은 스트림 키마다 마지막 메시지만 기억하고, 그 기억 전부를 다시 얹을 후보로 낸다", () => {
  let list = [];
  for (let n = 0; n < 300; n++) list = remember(list, "board:A,B", n % 2 ? "B" : "A", { n }, true, 1000 + n);
  assert.deepEqual(list.map((a) => a.m.n), [298, 299]);               // 30종목 × 초당 메시지가 서로를 밀어내지 않는다
  assert.deepEqual(pending(list, "board:A,B", 5000, true).map((m) => m.n), [298, 299]);   // 요청 전에 받은 것도
  assert.deepEqual(pending(list, "other", 0, true), []);
});

test("알림은 모두 기억하되 요청 뒤에 받은 것만 다시 얹고, 오래된 기억은 버린다", () => {
  let list = [];
  list = remember(list, "alerts", "flow_shift", { id: 1 }, false, 1000);
  list = remember(list, "alerts", "flow_shift", { id: 2 }, false, 2000);
  assert.deepEqual(pending(list, "alerts", 1500, false).map((m) => m.id), [2]);
  list = remember(list, "alerts", "x", { id: 3 }, false, 1001 + REPLAY_KEEP_MS);
  assert.deepEqual(list.map((a) => a.m.id), [2, 3]);                   // 1 은 보존창(KEEP) 밖, 2 는 안
});

test("시각을 읽지 못하면 메시지가 이기고, 표기가 달라도 같은 시각은 같게 본다", () => {
  const rest = flow(2601, null);
  assert.equal(replayOver(rest, [msg(2688, "2026-11-23T02:01:00+00:00")], apply, stamps).price, 2688);
  const kst = flow(2601, "2026-11-23T11:01:00+09:00");
  assert.equal(replayOver(kst, [msg(2688, "2026-11-23T02:01:00Z")], apply, stamps).price, 2688);   // 같은 순간: 메시지
  const ms = flow(2601, "2026-11-23T02:01:00.500Z");
  assert.equal(replayOver(ms, [msg(2688, "2026-11-23T02:01:00+00:00")], apply, stamps), ms);       // 0.5초 오래됨
});
