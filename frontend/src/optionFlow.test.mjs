import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import { test } from "node:test";

// optionFlow.ts 는 Vite 처럼 확장자 없이 import 한다("./format") — node 시험에서는 .ts 를 붙여 찾는다
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
const { optionsAsOf, putCallLine, PUT_CALL_TIP } = await import("./optionFlow.ts");

test("외국인 풋 − 콜: 금액과 더 많은 쪽을 단정하지 않는 말로", () => {
  const put = putCallLine(900);                       // 백만 원 → 9억
  assert.equal(put.value, "+9억");
  assert.equal(put.lean, "풋 쪽 순매수가 더 많아요(하락 대비일 수 있음)");
  const call = putCallLine(-25_000);
  assert.equal(call.value, "-250억");
  assert.match(call.lean, /콜 쪽/);
  assert.equal(putCallLine(40).lean, "콜 · 풋이 비슷합니다");   // 1억 원 미만
  assert.equal(putCallLine(null), null);
  assert.match(PUT_CALL_TIP, /팔아도 순매수가 음수/);
});

test("기준 시각: 이번 응답의 값인지, 실패로 마지막 정상값인지", () => {
  const options = { as_of: "2026-11-26T10:30:05+09:00" };
  assert.deepEqual(optionsAsOf({ fetched_at: "2026-11-26T10:30:05+09:00", options, options_error: null }),
    { text: "10:30 기준", stale: false });
  assert.deepEqual(optionsAsOf({ fetched_at: "2026-11-26T10:31:05+09:00", options, options_error: "x" }),
    { text: "10:30 마지막 정상값", stale: true });
  assert.equal(optionsAsOf({ fetched_at: "2026-11-26T10:31:05+09:00", options: null, options_error: null }), null);
});
