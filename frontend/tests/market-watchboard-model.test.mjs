import assert from "node:assert/strict";
import { test } from "node:test";
import { watchboardCalendarPoints, watchboardChartHref, watchboardSegments } from "../src/marketWatchboard.ts";
import { watchboardFixtures } from "./market-watchboard-data.mjs";

test("날짜 축은 실제 간격을 사용하고 누락일을 값으로 채우지 않는다", () => {
  const points = watchboardCalendarPoints([
    { date: "2026-11-23", value: -2.45 },
    { date: "2026-11-19", value: 0 },
    { date: "2026-11-20", value: 10 },
    { date: "2026-02-30", value: 10 },
    { date: "2026-11-24", value: NaN },
  ]);
  assert.deepEqual(points.map(({ value }) => value), [0, 10, -2.45]);
  assert.equal(points[2].day - points[1].day, 3);
  assert.deepEqual(watchboardSegments(points).map((part) => part.length), [2, 1]);
});

test("응답이 지원하는 동일 종목 일봉만 앱 링크가 된다", () => {
  const instrument = { ticker: "^TWII", chart_href: "/#/charts?t=%5ETWII&iv=1d" };
  assert.equal(watchboardChartHref(instrument), "#/charts?t=%5ETWII&iv=1d");
  for (const chart_href of ["/#/charts?t=TSM&iv=1d", "/#/charts?t=%5ETWII&iv=1w",
    "https://example.invalid/#/charts?t=%5ETWII&iv=1d", "//example.invalid/#/charts?t=%5ETWII&iv=1d", null]) {
    assert.equal(watchboardChartHref({ ...instrument, chart_href }), null);
  }
});

test("합성 비교 수익률은 각 상품의 공통 기준일 종가를 기준으로 한다", () => {
  for (const [group, board] of Object.entries(watchboardFixtures)) {
    assert.equal(board.group, group);
    const items = new Map(board.items.map((item) => [item.ticker, item]));
    for (const series of board.comparison.series) {
      const prices = new Map(items.get(series.ticker).history.map((bar) => [bar.trade_date, bar.close]));
      const base = prices.get(board.comparison.base_date);
      assert.ok(base > 0);
      assert.deepEqual(series.points[0], { trade_date: board.comparison.base_date, return_pct: 0 });
      for (const point of series.points) {
        assert.ok(Math.abs(100 * (prices.get(point.trade_date) / base - 1) - point.return_pct) < 1e-5);
      }
    }
  }
});
