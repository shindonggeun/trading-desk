import assert from "node:assert/strict";
import { test } from "node:test";
import { krFlowLinks, opensMarketFlow, shortSaleFlowTicker } from "../src/krFlowLinks.ts";
import { krFlowHistorySegments, krFlowPercent, krFlowScatter } from "../src/krFlowBoard.ts";
import { flowFixtures } from "./kr-flow-board-data.mjs";

test("종목별 공매도 주소는 국내 상장 종목만 열고 기존 장중 알림과 구분한다", () => {
  for (const ticker of ["005930.KS", "000660.KS", "247540.KQ"]) {
    const links = krFlowLinks(ticker);
    assert.equal(shortSaleFlowTicker(links.shortSale), ticker);
    assert.equal(opensMarketFlow(links.shortSale), true);
    assert.equal(links.chart, `#/charts?t=${ticker}&iv=1d`);
    assert.equal(links.analysis, `#/?ticker=${ticker}`);
  }
  for (const hash of ["#/market", "#/?flow=intraday", "#/?flow=short&shortTicker=NVDA",
    "#/?flow=short&shortTicker=005930", "#/charts?flow=short&shortTicker=005930.KS"]) {
    assert.equal(shortSaleFlowTicker(hash), null);
  }
  assert.equal(opensMarketFlow("#/?flow=intraday"), true);
  assert.equal(opensMarketFlow("#/?flow=short&shortTicker=NVDA"), false);
});

test("합성 22거래일 및 순위·지분율 기준일은 별도이며 0과 결측을 구분한다", () => {
  const { sessions, latest_session, ranking, favorites } = flowFixtures.kospi;
  assert.equal(sessions.length, 22);
  assert.notEqual(ranking.as_of, latest_session);
  assert.equal(ranking.items.length, 50);
  assert.notEqual(ranking.items[0].latest_ownership.as_of, ranking.as_of);
  const samsung = favorites.items[0];
  assert.equal(samsung.history.length, 22);
  assert.equal(samsung.history.at(-1).participation_pct, 0);
  assert.equal(samsung.history.at(-1).buy_sell_imbalance, null);
  assert.equal(samsung.history[5].participation_pct, null);
  assert.equal(favorites.items[1].latest_ownership.ratio_pct, 0);
});

test("산점도 표본 중앙값은 두 지표가 있는 점으로만 계산한다", () => {
  const result = krFlowScatter(flowFixtures.kospi.ranking.items);
  const participation = result.points.map((point) => point.participation).toSorted((a, b) => a - b);
  assert.equal(result.points.length, 49);
  assert.equal(result.median, participation[24]);
  assert.ok(result.points.some((point) => point.item.ticker === "000660.KS" && point.imbalance === 0));
});

test("22일 이력의 결측은 선을 끊고 실제 참여율 0은 남긴다", () => {
  const { sessions, favorites } = flowFixtures.kospi;
  const segments = krFlowHistorySegments(sessions, favorites.items[0].history, "participation");
  assert.deepEqual(segments.map((part) => part.length), [5, 16]);
  assert.equal(segments.at(-1).at(-1).value, 0);
  assert.equal(segments.at(-1).at(-1).index, 21);
});

test("표시 정밀도보다 작은 값과 실제 0을 같은 값으로 표시하지 않는다", () => {
  assert.equal(krFlowPercent(0, true), "0%");
  assert.notEqual(krFlowPercent(0.004, true), "0%");
  assert.notEqual(krFlowPercent(-0.004, true), "0%");
  assert.notEqual(krFlowPercent(null), "0%");
});
