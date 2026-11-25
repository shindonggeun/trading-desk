import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import { test } from "node:test";

// changeSplit.ts 는 Vite 처럼 확장자 없이 import 한다("./format") — node 시험에서는 .ts 를 붙여 찾는다
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
const { splitView, tileChange } = await import("./changeSplit.ts");

// #1001 응답: 정규장 -10% 뒤 NXT -0.2%
const split = (source = "nxt", regular = { pct: -10, base: 50000, base_date: "2026-11-24" }) => ({
  regular,
  extended: { pct: -0.2, base: 45000, base_date: "2026-11-25", session: "post", source, at: "2026-11-25T08:00:00+00:00" },
  total: { pct: -10.18, base: 50000, base_date: "2026-11-24" },
});

test("정규장 -10% 뒤 NXT -0.2%: 큰 숫자는 합친 하루, 작은 줄은 정규 · NXT", () => {
  const it = { change_pct: -0.2, change_split: split() };
  const v = splitView(it);
  assert.equal(v.pct, -10.18);
  assert.equal(v.line, "정규\u00a0-10.00% · NXT\u00a0-0.20%");
  assert.equal(tileChange(it), -10.18);
  // 기준 종가는 툴팁 — 부분마다 기준이 다르다
  assert.match(v.title, /하루 -10\.18%: 전일 종가 50,000\(11-24\) 대비/);
  assert.match(v.title, /NXT -0\.20%: 오늘 KRX 종가 45,000\(11-25\) 대비/);
});

test("KRX 시간외 · 저녁 추정은 그 이름으로", () => {
  assert.equal(splitView({ change_split: split("krx-overtime") }).line, "정규\u00a0-10.00% · 시간외\u00a0-0.20%");
  assert.equal(splitView({ change_split: split("binance-estimate") }).line, "정규\u00a0-10.00% · 추정\u00a0-0.20%");
});

test("장 전 NXT(정규장 전) · 필드 없음 · 예전 응답이면 지금 표시 그대로", () => {
  const pre = { change_pct: 1, change_split: split("nxt", null) };
  assert.equal(splitView(pre), null);
  assert.equal(tileChange(pre), 1);
  assert.equal(splitView({ change_pct: 2.5 }), null);
  assert.equal(tileChange({ change_pct: 2.5 }), 2.5);
  assert.equal(tileChange({ change_pct: null }), null);
  assert.equal(splitView(undefined), null);
});

test("오른 날은 + 를, 그대로면 부호 없이", () => {
  const up = { change_split: { ...split(), regular: { pct: 3.1, base: 100, base_date: "2026-11-24" },
    extended: { ...split().extended, pct: 0 }, total: { pct: 3.1, base: 100, base_date: "2026-11-24" } } };
  assert.equal(splitView(up).line, "정규\u00a0+3.10% · NXT\u00a00.00%");
});
