import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import { test } from "node:test";

// boardLive.ts 는 Vite 처럼 확장자 없이 import 한다("./ticker") — node 시험에서는 .ts 를 붙여 찾는다
registerHooks({
  resolve(spec, ctx, next) {
    try {
      return next(spec, ctx);
    } catch (e) {
      if (spec.startsWith(".") && !spec.endsWith(".ts")) return next(`${spec}.ts`, ctx);
      throw e;
    }
  },
});
const { agoText, applyEstimate, applyQuote, boardLive, ESTIMATE_QUIET_MS, moveSplit } = await import("./boardLive.ts");

const T0 = Date.parse("2026-11-28T03:00:00Z");
const live = (ticker, price, extra = {}) => ({
  ticker, state: "live", price, change_pct: 0, close: 100, close_date: "2026-11-27", source: null,
  quote_source: "kis", session: "regular", fetched_at: "2026-11-28T02:59:50Z", ...extra,
});
const est = (ticker, price, at, extra = {}) => ({
  ticker, state: "estimate", price, change_pct: 1, close: 100, close_date: "2026-11-27",
  source: { kind: "binance_perp_mark", contract: `${ticker}USDT`, basis: null, multiple: 1 },
  quote_source: "binance-estimate", session: "closed", fetched_at: at, ...extra,
});
const msg = (key, payload, source = "binance-estimate", observed = "2026-11-28T03:00:00Z") =>
  ({ v: 1, kind: "quote", key, event_id: `${key}-${observed}-${Math.random()}`, source, source_at: observed, observed_at: observed, payload });
const estMsg = (key, price, at) => msg(key, {
  ticker: key, state: "estimate", price, change_pct: Math.round((price / 100 - 1) * 10_000) / 100, close: 100,
  close_date: "2026-11-27", close_at: "2026-11-27T21:00:00Z", session: "closed",
  source: { kind: "binance_perp_mark", contract: `${key}USDT`, basis: null, multiple: 1 },
  quote_source: "binance-estimate", fetched_at: at,
}, "binance-estimate", at);

test("추정 메시지는 추정 칸을 그 줄로 갈아 끼운다", () => {
  const board = { items: [est("AAPL", 101, "2026-11-28T02:59:50Z")] };
  const got = applyEstimate(board, estMsg("AAPL", 102.5, "2026-11-28T03:00:00Z"));
  assert.equal(got.items[0].price, 102.5);
  assert.equal(got.items[0].change_pct, 2.5);
  assert.equal(got.items[0].fetched_at, "2026-11-28T03:00:00Z");
  assert.equal(got.items[0].state, "estimate");
});

test("추정 불가 칸도 추정 메시지가 오면 추정으로 바뀐다", () => {
  const board = { items: [{ ...est("AAPL", null, null), state: "unavailable", source: null, quote_source: null }] };
  const got = applyEstimate(board, estMsg("AAPL", 99, "2026-11-28T03:00:00Z"));
  assert.equal(got.items[0].state, "estimate");
  assert.equal(got.items[0].quote_source, "binance-estimate");
  assert.equal(got.items[0].source.contract, "AAPLUSDT");
});

test("실시간 칸은 추정 메시지로 바꾸지 않는다(미국 장외 체결로 live 인 칸)", () => {
  const board = { items: [live("AAPL", 101, { session: "post", quote_source: "toss" })] };
  assert.equal(applyEstimate(board, estMsg("AAPL", 103, "2026-11-28T03:00:00Z")), undefined);
});

test("이미 가진 것보다 옛 추정은 얹지 않고, 같은 값 · 같은 시각이면 그대로", () => {
  const board = { items: [est("AAPL", 101, "2026-11-28T03:00:10Z")] };
  assert.equal(applyEstimate(board, estMsg("AAPL", 100, "2026-11-28T03:00:00Z")), undefined);
  assert.equal(applyEstimate(board, estMsg("AAPL", 101, "2026-11-28T03:00:10Z")), undefined);
  // 값이 같아도 새 계산이면 시각은 바꾼다("n초 전")
  assert.equal(applyEstimate(board, estMsg("AAPL", 101, "2026-11-28T03:00:20Z")).items[0].fetched_at, "2026-11-28T03:00:20Z");
});

test("맨 코드(005930)로 물은 칸도 추정을 받고 쉬기 판단에 쓴다 — 스트림은 물은 표기로 다시 보낸다", () => {
  const board = { items: [est("005930.KS", 70000, "2026-11-28T02:59:50Z", { close: 70000 })] };
  const spec = boardLive(["005930"], () => T0);
  const m = estMsg("005930", 70700, "2026-11-28T03:00:00Z");   // marketStream.quoteKeys 가 구독자 표기로 바꾼 메시지
  spec.received(board, m);
  assert.equal(spec.apply(board, m).items[0].price, 70700);
  assert.equal(spec.pauseWhen(board), true);
});

test("실시간 quote 는 정규장 칸에만 — 추정 칸은 그대로", () => {
  const board = { items: [live("NVDA", 180), est("AAPL", 101, "2026-11-28T02:59:50Z")] };
  const q = (key, price) => msg(key, { ticker: key, price, source: "kis", fetched_at: "2026-11-28T03:00:01Z" }, "kis");
  assert.equal(applyQuote(board, q("NVDA", 181)).items[0].price, 181);
  assert.equal(applyQuote(board, q("AAPL", 99)), undefined);
  const spec = boardLive(["NVDA", "AAPL"]);
  assert.equal(spec.apply(board, q("AAPL", 99)), undefined);
});

test("쉬기: 모든 칸이 정규장이거나 최근에 스트림 추정을 받은 칸일 때만", () => {
  let now = T0;
  const spec = boardLive(["NVDA", "AAPL"], () => now);
  const board = { items: [live("NVDA", 180), est("AAPL", 101, "2026-11-28T02:59:50Z")] };
  assert.equal(spec.pauseWhen(board), false);                 // REST 로만 받은 추정 칸 — 스트림이 덮는지 모른다
  spec.received(board, estMsg("AAPL", 101.5, "2026-11-28T03:00:00Z"));
  assert.equal(spec.pauseWhen(board), true);
  now += ESTIMATE_QUIET_MS;                                   // 30초 동안 추정이 끊겼다 — 다시 REST
  assert.equal(spec.pauseWhen(board), false);
  const nxt = { items: [live("005930.KS", 70000, { quote_source: "nxt", session: "post" })] };
  assert.equal(boardLive(["005930.KS"]).pauseWhen(nxt), false);
});

test("정규장 칸에 추정이 오면(장이 막 닫힘) REST 로 다시 맞추자고 한다", () => {
  const spec = boardLive(["NVDA", "AAPL"]);
  const board = { items: [live("NVDA", 180), est("AAPL", 101, "2026-11-28T02:59:50Z")] };
  assert.equal(spec.received(board, estMsg("NVDA", 179, "2026-11-28T03:00:00Z")), true);
  assert.equal(spec.received(board, estMsg("AAPL", 102, "2026-11-28T03:00:00Z")), false);
  assert.equal(spec.received(board, msg("NVDA", { price: 181 }, "kis")), false);
  assert.equal(spec.received(undefined, estMsg("NVDA", 179, "2026-11-28T03:00:00Z")), false);
});

test("n초 전", () => {
  const at = "2026-11-28T03:00:00Z";
  assert.equal(agoText(at, T0 + 2_000), "방금");
  assert.equal(agoText(at, T0 + 12_000), "12초 전");
  assert.equal(agoText(at, T0 + 125_000), "2분 전");
  assert.equal(agoText(at, T0 - 5_000), "방금");             // 이 탭의 시계가 늦어도
  assert.equal(agoText(null, T0), null);
});

// #1002 저녁 추정 칸: REST 가 준 나눈 하루(정규 -10% · 추정 · 합친)를 스트림 가격으로 옮긴다
const split = {
  regular: { pct: -10, base: 50000, base_date: "2026-11-24" },
  extended: { pct: -0.2, base: 45000, base_date: "2026-11-25", session: "closed", source: "binance-estimate", at: null },
  total: { pct: -10.18, base: 50000, base_date: "2026-11-24" },
};

test("스트림 추정이 오면 나눈 하루의 장외 · 합친 등락도 새 가격으로(기준 종가 · 정규는 그대로)", () => {
  const board = { items: [est("005930.KS", 44910, "2026-11-25T11:30:00Z", { close: 45000, change_split: split })] };
  const got = applyEstimate(board, estMsg("005930.KS", 44550, "2026-11-25T11:30:10Z")).items[0];
  assert.equal(got.change_split.regular.pct, -10);
  assert.equal(got.change_split.extended.pct, -1);
  assert.equal(got.change_split.total.pct, -10.9);
  assert.equal(got.change_split.extended.at, "2026-11-25T11:30:10Z");
  assert.deepEqual(moveSplit(split, 45000, "x").extended.pct, 0);
});

test("메시지에 나눈 하루가 실려 오면 그것을 쓰고, 칸에 없으면 만들지 않는다", () => {
  const board = { items: [est("005930.KS", 44910, "2026-11-25T11:30:00Z", { close: 45000, change_split: split })] };
  const m = estMsg("005930.KS", 44550, "2026-11-25T11:30:10Z");
  const sent = { ...split, total: { ...split.total, pct: -10.9 } };
  m.payload.change_split = sent;
  assert.deepEqual(applyEstimate(board, m).items[0].change_split, sent);
  const plain = { items: [est("AAPL", 101, "2026-11-28T02:59:50Z")] };
  assert.equal(applyEstimate(plain, estMsg("AAPL", 102, "2026-11-28T03:00:00Z")).items[0].change_split, undefined);
});
