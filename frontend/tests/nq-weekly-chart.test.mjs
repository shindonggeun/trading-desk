import assert from "node:assert/strict";
import test from "node:test";
import { nqMondayLabel, nqMarkedRows } from "../src/nqWeeklyChart.ts";

test("주봉 앵커는 서머타임과 표준시 모두 뉴욕의 월요일로 비교한다", () => {
  assert.equal(nqMondayLabel("2026-11-09T05:00:00Z"), "2026-11-09");
  assert.equal(nqMondayLabel("2026-10-05T04:00:00Z"), "2026-10-05");
  assert.equal(nqMondayLabel("2026-11-09"), "2026-11-09");
  assert.equal(nqMondayLabel("2026-11-09T04:59:59Z"), null);
  assert.equal(nqMondayLabel("2026-11-09T00:00:00"), null);
  assert.equal(nqMondayLabel("2026-11-08"), null);
});

test("없는 주를 인접한 봉이나 진행 주로 대체하지 않는다", () => {
  const bars = [{ at: "2026-11-02T05:00:00Z" }, { at: "2026-11-16T05:00:00Z" }, { at: "2026-11-23T05:00:00Z" }];
  const rows = nqMarkedRows(bars, ["2026-11-09", "2026-11-16"], bar => bar.at);
  assert.deepEqual([...rows], [["2026-11-16", bars[1]]]);
});
