import assert from "node:assert/strict";
import { test } from "node:test";
import { beginnerGuide } from "./chartBeginner.ts";

const band = (low, high, role) => ({ low, high, role, score: 1, basis: ["과거 거래"], distance_pct: 0 });
const view = (below, above) => ({ view: "short", label: "단기", timeframe: "60m", below, above });

test("저항 시작까지 남은 비율은 현재가에서 계산한다", () => {
  const guide = beginnerGuide({ timeframe: "60m", price: 134.6 }, view(
    [band(128, 131, "accumulation")], [{ ...band(148.3, 151.07, "supply"), basis: ["매물대"] }],
  ));
  assert.match(guide.resistance, /148\.3~151\.07/);
  assert.match(guide.resistance, /약 10\.2%/);
  assert.match(guide.resistance, /예전에 거래가 많아/);
  assert.match(guide.entry, /128~131/);
  assert.match(guide.entry, /128 아래로 다시 내려가면/);
});

test("저항 안에서는 남은 비율을 말하지 않는다", () => {
  const guide = beginnerGuide({ timeframe: "60m", price: 150 }, view([], [band(148.3, 151.07, "supply")]));
  assert.match(guide.resistance, /이미 이 구간에 들어왔어요/);
  assert.doesNotMatch(guide.resistance, /남았어요/);
  assert.doesNotMatch(guide.resistance, /예전에 거래가 많아/);
  assert.match(guide.entry, /151\.07 위로 올라선 뒤/);
});

test("후보와 현재가가 없으면 근거 없는 진입 가격을 만들지 않는다", () => {
  const noBands = beginnerGuide({ timeframe: "1d", price: 100 }, view([], []));
  assert.match(noBands.entry, /진입 가격이 뚜렷하지 않아요/);
  const noPrice = beginnerGuide({ timeframe: "1d", price: null }, view([], [band(110, 112, "supply")]));
  assert.match(noPrice.now, /확인할 수 없어요/);
  assert.match(noPrice.entry, /진입 가격이 뚜렷하지 않아요/);
});
