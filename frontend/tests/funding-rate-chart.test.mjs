import assert from "node:assert/strict";
import { test } from "node:test";
import { fundingChartRows, fundingDayTime, fundingLineRuns } from "../src/fundingRateChartModel.ts";
import { fundingFixture } from "./funding-rate-fixture.mjs";

test("UTC 완료일은 0과 결측을 유지하고 오늘 결제를 차트 날짜에 넣지 않는다", () => {
  const snapshot = fundingFixture("BTCUSDT", 7);
  const rows = fundingChartRows(snapshot.daily, snapshot.settlements);
  assert.equal(rows.length, 7);
  assert.equal(rows.at(-1).date, "2026-11-23");
  assert.equal(rows.at(-1).total_rate, -0.0006);
  assert.equal(rows.at(-3).total_rate, 0);
  assert.equal(rows.at(-3).mark, null);
  assert.equal(rows.at(-4).total_rate, null);
  assert.ok(!rows.some(row => row.date === snapshot.current_day.date));
});

test("오프셋이 다른 결제 시각을 UTC로 비교해 마지막 결제의 마크 가격을 고른다", () => {
  const [older, later] = [
    { settled_at: "2026-11-24T00:00:00+09:00", rate: 0, rate_type: "regular", mark_price: 90 },
    { settled_at: "2026-11-23T16:00:00Z", rate: 0, rate_type: "regular", mark_price: null },
  ];
  const day = { date: "2026-11-23", total_rate: 0, status: "complete", settlement_count: 2, average_7d: null };
  assert.equal(fundingChartRows([day], [later, older])[0].mark, null);
  assert.equal(fundingChartRows([day], [older])[0].mark, 90);
  assert.deepEqual(fundingDayTime(day.date), { year: 2026, month: 11, day: 23 });
});

test("same-time regular and special settlements preserve a reported mark", () => {
  const day = { date: "2026-11-23", total_rate: 0, status: "complete", settlement_count: 2, average_7d: null };
  const regular = { settled_at: "2026-11-23T16:00:00Z", rate: 0, rate_type: "regular", mark_price: 100 };
  const special = { ...regular, rate_type: "special", mark_price: null };
  assert.equal(fundingChartRows([day], [regular, special])[0].mark, 100);
  assert.equal(fundingChartRows([day], [special, regular])[0].mark, 100);
  assert.equal(fundingChartRows([day], [{ ...special, mark_price: 90 }, regular])[0].mark, 100);
});

test("7일 평균과 마크 선은 제공된 값만 써서 날짜 공백과 결측에서 끊긴다", () => {
  const day = (date, average_7d, mark) => ({ date, average_7d, mark, status: "complete" });
  const rows = [
    day("2026-11-17", 0, 100),
    day("2026-11-18", null, null),
    day("2026-11-19", -0.0001, 200),
    day("2026-11-21", 0.0002, 300),
    day("2026-11-22", 0.0003, 400),
  ];
  assert.deepEqual(fundingLineRuns(rows, row => row.average_7d).map(series => series.map(point => point.value)),
    [[0], [-0.0001], [0.0002, 0.0003]]);
  assert.deepEqual(fundingLineRuns(rows, row => row.mark).map(series => series.map(point => point.value)),
    [[100], [200], [300, 400]]);
});
